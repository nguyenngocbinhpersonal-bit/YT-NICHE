"""Save and read dig runs (the dig_run table)."""

from __future__ import annotations

import json
import sqlite3


def _row_to_run(row: sqlite3.Row, with_raw: bool = False) -> dict:
    run = dict(row)
    run["affiliates"] = json.loads(run["affiliates"])
    run["checked_channels"] = json.loads(run["checked_channels"])
    run["channel_notes"] = json.loads(run.get("channel_notes") or "{}")
    run["result"] = json.loads(run.pop("result_json"))
    raw = run.pop("raw_json", None)
    if with_raw and raw is not None:
        run["raw"] = json.loads(raw)
    return run


def insert(
    conn: sqlite3.Connection,
    *,
    raw: dict,
    result: dict,
    category: str | None,
    affiliates: list[str],
    parent_id: int | None = None,
    layer: int = 1,
    filter_type: str = "",
) -> int:
    p = raw["params"]
    cur = conn.execute(
        """INSERT INTO dig_run (topic, region, language, window_days, fmt, category, created_at,
               score, label, confidence, verdict, affiliates, raw_json, result_json, parent_id, layer, filter_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["topic"], p["region"], p["language"], p["window_days"], p["fmt"], category, raw["fetched_at"],
            result["score"], result["labels"][0], result["confidence"], result["three_tier"]["verdict"],
            json.dumps(affiliates, ensure_ascii=False),
            json.dumps(raw, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            parent_id, layer, filter_type,
        ),
    )
    return cur.lastrowid


def update_result(conn: sqlite3.Connection, run_id: int, result: dict, *, category: str | None, affiliates: list[str]) -> None:
    conn.execute(
        """UPDATE dig_run SET score = ?, label = ?, confidence = ?, verdict = ?, category = ?, affiliates = ?,
               result_json = ? WHERE id = ?""",
        (
            result["score"], result["labels"][0], result["confidence"], result["three_tier"]["verdict"], category,
            json.dumps(affiliates, ensure_ascii=False), json.dumps(result, ensure_ascii=False), run_id,
        ),
    )


def update_raw(conn: sqlite3.Connection, run_id: int, raw: dict) -> None:
    conn.execute("UPDATE dig_run SET raw_json = ? WHERE id = ?", (json.dumps(raw, ensure_ascii=False), run_id))


def set_checked_channels(conn: sqlite3.Connection, run_id: int, channel_ids: list[str]) -> None:
    conn.execute("UPDATE dig_run SET checked_channels = ? WHERE id = ?", (json.dumps(channel_ids), run_id))


def set_channel_note(conn: sqlite3.Connection, run_id: int, channel_id: str, *, note: str, reup: bool) -> None:
    """Note on one small booming channel: what it repeats (angle, length, thumbnail) and whether it is a re-upload channel."""
    row = conn.execute("SELECT channel_notes FROM dig_run WHERE id = ?", (run_id,)).fetchone()
    notes = json.loads(row["channel_notes"] or "{}")
    note = " ".join(note.split())
    if note or reup:
        notes[channel_id] = {"note": note, "reup": bool(reup)}
    else:
        notes.pop(channel_id, None)
    conn.execute("UPDATE dig_run SET channel_notes = ? WHERE id = ?", (json.dumps(notes, ensure_ascii=False), run_id))


def format_variants(conn: sqlite3.Connection, run: dict) -> list[dict]:
    """Digs with the same topic, market and time window in any format (to compare Any length with the winning format)."""
    rows = conn.execute(
        f"""SELECT {SUMMARY_COLUMNS} FROM dig_run
            WHERE topic = ? AND region = ? AND language = ? AND window_days = ?
            ORDER BY created_at DESC, id DESC""",
        (run["topic"], run["region"], run["language"], run["window_days"]),
    ).fetchall()
    return [_row_to_run(r) for r in rows]


def set_shortlisted(conn: sqlite3.Connection, run_id: int, value: bool) -> None:
    conn.execute("UPDATE dig_run SET shortlisted = ? WHERE id = ?", (int(value), run_id))


def get(conn: sqlite3.Connection, run_id: int, with_raw: bool = False) -> dict | None:
    row = conn.execute("SELECT * FROM dig_run WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row, with_raw) if row else None


SUMMARY_COLUMNS = (
    "id, topic, region, language, window_days, fmt, category, created_at, score, label, confidence, verdict, "
    "affiliates, checked_channels, shortlisted, result_json, parent_id, layer, filter_type, channel_notes"
)


def list_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM dig_run ORDER BY created_at DESC, id DESC").fetchall()
    return [_row_to_run(r) for r in rows]


def same_setup(conn: sqlite3.Connection, run: dict) -> list[dict]:
    """Digs with the same topic, market, time window and format; newest first."""
    rows = conn.execute(
        f"""SELECT {SUMMARY_COLUMNS} FROM dig_run
            WHERE topic = ? AND region = ? AND language = ? AND window_days = ? AND fmt = ?
            ORDER BY created_at DESC, id DESC""",
        (run["topic"], run["region"], run["language"], run["window_days"], run["fmt"]),
    ).fetchall()
    return [_row_to_run(r) for r in rows]


def delete(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM dig_run WHERE id = ?", (run_id,))


def children(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT {SUMMARY_COLUMNS} FROM dig_run WHERE parent_id = ? ORDER BY created_at DESC, id DESC", (run_id,)
    ).fetchall()
    return [_row_to_run(r) for r in rows]
