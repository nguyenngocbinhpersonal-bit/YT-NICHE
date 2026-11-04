"""10-test-video plan attached to a scorecard (for the locked-in main niche)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from yt_niche.core import test_plan as tp
from yt_niche.services import scorecards

TEXT_FIELDS = ("pillar", "title", "hook", "thumbnail", "note")
DATE_FIELDS = ("planned_date", "published_at")
INT_FIELDS = ("impressions", "views", "avd_seconds", "new_subs")
FLOAT_FIELDS = ("ctr", "retention")
# Substring of the core.test_plan.plan_issues message about the number of pillars (the only issue checked on create / rename).
_PILLAR_ISSUE = "content pillar"


class PlanError(ValueError):
    pass


def _none_if_blank(value):
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN from pandas
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if str(value) in ("NaT", "nan"):  # empty cell from the Streamlit data editor
        return None
    return value


def get(conn: sqlite3.Connection, scorecard_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM test_plan WHERE scorecard_id = ?", (scorecard_id,)).fetchone()
    if row is None:
        return None
    plan = dict(row)
    plan["pillars"] = json.loads(plan.pop("pillars_json"))
    plan["drop_flags"] = json.loads(plan["drop_flags"])
    plan["videos"] = [
        {**dict(v), "asks_more": bool(v["asks_more"])}
        for v in conn.execute("SELECT * FROM test_video WHERE scorecard_id = ? ORDER BY idx", (scorecard_id,))
    ]
    return plan


def create(conn: sqlite3.Connection, scorecard_id: int, pillars: list[str], start: date, per_week: int) -> None:
    board = scorecards.load(conn, scorecard_id)
    main = (board["decision"] or {}).get("main")
    if not main:
        raise PlanError("The scorecard has no main niche yet. Finish scoring before planning.")
    pillars = [p.strip() for p in pillars]
    issues = [i for i in tp.plan_issues(pillars, []) if _PILLAR_ISSUE in i]
    if issues:
        raise PlanError(issues[0])
    if not 1 <= per_week <= 7:
        raise PlanError("Videos per week must be between 1 and 7")

    conn.execute(
        "INSERT INTO test_plan (scorecard_id, run_id, pillars_json, start_date, per_week, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            scorecard_id,
            main["id"],
            json.dumps(pillars, ensure_ascii=False),
            start.isoformat(),
            per_week,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    for i, (pillar, day) in enumerate(zip(tp.default_pillar_order(pillars), tp.schedule(start, per_week)), start=1):
        conn.execute(
            "INSERT INTO test_video (scorecard_id, idx, pillar, planned_date) VALUES (?, ?, ?, ?)",
            (scorecard_id, i, pillar, day.isoformat()),
        )


def rename_pillars(conn: sqlite3.Connection, scorecard_id: int, pillars: list[str]) -> None:
    plan = get(conn, scorecard_id)
    pillars = [p.strip() for p in pillars]
    issues = [i for i in tp.plan_issues(pillars, []) if _PILLAR_ISSUE in i]
    if issues:
        raise PlanError(issues[0])
    for old, new in zip(plan["pillars"], pillars):
        conn.execute("UPDATE test_video SET pillar = ? WHERE scorecard_id = ? AND pillar = ?", (new, scorecard_id, old))
    conn.execute(
        "UPDATE test_plan SET pillars_json = ? WHERE scorecard_id = ?", (json.dumps(pillars, ensure_ascii=False), scorecard_id)
    )


def save_videos(conn: sqlite3.Connection, scorecard_id: int, rows: list[dict]) -> None:
    """Save the plan columns (pillar, title, hook, thumbnail, dates) and the measurements, by idx."""
    for row in rows:
        values = {}
        for f in TEXT_FIELDS:
            if f in row:
                v = _none_if_blank(row[f])
                values[f] = "" if v is None else str(v).strip()
        for f in DATE_FIELDS:
            if f in row:
                v = _none_if_blank(row[f])
                values[f] = None if v is None else str(v)[:10]
        for f in INT_FIELDS:
            if f in row:
                v = _none_if_blank(row[f])
                values[f] = None if v is None else int(v)
        for f in FLOAT_FIELDS:
            if f in row:
                v = _none_if_blank(row[f])
                values[f] = None if v is None else float(v)
        if "asks_more" in row:
            values["asks_more"] = int(bool(_none_if_blank(row["asks_more"])))
        if not values:
            continue
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(
            f"UPDATE test_video SET {sets} WHERE scorecard_id = ? AND idx = ?",
            (*values.values(), scorecard_id, int(row["idx"])),
        )


def set_flags(
    conn: sqlite3.Connection, scorecard_id: int, *, fixes_applied: bool, out_of_ideas: bool, drop_flags: list[str]
) -> None:
    unknown = set(drop_flags) - set(tp.DROP_SIGNALS)
    if unknown:
        raise PlanError(f"Unknown signal: {', '.join(unknown)}")
    conn.execute(
        "UPDATE test_plan SET fixes_applied = ?, out_of_ideas = ?, drop_flags = ? WHERE scorecard_id = ?",
        (int(fixes_applied), int(out_of_ideas), json.dumps(sorted(drop_flags)), scorecard_id),
    )


def delete(conn: sqlite3.Connection, scorecard_id: int) -> None:
    conn.execute("DELETE FROM test_plan WHERE scorecard_id = ?", (scorecard_id,))


def keyword_ideas(conn: sqlite3.Connection, run: dict) -> list[dict]:
    """Scored long-tail keywords of the main niche with score ≥ 55 and not "low data"; keywords ticked "Pick to dig" first."""
    from yt_niche import config as cfg
    from yt_niche.services import keywords as kw_svc

    good = [
        k
        for k in kw_svc.scored_for(conn, run["topic"], run["region"])
        if k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]
    ]
    return sorted(good, key=lambda k: (not k["picked"], -k["score"]))


def fill_titles(conn: sqlite3.Connection, scorecard_id: int, titles: list[str]) -> int:
    """Fill draft titles (main keyword first) into the videos without a title, in order. Returns how many were filled."""
    titles = [t.strip() for t in titles if t and t.strip()]
    empty = [
        r["idx"]
        for r in conn.execute(
            "SELECT idx FROM test_video WHERE scorecard_id = ? AND title = '' ORDER BY idx", (scorecard_id,)
        )
    ]
    for idx, title in zip(empty, titles):
        conn.execute(
            "UPDATE test_video SET title = ? WHERE scorecard_id = ? AND idx = ?",
            (title[:1].upper() + title[1:], scorecard_id, idx),
        )
    return min(len(empty), len(titles))
