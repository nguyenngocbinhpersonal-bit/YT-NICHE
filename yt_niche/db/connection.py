"""SQLite connection and schema."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from yt_niche.config import DB_PATH, MARKETS

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    key        TEXT PRIMARY KEY,
    namespace  TEXT NOT NULL,
    response   TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_log (
    id         INTEGER PRIMARY KEY,
    day        TEXT NOT NULL,      -- day in Pacific time (the quota reset day)
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
    affiliates       TEXT NOT NULL DEFAULT '[]',  -- JSON: list of affiliate products
    checked_channels TEXT NOT NULL DEFAULT '[]',  -- JSON: small channels opened and checked, original content
    shortlisted      INTEGER NOT NULL DEFAULT 0,
    raw_json         TEXT NOT NULL,               -- raw data from fetch_niche
    result_json      TEXT NOT NULL                -- output of analyze()
);
CREATE INDEX IF NOT EXISTS ix_dig_run_setup ON dig_run(topic, region, window_days, fmt);

CREATE TABLE IF NOT EXISTS scorecard (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    parent_id    INTEGER,                       -- previous version (when weights change after locking)
    weights_json TEXT NOT NULL,
    locked_at    TEXT,                          -- when the weights were locked; NULL = not locked yet
    promise      TEXT NOT NULL DEFAULT '',      -- one-sentence channel promise
    backup_close INTEGER NOT NULL DEFAULT 0,    -- backup shares audience / style with the main niche
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
    score        INTEGER,                       -- 1–5, NULL = not scored yet
    auto_score   INTEGER,                       -- the app's suggestion
    rationale    TEXT NOT NULL DEFAULT '',      -- rationale of the suggestion (from the dig numbers)
    note         TEXT NOT NULL DEFAULT '',      -- rationale written by the scorer
    PRIMARY KEY (scorecard_id, run_id, criterion)
);

CREATE TABLE IF NOT EXISTS test_plan (
    scorecard_id  INTEGER PRIMARY KEY REFERENCES scorecard(id) ON DELETE CASCADE,
    run_id        INTEGER NOT NULL,                -- main niche when the plan was made
    pillars_json  TEXT NOT NULL,                   -- 3 content pillars
    start_date    TEXT NOT NULL,
    per_week      INTEGER NOT NULL,
    fixes_applied INTEGER NOT NULL DEFAULT 0,      -- titles / thumbnails / hooks fixed at the video 5 checkpoint
    out_of_ideas  INTEGER NOT NULL DEFAULT 0,
    drop_flags    TEXT NOT NULL DEFAULT '[]',      -- ticked drop signals
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
    -- Numbers after 7 days (YouTube Studio)
    impressions  INTEGER,
    ctr          REAL,                             -- %
    views        INTEGER,
    avd_seconds  INTEGER,                          -- average view duration
    retention    REAL,                             -- % of the video watched
    new_subs     INTEGER,
    asks_more    INTEGER NOT NULL DEFAULT 0,       -- comments ask for more on this niche
    note         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (scorecard_id, idx)
);

-- Lesson 2.3: the "viable sub-niche" check sheet (the hand-written part).
CREATE TABLE IF NOT EXISTS niche_check (
    run_id     INTEGER PRIMARY KEY REFERENCES dig_run(id) ON DELETE CASCADE,
    ideas      TEXT NOT NULL DEFAULT '',          -- one video idea per line
    promise    TEXT NOT NULL DEFAULT '',          -- one-sentence channel promise
    updated_at TEXT NOT NULL
);

-- Lesson 2.3: keywords (YouTube search suggestions) and their scores. One keyword can be a suggestion of several seeds;
-- score, picked flag and dig run are updated by (region, keyword) for every seed.
CREATE TABLE IF NOT EXISTS keyword (
    region       TEXT NOT NULL,
    keyword      TEXT NOT NULL,
    seed         TEXT NOT NULL,                   -- the sub-niche the suggestions were fetched for
    language     TEXT NOT NULL,
    source       TEXT NOT NULL,                   -- suggest | az | manual
    rank         INTEGER,                         -- position in the suggestion list
    created_at   TEXT NOT NULL,
    scored_at    TEXT,
    score        REAL,
    demand       REAL,
    competition  REAL,
    n            INTEGER,
    low_data     INTEGER,
    metrics_json TEXT,
    picked       INTEGER NOT NULL DEFAULT 0,
    run_id       INTEGER REFERENCES dig_run(id) ON DELETE SET NULL,  -- the dig of this keyword
    PRIMARY KEY (region, seed, keyword)
);
CREATE INDEX IF NOT EXISTS ix_keyword_kw ON keyword(region, keyword);

-- Lesson 2.4: one multi-market dig.
CREATE TABLE IF NOT EXISTS market_batch (
    id          INTEGER PRIMARY KEY,
    topic       TEXT NOT NULL,                    -- the original keyword
    category    TEXT,
    window_days INTEGER NOT NULL,
    fmt         TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_batch_item (
    batch_id INTEGER NOT NULL REFERENCES market_batch(id) ON DELETE CASCADE,
    region   TEXT NOT NULL,
    keyword  TEXT NOT NULL,                       -- the keyword translated for this market
    run_id   INTEGER REFERENCES dig_run(id) ON DELETE SET NULL,
    error    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, region)
);

-- The app's single list of markets (every feature reads from here). Regions / languages follow Google's lists.
CREATE TABLE IF NOT EXISTS market (
    region     TEXT PRIMARY KEY,                  -- YouTube region code (gl), e.g. VN
    name       TEXT NOT NULL,
    hl         TEXT NOT NULL,                     -- Google language code (i18nLanguages), e.g. vi, zh-TW, es-419
    group_name TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Legacy: user-added markets (merged into the market table, kept to migrate data).
CREATE TABLE IF NOT EXISTS custom_market (
    region   TEXT PRIMARY KEY,                    -- YouTube region code (gl), e.g. ES
    language TEXT NOT NULL,                       -- relevanceLanguage, e.g. es
    name     TEXT NOT NULL,
    added_at TEXT NOT NULL
);

-- Keyword radar: scans Google Trends categories spread over the day.
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
    value         INTEGER NOT NULL DEFAULT 0,     -- % growth (rising) or level 0–100 (top)
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    seen_days     INTEGER NOT NULL DEFAULT 1,     -- number of distinct days it kept appearing
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

-- Radar pace log (one row per tick that did work) to tune the pace against Google's real block threshold.
CREATE TABLE IF NOT EXISTS radar_pace_log (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL,
    action          TEXT NOT NULL,
    requests        INTEGER NOT NULL DEFAULT 0,  -- Google Trends requests in this tick
    throttled       INTEGER NOT NULL DEFAULT 0,  -- 1 = blocked by Google (429) in this tick
    interval_before REAL NOT NULL,               -- pace (seconds/tick) before the tick
    interval_after  REAL NOT NULL,               -- pace after self-adjusting
    streak          INTEGER NOT NULL DEFAULT 0,  -- ticks in a row without a block
    gap_s           REAL,                        -- real seconds since the previous tick with requests
    req_1h          INTEGER NOT NULL DEFAULT 0,  -- total requests in the 60 minutes up to this tick
    forced          INTEGER NOT NULL DEFAULT 0   -- 1 = "Run 1 tick now" was clicked
);
CREATE INDEX IF NOT EXISTS ix_radar_pace_log_at ON radar_pace_log(at);

-- One "Similar videos & channels" lookup (services/similar.py).
CREATE TABLE IF NOT EXISTS similar_run (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    input       TEXT NOT NULL,     -- the URL the user pasted
    kind        TEXT NOT NULL,     -- video | channel
    region      TEXT NOT NULL,
    label       TEXT NOT NULL,     -- source video title or channel name
    n_channels  INTEGER NOT NULL,
    n_videos    INTEGER NOT NULL,
    result_json TEXT NOT NULL
);

-- Niche map (docs/NICHE_MAP_MCP_PLAN.md): the niche tree Claude builds through MCP, numbers come from the older tables.
CREATE TABLE IF NOT EXISTS niche (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    language             TEXT NOT NULL DEFAULT '',       -- one niche = one language (relevanceLanguage code: vi, en, ja…)
    parent_id            INTEGER REFERENCES niche(id) ON DELETE SET NULL,
    motivation           TEXT,                           -- main motivation (services/niche_map.MOTIVATIONS)
    motivation_secondary TEXT,
    status               TEXT NOT NULL DEFAULT 'draft',  -- draft | active | merged | dropped
    merged_into          INTEGER REFERENCES niche(id) ON DELETE SET NULL,
    potential            REAL,                           -- 0–100, ranked by Claude after reading real numbers
    potential_rationale  TEXT NOT NULL DEFAULT '',
    basis_json           TEXT,                           -- snapshot of the numbers at ranking time (to tell a stale rank)
    ranked_at            TEXT,
    summary              TEXT NOT NULL DEFAULT '',       -- 2–3 sentences: audience, problem, angle
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
    demand      REAL,                                    -- copied from keyword / radar_keyword when attached
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

-- Decision log: every change by Claude or the user carries a rationale and can be read back.
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

# Columns added after the DB already held data (CREATE TABLE IF NOT EXISTS does not add columns).
MIGRATIONS = {
    "dig_run": {
        "parent_id": "INTEGER",  # lesson 2.3: parent niche of a sub-niche
        "layer": "INTEGER NOT NULL DEFAULT 1",
        "filter_type": "TEXT NOT NULL DEFAULT ''",  # the filter added at this layer
        # lesson 2.2: notes on small booming channels that were opened, JSON {channel_id: {"note": "...", "reup": bool}}
        "channel_notes": "TEXT NOT NULL DEFAULT '{}'",
    },
    "radar_source": {"scans": "INTEGER NOT NULL DEFAULT 0"},  # number of scans of this pair
    # number of 429s in a tick that passed on retry with a fresh cookie
    "radar_pace_log": {"rescued": "INTEGER NOT NULL DEFAULT 0"},
    # AI keyword classification (services/ai.KEYWORD_TYPES) and the Vietnamese meaning
    "radar_keyword": {"ai_type": "TEXT", "ai_vi": "TEXT"},
    "niche": {"language": "TEXT NOT NULL DEFAULT ''"},  # niche map: one niche = one language
}

# ---------------------------------------------------------------------------
# One-time migration of stored Vietnamese values to the English constants (docs/GLOSSARY_EN.md §3).
# ---------------------------------------------------------------------------

LANG_MIGRATION_FLAG = "lang_migrated_en"

LABELS_VI = {"Ngách xanh": "Green niche", "Ngách tiềm năng": "Promising niche", "Ngách đỏ": "Red niche", "Mẫu còn ít": "Small sample"}
CONFIDENCE_VI = {"cao": "high", "vừa": "medium", "thấp": "low"}  # also rpm_tier (same words, same mapping)
TIER_LEVELS_VI = {"Mạnh": "Strong", "Vừa": "Medium", "Yếu": "Weak"}
VERDICT_VI = {"THỬ": "TRY", "THỬ SAU KHI SỬA": "TRY AFTER FIXING", "BỎ": "DROP"}
TREND_CLASS_VI = {"bền": "evergreen", "trào lưu": "fad", "đang rơi": "declining", "theo mùa": "seasonal"}
TREND_DIRECTION_VI = {"đi lên": "up", "đi ngang": "flat", "đi xuống": "down"}
OUTLIER_LEVEL_VI = {"Vượt trội": "Outperformer", "Nổ view": "Viral", "Siêu viral": "Super viral"}
FIT_LABEL_VI = {"Ngách hợp video dài": "Fits long-form", "Ngách hợp Shorts": "Fits Shorts"}
CATEGORY_VI = {
    "Giải trí / kể chuyện": "Entertainment / storytelling",
    "Lịch sử": "History",
    "Giáo dục": "Education",
    "Tài chính / kinh doanh": "Finance / business",
    "Công nghệ": "Technology",
    "Du lịch": "Travel",
    "Game": "Gaming",
    "Nấu ăn": "Cooking",
    "Sức khỏe": "Health",
    "Thiếu nhi": "Kids",
    "Tâm linh / tôn giáo": "Spirituality / religion",
    "Tâm lý / phát triển bản thân": "Psychology / self-improvement",
    "Âm nhạc / thư giãn": "Music / relaxation",
}
MARKET_NAME_VI = {
    "US": "Mỹ", "GB": "Anh", "AU": "Úc", "DE": "Đức", "FR": "Pháp", "JP": "Nhật Bản", "KR": "Hàn Quốc",
    "TW": "Đài Loan", "VN": "Việt Nam", "TH": "Thái Lan", "IN": "Ấn Độ",
}
MARKET_GROUP_VI = {
    "Tier 1 (RPM cao)": "Tier 1 (high RPM)",
    "Châu Á": "East Asia",
    "Đông Nam Á": "Southeast Asia",
    "Mỹ Latin & Ấn Độ": "Latin America & India",
    "Khác": "Other",
}
# Every string leaf of dig_run.result_json that equals one of these keys is replaced (exact match only).
RESULT_JSON_VI = {
    **LABELS_VI, **CONFIDENCE_VI, **TIER_LEVELS_VI, **VERDICT_VI, **TREND_CLASS_VI, **TREND_DIRECTION_VI,
    **OUTLIER_LEVEL_VI, **FIT_LABEL_VI,
}


def _translate_leaves(value, mapping: dict[str, str]):
    """Return a copy of a JSON value with every string leaf found in `mapping` replaced. Keys are left alone."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_translate_leaves(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _translate_leaves(v, mapping) for k, v in value.items()}
    return value


def _update_values(conn: sqlite3.Connection, table: str, column: str, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old))


def _migrate_language(conn: sqlite3.Connection) -> None:
    """Map stored Vietnamese values to the English constants, once (flag in app_meta). Unknown values are left alone."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (LANG_MIGRATION_FLAG,)).fetchone()
    if row is not None:
        return
    _update_values(conn, "dig_run", "label", LABELS_VI)
    _update_values(conn, "dig_run", "confidence", CONFIDENCE_VI)
    _update_values(conn, "dig_run", "verdict", VERDICT_VI)
    _update_values(conn, "dig_run", "category", CATEGORY_VI)
    _update_values(conn, "market_batch", "category", CATEGORY_VI)
    _update_values(conn, "radar_keyword", "trend_class", TREND_CLASS_VI)
    _update_values(conn, "niche_keyword", "trend_class", TREND_CLASS_VI)
    _update_values(conn, "market", "group_name", MARKET_GROUP_VI)
    for region, old_name in MARKET_NAME_VI.items():
        if region in MARKETS:
            conn.execute("UPDATE market SET name = ? WHERE region = ? AND name = ?", (MARKETS[region][1], region, old_name))
    for rid, text in conn.execute("SELECT id, result_json FROM dig_run").fetchall():
        try:
            result = json.loads(text)
        except ValueError:
            continue
        translated = _translate_leaves(result, RESULT_JSON_VI)
        if translated != result:
            conn.execute("UPDATE dig_run SET result_json = ? WHERE id = ?", (json.dumps(translated, ensure_ascii=False), rid))
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, '1')", (LANG_MIGRATION_FLAG,))


def _migrate(conn: sqlite3.Connection) -> None:
    # The first keyword table had the key (region, keyword): it held only suggestions (free to refetch), so recreate it.
    pk = [r["name"] for r in sorted(conn.execute("PRAGMA table_info(keyword)"), key=lambda r: r["pk"]) if r["pk"]]
    if pk == ["region", "keyword"]:
        conn.execute("DROP TABLE keyword")
        conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _migrate_language(conn)


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the DB (create it if missing). Autocommit; use ':memory:' for tests."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit may rerun the script on another thread within the same session.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # The MCP server (Claude) and Streamlit write at the same time: wait instead of failing with "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
