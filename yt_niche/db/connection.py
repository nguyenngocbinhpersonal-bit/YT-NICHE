"""Kết nối SQLite và schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from yt_niche.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    key        TEXT PRIMARY KEY,
    namespace  TEXT NOT NULL,
    response   TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_log (
    id         INTEGER PRIMARY KEY,
    day        TEXT NOT NULL,      -- ngày theo giờ Thái Bình Dương (ngày reset quota)
    endpoint   TEXT NOT NULL,
    units      INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quota_log_day ON quota_log(day);

CREATE TABLE IF NOT EXISTS channel (
    channel_id       TEXT PRIMARY KEY,
    title            TEXT,
    subs             INTEGER,
    subs_hidden      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT,
    video_count      INTEGER,
    uploads_playlist TEXT,
    median_long      REAL,
    n_long           INTEGER NOT NULL DEFAULT 0,
    median_short     REAL,
    n_short          INTEGER NOT NULL DEFAULT 0,
    median_fetched_at REAL
);

CREATE TABLE IF NOT EXISTS dig_run (
    id               INTEGER PRIMARY KEY,
    topic            TEXT NOT NULL,
    region           TEXT NOT NULL,
    language         TEXT NOT NULL,
    window_days      INTEGER NOT NULL,
    fmt              TEXT NOT NULL,
    category         TEXT,
    created_at       TEXT NOT NULL,
    score            REAL,
    label            TEXT,
    confidence       TEXT,
    verdict          TEXT,
    affiliates       TEXT NOT NULL DEFAULT '[]',  -- JSON: danh sách sản phẩm affiliate
    checked_channels TEXT NOT NULL DEFAULT '[]',  -- JSON: kênh nhỏ đã mở xem, tự làm nội dung
    shortlisted      INTEGER NOT NULL DEFAULT 0,
    raw_json         TEXT NOT NULL,               -- dữ liệu thô của fetch_niche
    result_json      TEXT NOT NULL                -- kết quả analyze()
);
CREATE INDEX IF NOT EXISTS ix_dig_run_setup ON dig_run(topic, region, window_days, fmt);

CREATE TABLE IF NOT EXISTS scorecard (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    parent_id    INTEGER,                       -- phiên bản trước (khi đổi trọng số sau khi khoá)
    weights_json TEXT NOT NULL,
    locked_at    TEXT,                          -- thời điểm khoá trọng số; NULL = chưa khoá
    promise      TEXT NOT NULL DEFAULT '',      -- lời hứa kênh một câu
    backup_close INTEGER NOT NULL DEFAULT 0,    -- dự phòng gần khán giả / phong cách với ngách chính
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scorecard_item (
    scorecard_id INTEGER NOT NULL REFERENCES scorecard(id) ON DELETE CASCADE,
    run_id       INTEGER NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (scorecard_id, run_id)
);

CREATE TABLE IF NOT EXISTS scorecard_cell (
    scorecard_id INTEGER NOT NULL REFERENCES scorecard(id) ON DELETE CASCADE,
    run_id       INTEGER NOT NULL,
    criterion    TEXT NOT NULL,
    score        INTEGER,                       -- 1–5, NULL = chưa chấm
    auto_score   INTEGER,                       -- gợi ý của app
    rationale    TEXT NOT NULL DEFAULT '',      -- căn cứ gợi ý (từ số liệu đào)
    note         TEXT NOT NULL DEFAULT '',      -- căn cứ người chấm ghi
    PRIMARY KEY (scorecard_id, run_id, criterion)
);

CREATE TABLE IF NOT EXISTS test_plan (
    scorecard_id  INTEGER PRIMARY KEY REFERENCES scorecard(id) ON DELETE CASCADE,
    run_id        INTEGER NOT NULL,                -- ngách chính lúc lập kế hoạch
    pillars_json  TEXT NOT NULL,                   -- 3 trụ nội dung
    start_date    TEXT NOT NULL,
    per_week      INTEGER NOT NULL,
    fixes_applied INTEGER NOT NULL DEFAULT 0,      -- đã chỉnh tiêu đề / ảnh bìa / hook ở mốc video 5
    out_of_ideas  INTEGER NOT NULL DEFAULT 0,
    drop_flags    TEXT NOT NULL DEFAULT '[]',      -- tín hiệu nên bỏ ngách đã tick
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_video (
    scorecard_id INTEGER NOT NULL REFERENCES test_plan(scorecard_id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,                 -- 1–10
    pillar       TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    hook         TEXT NOT NULL DEFAULT '',
    thumbnail    TEXT NOT NULL DEFAULT '',
    planned_date TEXT,
    published_at TEXT,
    -- Số đo sau 7 ngày (YouTube Studio)
    impressions  INTEGER,
    ctr          REAL,                             -- %
    views        INTEGER,
    avd_seconds  INTEGER,                          -- thời lượng xem trung bình
    retention    REAL,                             -- % thời lượng video được xem
    new_subs     INTEGER,
    asks_more    INTEGER NOT NULL DEFAULT 0,       -- có bình luận hỏi thêm chủ đề cùng ngách
    note         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (scorecard_id, idx)
);

-- Bài 2.3: phiếu kiểm tra ngách con "đủ sống" (phần nhập tay).
CREATE TABLE IF NOT EXISTS niche_check (
    run_id     INTEGER PRIMARY KEY REFERENCES dig_run(id) ON DELETE CASCADE,
    ideas      TEXT NOT NULL DEFAULT '',          -- mỗi dòng một ý tưởng video
    promise    TEXT NOT NULL DEFAULT '',          -- lời hứa kênh một câu
    updated_at TEXT NOT NULL
);

-- Bài 2.3: từ khoá (gợi ý tìm kiếm YouTube) và điểm chấm. Một từ khoá có thể là gợi ý của nhiều từ gốc;
-- điểm, cột chọn và lần đào được cập nhật theo (region, keyword) cho mọi từ gốc.
CREATE TABLE IF NOT EXISTS keyword (
    region       TEXT NOT NULL,
    keyword      TEXT NOT NULL,
    seed         TEXT NOT NULL,                   -- ngách con dùng để lấy gợi ý
    language     TEXT NOT NULL,
    source       TEXT NOT NULL,                   -- suggest | az | manual
    rank         INTEGER,                         -- thứ hạng trong danh sách gợi ý
    created_at   TEXT NOT NULL,
    scored_at    TEXT,
    score        REAL,
    demand       REAL,
    competition  REAL,
    n            INTEGER,
    low_data     INTEGER,
    metrics_json TEXT,
    picked       INTEGER NOT NULL DEFAULT 0,
    run_id       INTEGER REFERENCES dig_run(id) ON DELETE SET NULL,  -- lần đào từ khoá này
    PRIMARY KEY (region, seed, keyword)
);
CREATE INDEX IF NOT EXISTS ix_keyword_kw ON keyword(region, keyword);

-- Bài 2.4: một lần đào đa thị trường.
CREATE TABLE IF NOT EXISTS market_batch (
    id          INTEGER PRIMARY KEY,
    topic       TEXT NOT NULL,                    -- từ khoá gốc (tiếng Việt)
    category    TEXT,
    window_days INTEGER NOT NULL,
    fmt         TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_batch_item (
    batch_id INTEGER NOT NULL REFERENCES market_batch(id) ON DELETE CASCADE,
    region   TEXT NOT NULL,
    keyword  TEXT NOT NULL,                       -- từ khoá đã dịch cho thị trường này
    run_id   INTEGER REFERENCES dig_run(id) ON DELETE SET NULL,
    error    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, region)
);

-- Danh sách thị trường duy nhất của app (mọi tính năng đọc từ đây). Khu vực / ngôn ngữ theo danh sách của Google.
CREATE TABLE IF NOT EXISTS market (
    region     TEXT PRIMARY KEY,                  -- mã khu vực YouTube (gl), vd. VN
    name       TEXT NOT NULL,
    hl         TEXT NOT NULL,                     -- mã ngôn ngữ của Google (i18nLanguages), vd. vi, zh-TW, es-419
    group_name TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Bản cũ: thị trường tự thêm (đã gộp vào bảng market, giữ để chuyển dữ liệu).
CREATE TABLE IF NOT EXISTS custom_market (
    region   TEXT PRIMARY KEY,                    -- mã khu vực YouTube (gl), vd. ES
    language TEXT NOT NULL,                       -- relevanceLanguage, vd. es
    name     TEXT NOT NULL,
    added_at TEXT NOT NULL
);

-- Radar từ khoá: quét danh mục Google Trends rải đều trong ngày.
CREATE TABLE IF NOT EXISTS radar_source (
    region      TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    scanned_at  TEXT,
    n_found     INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (region, category_id)
);

CREATE TABLE IF NOT EXISTS radar_keyword (
    region        TEXT NOT NULL,
    keyword       TEXT NOT NULL,
    category_id   INTEGER NOT NULL,
    kind          TEXT NOT NULL,                  -- rising | top
    value         INTEGER NOT NULL DEFAULT 0,     -- % tăng (rising) hoặc mức 0–100 (top)
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    seen_days     INTEGER NOT NULL DEFAULT 1,     -- số ngày khác nhau còn xuất hiện
    status        TEXT NOT NULL DEFAULT 'pending',-- pending | passed | excluded | ignored | watching
    reason        TEXT NOT NULL DEFAULT '',
    entity_type   TEXT,
    trend_class   TEXT,
    trend_json    TEXT,
    checked_at    TEXT,
    yt_score      REAL,
    yt_json       TEXT,
    yt_scored_at  TEXT,
    potential     REAL,
    PRIMARY KEY (region, keyword)
);
CREATE INDEX IF NOT EXISTS ix_radar_keyword_status ON radar_keyword(status, potential);

CREATE TABLE IF NOT EXISTS radar_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Nhật ký nhịp quét Radar (mỗi lượt có làm việc một dòng) để hiệu chỉnh nhịp theo ngưỡng chặn thật của Google.
CREATE TABLE IF NOT EXISTS radar_pace_log (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL,
    action          TEXT NOT NULL,
    requests        INTEGER NOT NULL DEFAULT 0,  -- số yêu cầu Google Trends trong lượt
    throttled       INTEGER NOT NULL DEFAULT 0,  -- 1 = bị Google chặn (429) ở lượt này
    interval_before REAL NOT NULL,               -- nhịp (giây/lượt) trước lượt
    interval_after  REAL NOT NULL,               -- nhịp sau khi tự điều chỉnh
    streak          INTEGER NOT NULL DEFAULT 0,  -- số lượt liền không bị chặn
    gap_s           REAL,                        -- giây thật kể từ lượt có yêu cầu trước đó
    req_1h          INTEGER NOT NULL DEFAULT 0,  -- tổng yêu cầu trong 60 phút tính đến lượt này
    forced          INTEGER NOT NULL DEFAULT 0   -- 1 = bấm "Chạy 1 lượt ngay"
);
CREATE INDEX IF NOT EXISTS ix_radar_pace_log_at ON radar_pace_log(at);

-- Một lần tra "Video & kênh tương tự" (services/similar.py).
CREATE TABLE IF NOT EXISTS similar_run (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    input       TEXT NOT NULL,     -- URL người dùng dán
    kind        TEXT NOT NULL,     -- video | channel
    region      TEXT NOT NULL,
    label       TEXT NOT NULL,     -- tiêu đề video hoặc tên kênh gốc
    n_channels  INTEGER NOT NULL,
    n_videos    INTEGER NOT NULL,
    result_json TEXT NOT NULL
);

-- Bản đồ ngách (docs/NICHE_MAP_MCP_PLAN.md): cây ngách do Claude dựng qua MCP, số liệu lấy từ các bảng cũ.
CREATE TABLE IF NOT EXISTS niche (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    language             TEXT NOT NULL DEFAULT '',       -- một ngách = một ngôn ngữ (mã relevanceLanguage: vi, en, ja…)
    parent_id            INTEGER REFERENCES niche(id) ON DELETE SET NULL,
    motivation           TEXT,                           -- động cơ chính (services/niche_map.MOTIVATIONS)
    motivation_secondary TEXT,
    status               TEXT NOT NULL DEFAULT 'draft',  -- draft | active | merged | dropped
    merged_into          INTEGER REFERENCES niche(id) ON DELETE SET NULL,
    potential            REAL,                           -- 0–100, Claude xếp sau khi đọc số thật
    potential_rationale  TEXT NOT NULL DEFAULT '',
    basis_json           TEXT,                           -- ảnh chụp số liệu lúc xếp hạng (để biết hạng đã cũ)
    ranked_at            TEXT,
    summary              TEXT NOT NULL DEFAULT '',       -- 2–3 câu: khán giả, vấn đề, góc kể
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_niche_parent ON niche(parent_id);

CREATE TABLE IF NOT EXISTS niche_keyword (
    niche_id    INTEGER NOT NULL REFERENCES niche(id) ON DELETE CASCADE,
    region      TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    kind        TEXT NOT NULL,                           -- main | related | longtail | trend
    source      TEXT NOT NULL DEFAULT 'claude',
    demand      REAL,                                    -- copy từ keyword / radar_keyword lúc gắn
    competition REAL,
    score       REAL,
    n           INTEGER,
    scored_at   TEXT,
    trend_class TEXT,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (niche_id, region, keyword)
);

CREATE TABLE IF NOT EXISTS niche_run (
    niche_id INTEGER NOT NULL REFERENCES niche(id) ON DELETE CASCADE,
    run_id   INTEGER NOT NULL REFERENCES dig_run(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL,
    PRIMARY KEY (niche_id, run_id)
);

CREATE TABLE IF NOT EXISTS niche_channel (
    niche_id   INTEGER NOT NULL REFERENCES niche(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'leader',           -- leader | rising | fallen
    note       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (niche_id, channel_id)
);

-- Nhật ký quyết định: mọi thay đổi do Claude hay người dùng đều có lý do, đọc lại được.
CREATE TABLE IF NOT EXISTS niche_event (
    id           INTEGER PRIMARY KEY,
    niche_id     INTEGER REFERENCES niche(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                          -- create | rename | move | merge | unmerge | motivation | rank | keywords | run | channels | drop
    payload_json TEXT NOT NULL DEFAULT '{}',
    rationale    TEXT NOT NULL DEFAULT '',
    actor        TEXT NOT NULL DEFAULT 'claude',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_niche_event_niche ON niche_event(niche_id, id);
"""

# Cột thêm sau khi DB đã có dữ liệu (CREATE TABLE IF NOT EXISTS không tự thêm cột).
MIGRATIONS = {
    "dig_run": {
        "parent_id": "INTEGER",  # bài 2.3: ngách mẹ của ngách con
        "layer": "INTEGER NOT NULL DEFAULT 1",
        "filter_type": "TEXT NOT NULL DEFAULT ''",  # bộ lọc thêm ở tầng này
        # bài 2.2: nhận xét kênh nhỏ nổ đã mở xem, JSON {channel_id: {"note": "...", "reup": bool}}
        "channel_notes": "TEXT NOT NULL DEFAULT '{}'",
    },
    "radar_source": {"scans": "INTEGER NOT NULL DEFAULT 0"},
    # số lần Google trả 429 trong lượt nhưng thử lại bằng cookie mới thì qua
    "radar_pace_log": {"rescued": "INTEGER NOT NULL DEFAULT 0"},
    # AI phân loại từ khoá (services/ai.KEYWORD_TYPES) và nghĩa tiếng Việt
    "radar_keyword": {"ai_type": "TEXT", "ai_vi": "TEXT"},  # số lần đã quét cặp này
    "niche": {"language": "TEXT NOT NULL DEFAULT ''"},  # bản đồ ngách: một ngách = một ngôn ngữ
}


def _migrate(conn: sqlite3.Connection) -> None:
    # Bảng keyword bản đầu có khoá (region, keyword): chỉ chứa gợi ý (lấy lại được miễn phí) nên tạo lại.
    pk = [r["name"] for r in sorted(conn.execute("PRAGMA table_info(keyword)"), key=lambda r: r["pk"]) if r["pk"]]
    if pk == ["region", "keyword"]:
        conn.execute("DROP TABLE keyword")
        conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Mở DB (tạo nếu chưa có). Autocommit; dùng ':memory:' cho test."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit có thể chạy lại script ở thread khác trong cùng phiên.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # MCP server (Claude) và Streamlit ghi cùng lúc: chờ thay vì báo "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
