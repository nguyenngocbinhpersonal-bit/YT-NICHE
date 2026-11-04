"""After the decision: monthly re-dig reminders (lesson 2.5) and the wrap-up checklist of the "Pick a niche" stage."""

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

MAIN, BACKUP, SHORTLIST = "main niche", "backup", "on scorecard"


def _age_days(run: dict, now: datetime) -> int:
    return (now - parse_iso(run["created_at"])).days


def latest_board(conn: sqlite3.Connection) -> dict | None:
    """The newest scorecard that is locked and has a main niche."""
    for b in scorecards.list_all(conn):
        if b["locked_at"]:
            data = scorecards.load(conn, b["id"])
            if data["decision"] and data["decision"]["main"]:
                return data
    return None


def tracked(conn: sqlite3.Connection) -> list[dict]:
    """Niches to follow: main / backup of the decided scorecards, and niches put on a scorecard.

    Each setup (topic, market, time window, format) keeps only its latest dig.
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
    """Tracked niches whose latest dig is older than `days` days (default REDIG_DAYS)."""
    now = now or datetime.now(timezone.utc)
    days = cfg.REDIG_DAYS if days is None else days
    out = []
    for t in tracked(conn):
        age = _age_days(t["run"], now)
        if age >= days:
            out.append({**t, "age_days": age})
    return out


def stage_checklist(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """The 10-item wrap-up checklist of the "Pick a niche" stage: [{"label", "ok", "detail"}]."""
    now = now or datetime.now(timezone.utc)
    runs = run_repo.list_all(conn)
    board = latest_board(conn)
    board_runs = board["runs"] if board else [r for r in runs if r["shortlisted"]]
    items = []

    def add(label: str, ok: bool, detail: str) -> None:
        items.append({"label": label, "ok": bool(ok), "detail": detail})

    fmt_topics = {r["topic"] for r in runs if r["fmt"] in ("long", "shorts")}
    add(
        "Dig at least 3 niches or sub-niches in the format you will make",
        len(fmt_topics) >= 3,
        f"{len(fmt_topics)} topics dug separately as long-form or Shorts",
    )

    sheets = [(r["topic"], seven_cells(r)) for r in board_runs]
    incomplete = [t for t, s in sheets if not s["complete"]]
    add(
        "Fill in the 7-cell sheet for every niche",
        sheets and not incomplete,
        "no niche on the scorecard yet" if not sheets else (f"incomplete: {', '.join(incomplete)}" if incomplete else f"{len(sheets)} niches with all 7 cells"),
    )

    no_trend = [r["topic"] for r in board_runs if not r["result"]["trends"].get("class")]
    add(
        "Classify Google Trends: evergreen, fad, declining or seasonal",
        board_runs and not no_trend,
        "no niche on the scorecard yet" if not board_runs else (f"no Trends yet: {', '.join(no_trend)}" if no_trend else "done"),
    )

    def opened(r: dict) -> int:
        notes = r.get("channel_notes") or {}
        return len(set(r["checked_channels"]) | {c for c, n in notes.items() if n.get("reup")})

    best_open = max((opened(r) for r in runs), default=0)
    add("Watch at least 3 small booming channels yourself", best_open >= 3, f"at most {best_open} channels opened in one dig")

    best_kw = 0
    for s in kw_svc.seeds(conn):
        good = [
            k for k in kw_svc.scored_for(conn, s["seed"], s["region"]) if k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]
        ]
        best_kw = max(best_kw, len(good))
    add(f"Collect 10 long-tail keywords scoring {cfg.KEYWORD_GOOD} or more", best_kw >= 10, f"{best_kw}/10 (one sub-niche)")

    warns = setup_warnings(board_runs) if len(board_runs) >= 2 else ["fewer than 2 niches to compare"]
    add("Compare niches under the same conditions, on the same day", not warns, "; ".join(warns) or "same setup")

    add(
        "Fill in the weighted scorecard, lock the weights before scoring",
        board is not None,
        f"scorecard \"{board['name']}\" locked on {board['locked_at'][:10]}" if board else "no locked and fully scored scorecard yet",
    )

    d = (board or {}).get("decision") or {}
    ok8 = bool(d.get("main") and d.get("backup") and board["promise"])
    add(
        "Lock in the main and backup niche, write a one-sentence channel promise",
        ok8,
        (
            f"main: {d['main']['name']}; backup: {d['backup']['name'] if d.get('backup') else 'none yet'}; "
            f"promise: {'written' if board['promise'] else 'not written'}"
        )
        if d.get("main")
        else "not decided yet",
    )

    has_plan = bool(board) and conn.execute("SELECT 1 FROM test_plan WHERE scorecard_id = ?", (board["id"],)).fetchone()
    add("Plan 10 test videos with review points at video 5 and 10", has_plan, "plan exists" if has_plan else "not planned yet")

    due = due_redigs(conn, now)
    main_age = None
    if d.get("main"):
        main_run = run_repo.get(conn, d["main"]["id"])
        if main_run:
            main_age = _age_days(run_repo.same_setup(conn, main_run)[0], now)
    add(
        f"Save the niche and dig again every month ({cfg.REDIG_DAYS} days)",
        main_age is not None and not due,
        ("main niche not decided yet" if main_age is None else f"main niche last dug {main_age} days ago")
        + (f"; {len(due)} niches due for a re-dig" if due else ""),
    )
    return items
