"""Bảng chấm chốt ngách: tạo, khoá trọng số, tạo phiên bản, chấm điểm, ra quyết định."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from yt_niche.core import scorecard as sc
from yt_niche.db import runs as run_repo
from yt_niche.services import keywords as keyword_svc

MIN_ITEMS, MAX_ITEMS = 2, 5


class ScorecardError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _header(conn: sqlite3.Connection, scorecard_id: int) -> dict:
    row = conn.execute("SELECT * FROM scorecard WHERE id = ?", (scorecard_id,)).fetchone()
    if row is None:
        raise ScorecardError(f"Không có bảng chấm #{scorecard_id}")
    d = dict(row)
    d["weights"] = json.loads(d.pop("weights_json"))
    return d


def _require_unlocked(header: dict) -> None:
    if header["locked_at"]:
        raise ScorecardError("Trọng số đã khoá. Muốn đổi thì tạo phiên bản mới.")


def _require_locked(header: dict) -> None:
    if not header["locked_at"]:
        raise ScorecardError("Khoá trọng số trước khi chấm điểm.")


# ---------------------------------------------------------------------------


def create(conn: sqlite3.Connection, name: str, run_ids: list[int]) -> int:
    run_ids = list(dict.fromkeys(run_ids))
    if not MIN_ITEMS <= len(run_ids) <= MAX_ITEMS:
        raise ScorecardError(f"Chọn từ {MIN_ITEMS} đến {MAX_ITEMS} ngách")
    runs = [run_repo.get(conn, rid) for rid in run_ids]
    if any(r is None for r in runs):
        raise ScorecardError("Có lần đào không còn tồn tại")
    low = [r["topic"] for r in runs if not r["result"]["can_shortlist"]]
    if low:
        raise ScorecardError(f"Độ tin cậy thấp, không đưa vào bảng chấm: {', '.join(low)}")

    cur = conn.execute(
        "INSERT INTO scorecard (name, weights_json, created_at) VALUES (?, ?, ?)",
        (name.strip() or "Bảng chấm", json.dumps(sc.DEFAULT_WEIGHTS), _now()),
    )
    scorecard_id = cur.lastrowid
    for pos, rid in enumerate(run_ids):
        conn.execute("INSERT INTO scorecard_item (scorecard_id, run_id, position) VALUES (?, ?, ?)", (scorecard_id, rid, pos))

    suggestions = sc.auto_suggestions(runs, _keywords(conn, runs))
    for rid, cells in suggestions.items():
        for criterion, (auto, rationale) in cells.items():
            conn.execute(
                """INSERT INTO scorecard_cell (scorecard_id, run_id, criterion, score, auto_score, rationale)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (scorecard_id, rid, criterion, auto, auto, rationale),
            )
    return scorecard_id


def set_weights(conn: sqlite3.Connection, scorecard_id: int, weights: dict[str, int]) -> None:
    _require_unlocked(_header(conn, scorecard_id))
    weights = {k: int(v) for k, v in weights.items()}
    if err := sc.weights_error(weights):
        raise ScorecardError(err)
    conn.execute("UPDATE scorecard SET weights_json = ? WHERE id = ?", (json.dumps(weights), scorecard_id))


def lock(conn: sqlite3.Connection, scorecard_id: int) -> None:
    header = _header(conn, scorecard_id)
    _require_unlocked(header)
    if err := sc.weights_error(header["weights"]):
        raise ScorecardError(err)
    conn.execute("UPDATE scorecard SET locked_at = ? WHERE id = ?", (_now(), scorecard_id))


def new_version(conn: sqlite3.Connection, scorecard_id: int) -> int:
    """Bản sao chưa khoá (giữ điểm đã chấm) để đổi trọng số; bản cũ giữ nguyên để đối chiếu."""
    h = _header(conn, scorecard_id)
    cur = conn.execute(
        """INSERT INTO scorecard (name, version, parent_id, weights_json, promise, backup_close, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (h["name"], h["version"] + 1, scorecard_id, json.dumps(h["weights"]), h["promise"], h["backup_close"], _now()),
    )
    new_id = cur.lastrowid
    conn.execute(
        """INSERT INTO scorecard_item (scorecard_id, run_id, position)
           SELECT ?, run_id, position FROM scorecard_item WHERE scorecard_id = ?""",
        (new_id, scorecard_id),
    )
    conn.execute(
        """INSERT INTO scorecard_cell (scorecard_id, run_id, criterion, score, auto_score, rationale, note)
           SELECT ?, run_id, criterion, score, auto_score, rationale, note FROM scorecard_cell WHERE scorecard_id = ?""",
        (new_id, scorecard_id),
    )
    return new_id


def set_scores(conn: sqlite3.Connection, scorecard_id: int, run_id: int, scores: dict[str, tuple[int | None, str]]) -> None:
    """scores: {criterion: (điểm 1–5 hoặc None, căn cứ người chấm)}."""
    _require_locked(_header(conn, scorecard_id))
    for criterion, (score, note) in scores.items():
        if criterion not in sc.CRITERIA_BY_KEY:
            raise ScorecardError(f"Tiêu chí lạ: {criterion}")
        if score is not None and not 1 <= int(score) <= 5:
            raise ScorecardError("Điểm phải từ 1 đến 5")
        conn.execute(
            "UPDATE scorecard_cell SET score = ?, note = ? WHERE scorecard_id = ? AND run_id = ? AND criterion = ?",
            (None if score is None else int(score), note.strip(), scorecard_id, run_id, criterion),
        )


def _keywords(conn: sqlite3.Connection, runs: list[dict]) -> dict[int, list[dict]]:
    return {r["id"]: keyword_svc.scored_for(conn, r["topic"], r["region"]) for r in runs}


def refresh_suggestions(conn: sqlite3.Connection, scorecard_id: int) -> None:
    """Tính lại gợi ý từ kết quả đào hiện tại (sau khi tính lại RPM / affiliate); không đụng điểm đã chấm."""
    data = load(conn, scorecard_id)
    for rid, cells in sc.auto_suggestions(data["runs"], _keywords(conn, data["runs"])).items():
        for criterion, (auto, rationale) in cells.items():
            conn.execute(
                """UPDATE scorecard_cell SET auto_score = ?, rationale = ?
                   WHERE scorecard_id = ? AND run_id = ? AND criterion = ?""",
                (auto, rationale, scorecard_id, rid, criterion),
            )


def set_promise(conn: sqlite3.Connection, scorecard_id: int, promise: str, backup_close: bool) -> None:
    conn.execute(
        "UPDATE scorecard SET promise = ?, backup_close = ? WHERE id = ?", (promise.strip(), int(backup_close), scorecard_id)
    )


def delete(conn: sqlite3.Connection, scorecard_id: int) -> None:
    conn.execute("DELETE FROM scorecard WHERE id = ?", (scorecard_id,))


def list_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.id, s.name, s.version, s.locked_at, s.created_at, COUNT(i.run_id) AS n_items
           FROM scorecard s LEFT JOIN scorecard_item i ON i.scorecard_id = s.id
           GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def load(conn: sqlite3.Connection, scorecard_id: int) -> dict:
    header = _header(conn, scorecard_id)
    item_rows = conn.execute(
        "SELECT run_id FROM scorecard_item WHERE scorecard_id = ? ORDER BY position", (scorecard_id,)
    ).fetchall()
    runs = [r for row in item_rows if (r := run_repo.get(conn, row["run_id"]))]

    cells: dict[int, dict[str, dict]] = {r["id"]: {} for r in runs}
    for row in conn.execute("SELECT * FROM scorecard_cell WHERE scorecard_id = ?", (scorecard_id,)):
        if row["run_id"] in cells:
            cells[row["run_id"]][row["criterion"]] = dict(row)

    items = [
        {"id": r["id"], "name": r["topic"], "scores": {k: c["score"] for k, c in cells[r["id"]].items()}} for r in runs
    ]
    return {
        **header,
        "runs": runs,
        "cells": cells,
        "decision": sc.decide(items, header["weights"]) if header["locked_at"] else None,
        "warnings": sc.setup_warnings(runs),
        "missing_runs": len(item_rows) - len(runs),
    }
