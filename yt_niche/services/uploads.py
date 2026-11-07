"""Trang Upload: gán kênh YouTube cho kênh local, làm mới danh sách video, kiểm URL video đúng kênh.

Không đọc catalog. Ghi local duy nhất đi qua `pipectl.mark_published` (confirm_pipeline / retry_pending); video cũ chỉ ghi DB app.
Lỗi API được ném ra cho nơi gọi hiện (không nuốt).
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from urllib.parse import unquote

from yt_niche import config as cfg
from yt_niche.core import upload_facebook as fb
from yt_niche.core import upload_state as us
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
# Nền tảng của kênh (không có dòng trong DB và không có trong config = YouTube)
# ---------------------------------------------------------------------------


def platform_of(conn: sqlite3.Connection, slug: str) -> str:
    row = repo.get_platform_row(conn, slug)
    if row:
        return row["platform"]
    return us.FACEBOOK if slug in cfg.UPLOAD_FACEBOOK_CHANNELS else us.YOUTUBE


def page_url_of(conn: sqlite3.Connection, slug: str) -> str:
    """URL trang Facebook của kênh; dòng trong DB thắng mặc định trong config. Có thể trống."""
    row = repo.get_platform_row(conn, slug)
    if row:
        return row["page_url"] or ""
    return cfg.UPLOAD_FACEBOOK_CHANNELS.get(slug, "")


def set_page_url(conn: sqlite3.Connection, slug: str, url: str) -> None:
    """Khai (hoặc xoá, nếu để trống) URL trang Facebook của một kênh Facebook."""
    if platform_of(conn, slug) != us.FACEBOOK:
        raise UploadError("Kênh này không đăng Facebook.")
    url = (url or "").strip()
    if url:
        _, u = fb._split(url)
        if u is None or (u.hostname or "").lower() not in fb.FB_HOSTS or not fb.page_slug(url):
            raise UploadError("Không phải URL trang Facebook (ví dụ https://www.facebook.com/TenTrang/).")
    repo.set_platform(conn, slug, us.FACEBOOK, url)


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
    """Ghi kênh YouTube cho kênh local. Kênh YouTube đã gán cho kênh local khác → UploadError. Kênh Facebook không gán được."""
    if platform_of(conn, slug) == us.FACEBOOK:
        raise UploadError("Kênh này chỉ đăng Facebook nên không gán kênh YouTube.")
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
            if not ch or platform_of(conn, slug) == us.FACEBOOK:
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
    if not st.get("video_id") and st.get("published_at"):
        return _retry_without_link(conn, slug, number, st["published_at"], root)  # đánh dấu tay: không có link YouTube
    if not st.get("video_id") or not st.get("published_at"):
        return WriteResult(pipectl.ERROR, "Thiếu link hoặc ngày đã lưu để thử lại.")
    try:
        run_dir = pipectl.find_run_dir(root, slug, number)
    except pipectl.PipectlError as e:
        return WriteResult(pipectl.ERROR, f"Không đọc được trạng thái run: {e}", st["video_id"], st["published_at"])
    if not run_dir:
        return WriteResult(pipectl.ERROR, f"Không tìm thấy run {slug} #{number:03d}.", st["video_id"], st["published_at"])
    return _write(conn, slug, number, st["video_id"], st["published_at"], run_dir)


def _retry_without_link(conn, slug: str, number: int, published_at: str, root) -> WriteResult:
    """Thử lại ghi thiếu của video đánh dấu tay: `--url ""` và ngày đã lưu."""
    try:
        run_dir = pipectl.find_run_dir(root, slug, number)
    except pipectl.PipectlError as e:
        return WriteResult(pipectl.ERROR, f"Không đọc được trạng thái run: {e}", "", published_at)
    if not run_dir:
        return WriteResult(pipectl.ERROR, f"Không tìm thấy run {slug} #{number:03d}.", "", published_at)
    res = pipectl.mark_published(run_dir, "", published_at)
    if res.status == pipectl.OK:
        repo.update_state(conn, slug, number, catalog_pending=0)
    return WriteResult(res.status, res.message, "", published_at, run_dir)


def confirm_old(conn: sqlite3.Connection, slug: str, number: int, video_id: str, published_at: str | None) -> None:
    """Video cũ: chỉ ghi DB app. KHÔNG gọi pipectl, không đổi folder (sửa được bằng unconfirm_old)."""
    repo.confirm(conn, slug, number, watch_url(video_id), video_id, published_at, pending=False)


def unconfirm_old(conn: sqlite3.Connection, slug: str, number: int) -> None:
    repo.unconfirm(conn, slug, number)


# ---------------------------------------------------------------------------
# Đánh dấu đã đăng tay, đổi tên sau 1 giờ (T6)
#
# Đường này KHÔNG qua verify_for_write: app không kiểm được video thuộc kênh nào (owner đã chọn sau khi thấy cái giá).
# Hàm ở đây không bao giờ đặt youtube_url; `--url` gửi cho pipectl luôn rỗng. Link Facebook chỉ nằm trong DB app.
# ---------------------------------------------------------------------------

MARK_DELAY = timedelta(hours=1)
SKIPPED = "skipped"  # run đã published: chỉ xoá trạng thái chờ, không gọi lệnh
KEPT = "kept"  # run chưa xong hoặc không tìm thấy: giữ nguyên


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DueResult:
    slug: str
    number: int
    status: str  # pipectl.OK | CATALOG_PENDING | CATALOG_NOT_READY | LOCKED | ERROR | SKIPPED | KEPT
    message: str = ""

    @property
    def resolved(self) -> bool:
        """Mục đã hết chờ (đã ghi, ghi thiếu catalog, hoặc run đã published sẵn)."""
        return self.status in (pipectl.OK, pipectl.CATALOG_PENDING, SKIPPED)


def _check_fb_link(conn, slug: str, number: int, fb_url: str | None) -> str:
    """Kiểm link Facebook nhập tay; trả link đã làm sạch ('' nếu không nhập). Lỗi → UploadError."""
    if not (fb_url or "").strip():
        return ""
    chk = fb.parse_facebook_url(fb_url, page_url_of(conn, slug))
    if not chk.ok:
        raise UploadError(chk.error)
    key = fb.link_key(chk.url)
    for n, other in repo.list_fb_links(conn, slug):
        if n != number and fb.link_key(other) == key:
            raise UploadError(f"Link này đã dùng cho video {n:03d} của kênh.")
    return chk.url


def fb_link_check(conn, slug: str, fb_url: str) -> fb.FbCheck:
    """Cho trang: kiểm link (cảnh báo vàng/đỏ) trước khi lưu."""
    return fb.parse_facebook_url(fb_url, page_url_of(conn, slug))


def mark_later(
    conn: sqlite3.Connection, slug: str, number: int, *, fb_url: str | None = None, published_at: str | None = None,
    now: datetime | None = None, run_state: str | None = None,
) -> str:
    """Owner báo «đã đăng»: chỉ ghi DB app, chưa gọi pipectl. Tới `due_at` (= lúc bấm + 1 giờ) `run_due` mới đổi tên.

    Kênh YouTube không nhận link, không nhận ngày. Kênh Facebook: cả hai là tùy chọn; không nhập ngày thì ngày là `due_at`.
    `run_state` (nếu truyền): trạng thái run lúc bấm, phải là `complete`. Trả `due_at` (ISO UTC).
    """
    now = now or _utcnow()
    if run_state is not None and run_state != "complete":
        raise UploadError("Chỉ đánh dấu được video pipeline đã xong và chưa published.")
    plat = platform_of(conn, slug)
    cur = repo.get_state(conn, slug, number) or {}
    if cur.get("manual_state") == us.MANUAL_WAITING_UPLOAD:
        raise UploadError("Video này đã được đánh dấu, đang chờ đổi tên.")
    if cur.get("catalog_pending"):
        raise UploadError("Video này đang ghi thiếu catalog; thử lại ghi trước.")
    if plat == us.YOUTUBE and ((fb_url or "").strip() or published_at):
        raise UploadError("Kênh YouTube không nhận link hay ngày nhập tay (app sẽ đề xuất link và ngày thật khi YouTube đọc được).")
    link = _check_fb_link(conn, slug, number, fb_url) if plat == us.FACEBOOK else ""
    due = fb.to_iso(now + MARK_DELAY)
    if published_at:
        try:
            fb.check_iso_not_future(published_at, now)
        except ValueError as e:
            raise UploadError(str(e)) from e
    repo.update_state(conn, slug, number, manual_state=us.MANUAL_WAITING_UPLOAD, due_at=due,
                      published_at=published_at or due, fb_url=link, video_url="", video_id="")
    return due


def cancel_mark(conn: sqlite3.Connection, slug: str, number: int) -> None:
    """Huỷ khi đang chờ (trước khi đổi tên). Sau khi đã đổi tên thì không có lệnh huỷ."""
    cur = repo.get_state(conn, slug, number) or {}
    if cur.get("manual_state") != us.MANUAL_WAITING_UPLOAD:
        return
    repo.update_state(conn, slug, number, manual_state="", due_at="", published_at=None, fb_url="", next_try_at="", try_error="")


def _runs_index(root) -> dict[tuple[str, int], dict]:
    out = {}
    for r in pipectl.runs(root).get("runs", []):
        n = pipectl._num(r.get("video_id"))
        if n is not None:
            out[(r.get("channel_slug"), n)] = r
    return out


RETRY_AFTER = timedelta(minutes=5)  # thử đổi tên thất bại thì đợi bấy nhiêu mới thử lại (tránh vòng lặp dồn dập)


def _project_published_at(run_dir) -> str | None:
    try:
        doc = json.loads((Path(run_dir) / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = doc.get("published_at") if isinstance(doc, dict) else None
    return v if isinstance(v, str) and v else None


def _failed(conn, slug: str, number: int, status: str, message: str, now: datetime) -> DueResult:
    """Thử không xong: giữ mục chờ, nhớ lý do và giờ thử lại trong DB (không mất khi tải lại trang)."""
    repo.update_state(conn, slug, number, next_try_at=fb.to_iso(now + RETRY_AFTER), try_error=message)
    return DueResult(slug, number, status, message)


def _process(conn, slug: str, number: int, run: dict | None, published_at: str, now: datetime) -> DueResult:
    """Một mục chờ: chỉ gọi pipectl khi run chưa published (nếu đã có ngày thật thì không được ghi đè bằng ngày ước lượng)."""
    if run is None:
        return _failed(conn, slug, number, KEPT, "Không tìm thấy run trong pipectl runs.", now)
    if run.get("state") == "published" or run.get("lifecycle") == "published":
        # Ngày trong DB app theo project.json của run (ngày thật), không giữ ngày ước lượng đã lưu.
        real = _project_published_at(run.get("run_dir", ""))
        extra = {"published_at": real} if real else {}
        repo.update_state(conn, slug, number, manual_state="", next_try_at="", try_error="", **extra)
        return DueResult(slug, number, SKIPPED, "Run đã published sẵn: không ghi đè ngày, chỉ bỏ trạng thái chờ.")
    if run.get("state") != "complete":
        return _failed(conn, slug, number, KEPT, f"Run chưa xong (`{run.get('state')}`), chưa đổi tên.", now)
    res = pipectl.mark_published(run["run_dir"], "", published_at)  # --url luôn rỗng
    if res.status == pipectl.OK:
        repo.update_state(conn, slug, number, manual_state="", published_at=published_at, catalog_pending=0,
                          next_try_at="", try_error="")
    elif res.status == pipectl.CATALOG_PENDING:
        repo.update_state(conn, slug, number, manual_state="", published_at=published_at, catalog_pending=1,
                          next_try_at="", try_error="")
    else:
        return _failed(conn, slug, number, res.status, res.message, now)
    return DueResult(slug, number, res.status, res.message)


def due_items(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Mục đã đến hạn và không còn đang đợi giờ thử lại (`next_try_at`)."""
    now = now or _utcnow()
    out = []
    for st in repo.list_waiting(conn, us.MANUAL_WAITING_UPLOAD):
        t = fb.parse_iso(st["due_at"])
        retry = fb.parse_iso(st.get("next_try_at"))
        if t is not None and t <= now and (retry is None or retry <= now):
            out.append(st)
    return out


def run_due(conn: sqlite3.Connection, root, now: datetime | None = None) -> list[DueResult]:
    """Chạy các mục đã đến hạn. Việc kiểm DB rất rẻ; chỉ gọi `pipectl runs` khi thật sự có mục đến hạn."""
    now = now or _utcnow()
    due = due_items(conn, now)
    if not due:
        return []
    try:
        index = _runs_index(root)
    except pipectl.PipectlError as e:
        return [_failed(conn, d["channel_slug"], d["number"], KEPT, f"Không đọc được trạng thái run: {e}", now) for d in due]
    return [_process(conn, d["channel_slug"], d["number"], index.get((d["channel_slug"], d["number"])), d["published_at"], now)
            for d in due]


def run_now(conn: sqlite3.Connection, root, slug: str, number: int, now: datetime | None = None) -> DueResult:
    """«Ghi ngay» không chờ giờ và bỏ qua thời gian đợi thử lại. Ngày = lúc bấm, trừ khi owner đã nhập ngày riêng thì giữ."""
    now = now or _utcnow()
    st = repo.get_state(conn, slug, number) or {}
    if st.get("manual_state") != us.MANUAL_WAITING_UPLOAD:
        return DueResult(slug, number, KEPT, "Video này không ở trạng thái chờ đổi tên.")
    stored = st.get("published_at")
    date = stored if stored and stored != st.get("due_at") else fb.to_iso(now)
    try:
        run = _runs_index(root).get((slug, number))
    except pipectl.PipectlError as e:
        return _failed(conn, slug, number, KEPT, f"Không đọc được trạng thái run: {e}", now)
    return _process(conn, slug, number, run, date, now)


# ---- Facebook: sửa link/ngày sau khi xong, video cũ ----

def set_fb_link(conn: sqlite3.Connection, slug: str, number: int, fb_url: str | None) -> None:
    """Thêm/sửa/xoá link Facebook (chỉ DB app). Link trống = xoá."""
    if platform_of(conn, slug) != us.FACEBOOK:
        raise UploadError("Kênh này không đăng Facebook.")
    repo.update_state(conn, slug, number, fb_url=_check_fb_link(conn, slug, number, fb_url))


def set_fb_date(conn: sqlite3.Connection, slug: str, number: int, published_at: str, root, *, now: datetime | None = None) -> WriteResult:
    """Sửa ngày của video Facebook đã `published`: chạy lại mark-published với `--published-at`, không đổi tên lại."""
    now = now or _utcnow()
    if platform_of(conn, slug) != us.FACEBOOK:
        return WriteResult(pipectl.ERROR, "Kênh này không đăng Facebook.")
    try:
        fb.check_iso_not_future(published_at, now)
        run = _runs_index(root).get((slug, number))
    except (ValueError, pipectl.PipectlError) as e:
        return WriteResult(pipectl.ERROR, str(e))
    if run is None:
        return WriteResult(pipectl.ERROR, f"Không tìm thấy run {slug} #{number:03d}.")
    res = pipectl.mark_published(run["run_dir"], "", published_at)
    out = WriteResult(res.status, res.message, "", published_at, run["run_dir"])
    if res.status == pipectl.OK:
        repo.update_state(conn, slug, number, published_at=published_at, catalog_pending=0)
    elif res.status == pipectl.CATALOG_PENDING:
        repo.update_state(conn, slug, number, published_at=published_at, catalog_pending=1)
    return out


def confirm_facebook_old(
    conn: sqlite3.Connection, slug: str, number: int, *, fb_url: str | None = None, published_at: str | None = None,
    now: datetime | None = None,
) -> None:
    """Video cũ của kênh Facebook: chỉ ghi DB app. KHÔNG gọi pipectl, không đổi folder. Sửa được bằng unconfirm_facebook_old."""
    now = now or _utcnow()
    if platform_of(conn, slug) != us.FACEBOOK:
        raise UploadError("Kênh này không đăng Facebook.")
    link = _check_fb_link(conn, slug, number, fb_url)
    if published_at:
        try:
            fb.check_iso_not_future(published_at, now)
        except ValueError as e:
            raise UploadError(str(e)) from e
    repo.update_state(conn, slug, number, manual_state=us.MANUAL_FB_DONE, fb_url=link, published_at=published_at or fb.to_iso(now))


def unconfirm_facebook_old(conn: sqlite3.Connection, slug: str, number: int) -> None:
    repo.update_state(conn, slug, number, manual_state="", fb_url="", published_at=None)
