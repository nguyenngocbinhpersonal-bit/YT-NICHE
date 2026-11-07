"""Trang Upload: gán kênh YouTube cho kênh local, làm mới danh sách video, kiểm URL video đúng kênh.

Không đọc catalog. Ghi local duy nhất đi qua `pipectl.mark_published` (confirm_pipeline / retry_pending); video cũ chỉ ghi DB app.
Lỗi API được ném ra cho nơi gọi hiện (không nuốt).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass, field

import httpx
from urllib.parse import unquote

from yt_niche.core.upload_match import norm
from yt_niche.db import uploads as repo
from yt_niche.services import pipectl, similar
from yt_niche.sources.youtube import QuotaExceeded, YouTubeClient, YouTubeError

# Trạng thái của check_video_url
OK = "ok"
CHANNEL_MISMATCH = "channel_mismatch"
NOT_READABLE = "not_readable"
CHANNEL_UNBOUND = "channel_unbound"


class UploadError(RuntimeError):
    """Lỗi người dùng đọc được (URL sai, không tìm thấy kênh, kênh đã gán cho kênh khác...)."""


def _names_match(expected: str, actual: str) -> bool:
    a, b = norm(expected), norm(actual)
    return bool(a and b and (a == b or a in b or b in a))


def _client(conn, api_key, transport, fresh=True) -> YouTubeClient:
    return YouTubeClient(conn, api_key=api_key, fresh=fresh, transport=transport)


# ---------------------------------------------------------------------------
# Xem trước khi gán (chưa ghi gì)
# ---------------------------------------------------------------------------


_STUDIO_VIDEO_RE = re.compile(r"studio\.youtube\.com/video/([\w-]{11})")


def parse_url(url: str) -> tuple[str, str]:
    """(loại, id) từ @handle / URL kênh / URL video, gồm cả link Studio `studio.youtube.com/video/<id>`.
    URL sai thì ném UploadError (không để SimilarError lọt ra trang)."""
    s = unquote((url or "").strip())
    if m := _STUDIO_VIDEO_RE.search(s):
        return similar.VIDEO, m.group(1)
    try:
        return similar.parse_input(s)
    except similar.SimilarError as e:
        raise UploadError(str(e)) from e


async def _preview(conn, url, api_key, transport) -> dict:
    kind, ident = parse_url(url)
    video = None
    async with _client(conn, api_key, transport) as yt:
        if kind == similar.VIDEO:
            vids = await yt.video_status([ident])
            if not vids:
                raise UploadError("Không đọc được video (riêng tư, đã xoá hoặc sai link).")
            video = vids[0]
            info = await yt.channel_lookup(channel_id=video["snippet"]["channelId"])
        elif kind == similar.HANDLE:
            info = await yt.channel_lookup(handle=ident)
        else:
            info = await yt.channel_lookup(channel_id=ident)
    if not info:
        raise UploadError("Không tìm thấy kênh.")
    return {"kind": kind, "channel": info, "video": video}


def preview_binding(
    conn: sqlite3.Connection,
    slug: str,
    url: str,
    expected_name: str,
    local_titles: list[str],
    *,
    api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Đọc kênh từ @handle / URL kênh / URL video. Trả tên kênh thật, số video, name_matches,
    video_title_matches (chỉ với URL video: tiêu đề có khớp tiêu đề local nào không) và bound_to
    (kênh local khác đã gán kênh YouTube này). Chưa ghi gì."""
    res = asyncio.run(_preview(conn, url, api_key, transport))
    info = res["channel"]
    out = {
        "kind": res["kind"],
        "channel": info,
        "name_matches": _names_match(expected_name, info["title"]),
        "video_count": info["video_count"],
        "video_title": None,
        "video_title_matches": None,
        "bound_to": None,
    }
    if res["video"] is not None:
        title = res["video"]["snippet"].get("title", "")
        out["video_title"] = title
        out["video_title_matches"] = norm(title) in {norm(t) for t in local_titles if norm(t)}
    other = repo.slug_for_channel_id(conn, info["channel_id"])
    if other and other != slug:
        out["bound_to"] = other
    return out


# ---------------------------------------------------------------------------
# Gán / đổi gán
# ---------------------------------------------------------------------------


def bind(conn: sqlite3.Connection, slug: str, info: dict) -> None:
    """Ghi kênh YouTube cho kênh local. Kênh YouTube đã gán cho kênh local khác → UploadError."""
    other = repo.slug_for_channel_id(conn, info["channel_id"])
    if other and other != slug:
        raise UploadError(f"Kênh YouTube này đã gán cho kênh «{other}». Đổi gán bên đó trước.")
    try:
        repo.upsert_channel(conn, slug, info)
    except sqlite3.IntegrityError as e:  # đua giữa hai phiên
        raise UploadError("Kênh YouTube này đã gán cho kênh local khác.") from e


def rebind(conn: sqlite3.Connection, slug: str, info: dict) -> None:
    """Đổi kênh YouTube đã gán. Xoá ảnh chụp video của kênh cũ."""
    old = repo.get_channel(conn, slug)
    bind(conn, slug, info)
    if old and old["channel_id"] != info["channel_id"]:
        conn.execute("DELETE FROM upload_remote WHERE channel_id = ?", (old["channel_id"],))


# ---------------------------------------------------------------------------
# Làm mới danh sách video
# ---------------------------------------------------------------------------


@dataclass
class RefreshResult:
    counts: dict[str, int] = field(default_factory=dict)  # slug → số video đọc được
    errors: dict[str, str] = field(default_factory=dict)  # slug → lỗi
    quota_exceeded: bool = False


async def _refresh(conn, slugs, api_key, transport) -> RefreshResult:
    res = RefreshResult()
    async with _client(conn, api_key, transport, fresh=True) as yt:
        for slug in slugs:
            ch = repo.get_channel(conn, slug)
            if not ch:
                continue
            try:
                if not ch["uploads_playlist"]:
                    raise UploadError("Kênh chưa có playlist uploads.")
                items = await yt.upload_items(ch["uploads_playlist"])
            except QuotaExceeded as e:
                msg = f"Hết quota YouTube: {e}"
                repo.set_refresh_result(conn, slug, msg)
                res.errors[slug] = msg
                res.quota_exceeded = True
                break
            except (YouTubeError, UploadError, httpx.HTTPError) as e:
                msg = str(e) or type(e).__name__
                repo.set_refresh_result(conn, slug, msg)
                res.errors[slug] = msg
                continue
            repo.replace_remote(conn, ch["channel_id"], items)
            repo.set_refresh_result(conn, slug)
            res.counts[slug] = len(items)
    return res


def refresh(
    conn: sqlite3.Connection,
    slugs: list[str],
    api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RefreshResult:
    """Đọc lại uploads của các kênh đã gán (không cache), cập nhật upload_remote. Lỗi một kênh không ảnh hưởng kênh khác."""
    return asyncio.run(_refresh(conn, slugs, api_key, transport))


# ---------------------------------------------------------------------------
# Kiểm URL video dán tay
# ---------------------------------------------------------------------------


async def _check(conn, ident, api_key, transport) -> dict | None:
    async with _client(conn, api_key, transport) as yt:
        vids = await yt.video_status([ident])
    return vids[0] if vids else None


def check_video_url(
    conn: sqlite3.Connection,
    slug: str,
    url: str,
    *,
    api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Đọc video và so channelId với kênh đã gán cho `slug`.

    status: ok | channel_mismatch | not_readable | channel_unbound. Kèm video_id, channel_id, published_at, privacy.
    """
    kind, ident = parse_url(url)
    if kind != similar.VIDEO:
        raise UploadError("Đây không phải link video.")
    bound = repo.get_channel(conn, slug)
    base = {"video_id": ident, "channel_id": None, "published_at": None, "privacy": None, "title": None}
    if not bound:
        return {**base, "status": CHANNEL_UNBOUND}
    video = asyncio.run(_check(conn, ident, api_key, transport))
    if video is None:
        return {**base, "status": NOT_READABLE}
    sn = video.get("snippet", {})
    info = {
        **base,
        "channel_id": sn.get("channelId"),
        "published_at": sn.get("publishedAt"),
        "privacy": video.get("status", {}).get("privacyStatus"),
        "title": sn.get("title"),
    }
    info["status"] = OK if sn.get("channelId") == bound["channel_id"] else CHANNEL_MISMATCH
    if info["status"] == CHANNEL_MISMATCH:
        info["other_slug"] = repo.slug_for_channel_id(conn, sn.get("channelId") or "")
    return info


# ---------------------------------------------------------------------------
# Xác nhận: ghi local
# ---------------------------------------------------------------------------

PRIVACY_PUBLIC = "public"


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass
class WriteResult:
    status: str  # pipectl.OK | CATALOG_PENDING | CATALOG_NOT_READY | LOCKED | ERROR (kèm "blocked" khi chưa đủ điều kiện)
    message: str = ""
    video_id: str = ""
    published_at: str = ""
    run_dir: str = ""

    @property
    def ok(self) -> bool:
        return self.status == pipectl.OK


BLOCKED = "blocked"


def verify_for_write(
    conn: sqlite3.Connection, slug: str, video_id: str, *, api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict | None, str]:
    """Đọc video và kiểm đủ điều kiện ghi: API chạy, video đọc được, public, đúng channelId đã gán.
    Trả (info, "") nếu được ghi, hoặc (info|None, lý do tiếng Việt)."""
    try:
        info = check_video_url(conn, slug, watch_url(video_id), api_key=api_key, transport=transport)
    except (UploadError, YouTubeError, httpx.HTTPError) as e:
        return None, f"Không đọc được YouTube nên chưa ghi: {e or type(e).__name__}"
    st = info["status"]
    if st == CHANNEL_UNBOUND:
        return info, "Kênh chưa gán URL YouTube. Gán kênh trước (dán URL một video là đủ)."
    if st == CHANNEL_MISMATCH:
        return info, "Video này thuộc kênh YouTube khác với kênh đã gán. Không ghi."
    if st == NOT_READABLE:
        return info, "Không đọc được video (riêng tư hoặc chưa public). Dùng «Đã upload, chờ public»."
    if info["privacy"] != PRIVACY_PUBLIC:
        return info, f"Video chưa public (trạng thái: {info['privacy']}). Dùng «Đã upload, chờ public»."
    if not info["published_at"]:
        return info, "YouTube không trả ngày đăng của video này."
    return info, ""


def confirm_pipeline(
    conn: sqlite3.Connection, slug: str, number: int, video_id: str, root, *,
    api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
) -> WriteResult:
    """Xác nhận một video pipeline: kiểm điều kiện, lấy run_dir MỚI, gọi mark-published.

    Ghi thiếu catalog (rc 2 + «catalog was not updated») → đặt catalog_pending cùng link và ngày để thử lại.
    """
    info, why = verify_for_write(conn, slug, video_id, api_key=api_key, transport=transport)
    if why:
        return WriteResult(BLOCKED, why, video_id)
    published_at = info["published_at"]
    try:
        run_dir = pipectl.find_run_dir(root, slug, number)
    except pipectl.PipectlError as e:
        return WriteResult(pipectl.ERROR, f"Không đọc được trạng thái run: {e}", video_id, published_at)
    if not run_dir:
        return WriteResult(pipectl.ERROR, f"Không tìm thấy run {slug} #{number:03d} trong pipectl runs.", video_id, published_at)
    return _write(conn, slug, number, video_id, published_at, run_dir)


def _write(conn, slug, number, video_id, published_at, run_dir) -> WriteResult:
    url = watch_url(video_id)
    res = pipectl.mark_published(run_dir, url, published_at)
    out = WriteResult(res.status, res.message, video_id, published_at, run_dir)
    if res.status == pipectl.OK:
        repo.confirm(conn, slug, number, url, video_id, published_at, pending=False)
    elif res.status == pipectl.CATALOG_PENDING:
        repo.confirm(conn, slug, number, url, video_id, published_at, pending=True)
    return out


def retry_pending(conn: sqlite3.Connection, slug: str, number: int, root) -> WriteResult:
    """Thử lại ghi catalog cho video có catalog_pending: chạy lại mark-published (idempotent) với link và ngày đã lưu."""
    st = repo.get_state(conn, slug, number) or {}
    if not st.get("video_id") or not st.get("published_at"):
        return WriteResult(pipectl.ERROR, "Thiếu link hoặc ngày đã lưu để thử lại.")
    try:
        run_dir = pipectl.find_run_dir(root, slug, number)
    except pipectl.PipectlError as e:
        return WriteResult(pipectl.ERROR, f"Không đọc được trạng thái run: {e}", st["video_id"], st["published_at"])
    if not run_dir:
        return WriteResult(pipectl.ERROR, f"Không tìm thấy run {slug} #{number:03d}.", st["video_id"], st["published_at"])
    return _write(conn, slug, number, st["video_id"], st["published_at"], run_dir)


def confirm_old(conn: sqlite3.Connection, slug: str, number: int, video_id: str, published_at: str | None) -> None:
    """Video cũ: chỉ ghi DB app. KHÔNG gọi pipectl, không đổi folder (sửa được bằng unconfirm_old)."""
    repo.confirm(conn, slug, number, watch_url(video_id), video_id, published_at, pending=False)


def unconfirm_old(conn: sqlite3.Connection, slug: str, number: int) -> None:
    repo.unconfirm(conn, slug, number)
