"""Ngách con (bài 2.3): cây ngách mẹ → con, phiếu kiểm tra "đủ sống"."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from yt_niche.core.subniche import layer_status, viability
from yt_niche.db import runs as run_repo


def get_check(conn: sqlite3.Connection, run_id: int) -> dict:
    row = conn.execute("SELECT ideas, promise FROM niche_check WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else {"ideas": "", "promise": ""}


def save_check(conn: sqlite3.Connection, run_id: int, *, ideas: str, promise: str) -> None:
    conn.execute(
        """INSERT INTO niche_check (run_id, ideas, promise, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET ideas = excluded.ideas, promise = excluded.promise,
               updated_at = excluded.updated_at""",
        (run_id, ideas.strip(), " ".join(promise.split()), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def check(conn: sqlite3.Connection, run_id: int) -> dict:
    run = run_repo.get(conn, run_id)
    saved = get_check(conn, run_id)
    return {**viability(run, saved["ideas"], saved["promise"]), **saved, "layer": layer_status(run)}


def tree(conn: sqlite3.Connection, root_id: int) -> list[dict]:
    """Danh sách phẳng theo thứ tự cây: [{"run", "depth", "status"}], bắt đầu từ ngách mẹ."""
    out: list[dict] = []

    def walk(run: dict, depth: int) -> None:
        out.append({"run": run, "depth": depth, "status": layer_status(run)})
        for child in sorted(run_repo.children(conn, run["id"]), key=lambda r: r["id"]):
            walk(child, depth + 1)

    root = run_repo.get(conn, root_id)
    if root:
        walk(root, 0)
    return out


def root_of(conn: sqlite3.Connection, run_id: int) -> int:
    seen = set()
    run = run_repo.get(conn, run_id)
    while run and run.get("parent_id") and run["parent_id"] not in seen:
        seen.add(run["id"])
        parent = run_repo.get(conn, run["parent_id"])
        if parent is None:
            break
        run = parent
    return run["id"] if run else run_id
