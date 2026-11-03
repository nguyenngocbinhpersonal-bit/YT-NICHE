"""Theo dõi sau khi chốt: nhắc đào lại mỗi tháng (bài 2.5) và checklist tổng kết của chặng Chọn ngách."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from yt_niche import config as cfg
from yt_niche.core.reading import seven_cells
from yt_niche.core.scorecard import setup_warnings
from yt_niche.core.stats import parse_iso
from yt_niche.db import runs as run_repo
from yt_niche.services import keywords as kw_svc
from yt_niche.services import scorecards

MAIN, BACKUP, SHORTLIST = "ngách chính", "dự phòng", "trong bảng chấm"


def _age_days(run: dict, now: datetime) -> int:
    return (now - parse_iso(run["created_at"])).days


def latest_board(conn: sqlite3.Connection) -> dict | None:
    """Bảng chấm mới nhất đã khoá và chọn được ngách chính."""
    for b in scorecards.list_all(conn):
        if b["locked_at"]:
            data = scorecards.load(conn, b["id"])
            if data["decision"] and data["decision"]["main"]:
                return data
    return None


def tracked(conn: sqlite3.Connection) -> list[dict]:
    """Ngách cần theo dõi: chính / dự phòng của các bảng chấm đã chốt, và ngách đã đưa vào bảng chấm.

    Mỗi thiết lập (chủ đề, thị trường, khoảng thời gian, định dạng) chỉ lấy lần đào mới nhất.
    """
    roles: dict[int, str] = {}
    for b in scorecards.list_all(conn):
        if not b["locked_at"]:
            continue
        d = scorecards.load(conn, b["id"])["decision"] or {}
        for role, item in ((MAIN, d.get("main")), (BACKUP, d.get("backup"))):
            if item and roles.get(item["id"]) != MAIN:
                roles[item["id"]] = role
    for r in run_repo.list_all(conn):
        if r["shortlisted"]:
            roles.setdefault(r["id"], SHORTLIST)

    out: dict[tuple, dict] = {}
    rank = {MAIN: 0, BACKUP: 1, SHORTLIST: 2}
    for rid, role in roles.items():
        run = run_repo.get(conn, rid)
        if run is None:
            continue
        latest = run_repo.same_setup(conn, run)[0]
        k = (run["topic"], run["region"], run["language"], run["window_days"], run["fmt"])
        if k not in out or rank[role] < rank[out[k]["role"]]:
            out[k] = {"run": latest, "role": role}
    return sorted(out.values(), key=lambda x: (rank[x["role"]], x["run"]["topic"]))


def due_redigs(conn: sqlite3.Connection, now: datetime | None = None, days: int | None = None) -> list[dict]:
    """Ngách theo dõi có lần đào mới nhất cũ hơn `days` ngày (mặc định REDIG_DAYS)."""
    now = now or datetime.now(timezone.utc)
    days = cfg.REDIG_DAYS if days is None else days
    out = []
    for t in tracked(conn):
        age = _age_days(t["run"], now)
        if age >= days:
            out.append({**t, "age_days": age})
    return out


def stage_checklist(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """10 mục checklist tổng kết của chặng Chọn ngách: [{"label", "ok", "detail"}]."""
    now = now or datetime.now(timezone.utc)
    runs = run_repo.list_all(conn)
    board = latest_board(conn)
    board_runs = board["runs"] if board else [r for r in runs if r["shortlisted"]]
    items = []

    def add(label: str, ok: bool, detail: str) -> None:
        items.append({"label": label, "ok": bool(ok), "detail": detail})

    fmt_topics = {r["topic"] for r in runs if r["fmt"] in ("long", "shorts")}
    add(
        "Đào ít nhất 3 ngách hoặc ngách con đúng định dạng sẽ làm",
        len(fmt_topics) >= 3,
        f"{len(fmt_topics)} chủ đề đã đào riêng video dài hoặc Shorts",
    )

    sheets = [(r["topic"], seven_cells(r)) for r in board_runs]
    incomplete = [t for t, s in sheets if not s["complete"]]
    add(
        "Ghi phiếu 7 ô cho mỗi ngách",
        sheets and not incomplete,
        "chưa có ngách nào trong bảng chấm" if not sheets else (f"chưa đủ: {', '.join(incomplete)}" if incomplete else f"{len(sheets)} ngách đủ 7 ô"),
    )

    no_trend = [r["topic"] for r in board_runs if not r["result"]["trends"].get("class")]
    add(
        "Phân loại Google Trends: bền, trào lưu, đang rơi hoặc theo mùa",
        board_runs and not no_trend,
        "chưa có ngách nào trong bảng chấm" if not board_runs else (f"chưa có Trends: {', '.join(no_trend)}" if no_trend else "đủ"),
    )

    def opened(r: dict) -> int:
        notes = r.get("channel_notes") or {}
        return len(set(r["checked_channels"]) | {c for c, n in notes.items() if n.get("reup")})

    best_open = max((opened(r) for r in runs), default=0)
    add("Xem trực tiếp ít nhất 3 kênh nhỏ đang nổ", best_open >= 3, f"nhiều nhất {best_open} kênh đã mở trong một lần đào")

    best_kw = 0
    for s in kw_svc.seeds(conn):
        good = [
            k for k in kw_svc.scored_for(conn, s["seed"], s["region"]) if k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]
        ]
        best_kw = max(best_kw, len(good))
    add(f"Gom 10 từ khoá dài có điểm từ {cfg.KEYWORD_GOOD} trở lên", best_kw >= 10, f"{best_kw}/10 (một ngách con)")

    warns = setup_warnings(board_runs) if len(board_runs) >= 2 else ["chưa có ít nhất 2 ngách để so"]
    add("So các ngách trong cùng điều kiện, cùng ngày", not warns, "; ".join(warns) or "cùng thiết lập")

    add(
        "Điền bảng chấm có trọng số, chốt trọng số trước khi chấm",
        board is not None,
        f"bảng \"{board['name']}\" khoá lúc {board['locked_at'][:10]}" if board else "chưa có bảng chấm đã khoá và chấm xong",
    )

    d = (board or {}).get("decision") or {}
    ok8 = bool(d.get("main") and d.get("backup") and board["promise"])
    add(
        "Chốt ngách chính và dự phòng, viết lời hứa kênh một câu",
        ok8,
        (
            f"chính: {d['main']['name']}; dự phòng: {d['backup']['name'] if d.get('backup') else 'chưa có'}; "
            f"lời hứa: {'có' if board['promise'] else 'chưa viết'}"
        )
        if d.get("main")
        else "chưa chốt",
    )

    has_plan = bool(board) and conn.execute("SELECT 1 FROM test_plan WHERE scorecard_id = ?", (board["id"],)).fetchone()
    add("Lên kế hoạch 10 video thử với mốc xem lại ở video 5 và 10", has_plan, "đã có kế hoạch" if has_plan else "chưa lập")

    due = due_redigs(conn, now)
    main_age = None
    if d.get("main"):
        main_run = run_repo.get(conn, d["main"]["id"])
        if main_run:
            main_age = _age_days(run_repo.same_setup(conn, main_run)[0], now)
    add(
        f"Lưu ngách và đào lại mỗi tháng ({cfg.REDIG_DAYS} ngày)",
        main_age is not None and not due,
        ("chưa chốt ngách chính" if main_age is None else f"ngách chính đào lần cuối {main_age} ngày trước")
        + (f"; {len(due)} ngách đến hạn đào lại" if due else ""),
    )
    return items
