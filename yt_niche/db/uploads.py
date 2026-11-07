"""Repo SQLite cho trang Upload: kênh đã gán, ảnh chụp video remote, trạng thái từng video, đề xuất bị từ chối."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

MAX_THUMBS = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- upload_channel ----

def get_channel(conn: sqlite3.Connection, slug: str) -> dict | None:
    r = conn.execute("SELECT * FROM upload_channel WHERE channel_slug = ?", (slug,)).fetchone()
    return dict(r) if r else None


def list_channels(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM upload_channel ORDER BY channel_slug")]


def slug_for_channel_id(conn: sqlite3.Connection, channel_id: str) -> str | None:
    r = conn.execute("SELECT channel_slug FROM upload_channel WHERE channel_id = ?", (channel_id,)).fetchone()
    return r["channel_slug"] if r else None


def upsert_channel(conn: sqlite3.Connection, slug: str, info: dict) -> None:
    """Ghi (hoặc đổi) kênh gán. UNIQUE(channel_id) ném sqlite3.IntegrityError nếu kênh đã gán cho slug khác."""
    conn.execute(
        """INSERT INTO upload_channel (channel_slug, channel_id, handle, title, uploads_playlist, thumb_url, bound_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel_slug) DO UPDATE SET
               channel_id = excluded.channel_id, handle = excluded.handle, title = excluded.title,
               uploads_playlist = excluded.uploads_playlist, thumb_url = excluded.thumb_url,
               bound_at = excluded.bound_at, last_refresh_at = NULL, last_error = ''""",
        (slug, info["channel_id"], info.get("handle", ""), info.get("title", ""),
         info.get("uploads_playlist", ""), info.get("thumb_url", ""), now_iso()),
    )


def set_refresh_result(conn: sqlite3.Connection, slug: str, error: str = "") -> None:
    if error:
        conn.execute("UPDATE upload_channel SET last_error = ? WHERE channel_slug = ?", (error, slug))
    else:
        conn.execute(
            "UPDATE upload_channel SET last_refresh_at = ?, last_error = '' WHERE channel_slug = ?", (now_iso(), slug)
        )


# ---- upload_remote ----

def replace_remote(conn: sqlite3.Connection, channel_id: str, items: list[dict]) -> None:
    """Thay ảnh chụp video của một kênh bằng danh sách mới (video đã xoá trên YouTube biến mất)."""
    ts = now_iso()
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM upload_remote WHERE channel_id = ?", (channel_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO upload_remote (video_id, channel_id, title, published_at, fetched_at) VALUES (?, ?, ?, ?, ?)",
            [(i["video_id"], channel_id, i.get("title", ""), i.get("published_at"), ts) for i in items],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def list_remote(conn: sqlite3.Connection, channel_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM upload_remote WHERE channel_id = ? ORDER BY published_at DESC", (channel_id,)
        )
    ]


# ---- upload_video_state ----

def get_state(conn: sqlite3.Connection, slug: str, number: int) -> dict | None:
    r = conn.execute(
        "SELECT * FROM upload_video_state WHERE channel_slug = ? AND number = ?", (slug, number)
    ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["thumb_choice"] = json.loads(d["thumb_choice"] or "[]")
    return d


def list_states(conn: sqlite3.Connection, slug: str | None = None) -> list[dict]:
    sql = "SELECT channel_slug, number FROM upload_video_state"
    rows = conn.execute(sql + (" WHERE channel_slug = ?" if slug else ""), (slug,) if slug else ()).fetchall()
    return [get_state(conn, r["channel_slug"], r["number"]) for r in rows]


_FIELDS = {"manual_state", "video_url", "video_id", "published_at", "mp4_choice", "catalog_pending"}


def update_state(conn: sqlite3.Connection, slug: str, number: int, **fields) -> None:
    """Tạo dòng nếu chưa có rồi cập nhật các trường truyền vào (thumb_choice nhận list)."""
    if "thumb_choice" in fields:
        choice = list(dict.fromkeys(fields.pop("thumb_choice") or []))
        if len(choice) > MAX_THUMBS:
            raise ValueError(f"Chỉ chọn tối đa {MAX_THUMBS} thumbnail")
        fields["thumb_choice"] = json.dumps(choice, ensure_ascii=False)
    bad = set(fields) - _FIELDS - {"thumb_choice"}
    if bad:
        raise ValueError(f"trường không hợp lệ: {sorted(bad)}")
    conn.execute(
        "INSERT OR IGNORE INTO upload_video_state (channel_slug, number, updated_at) VALUES (?, ?, ?)",
        (slug, number, now_iso()),
    )
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE upload_video_state SET {sets}, updated_at = ? WHERE channel_slug = ? AND number = ?",
            (*fields.values(), now_iso(), slug, number),
        )


# ---- upload_match_rejected ----

def reject_match(conn: sqlite3.Connection, slug: str, number: int, video_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO upload_match_rejected (channel_slug, number, video_id, rejected_at) VALUES (?, ?, ?, ?)",
        (slug, number, video_id, now_iso()),
    )


def unreject_match(conn: sqlite3.Connection, slug: str, number: int, video_id: str) -> None:
    conn.execute(
        "DELETE FROM upload_match_rejected WHERE channel_slug = ? AND number = ? AND video_id = ?",
        (slug, number, video_id),
    )


def rejected_pairs(conn: sqlite3.Connection, slug: str | None = None) -> set[tuple[str, int, str]]:
    sql = "SELECT channel_slug, number, video_id FROM upload_match_rejected"
    rows = conn.execute(sql + (" WHERE channel_slug = ?" if slug else ""), (slug,) if slug else ()).fetchall()
    return {(r["channel_slug"], r["number"], r["video_id"]) for r in rows}
