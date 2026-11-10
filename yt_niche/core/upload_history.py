"""Video đã upload, gom theo ngày, cho trang Upload. Hàm thuần, không IO.

Một video chỉ hiện một lần. Ngày thật từ YouTube thắng ngày ước tính của đánh dấu tay. Ngày tính theo giờ VN.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone, tzinfo

from yt_niche.core import upload_match as um
from yt_niche.core import upload_state as us

CONFIRMED = "confirmed"  # đã xác nhận, có link
MARKED = "marked"  # đã đăng theo đánh dấu của owner, chưa có link
WAITING = "waiting"  # đã bấm đánh dấu, chưa tới giờ đổi tên
UNCONFIRMED = "unconfirmed"  # đang có trên kênh, khớp một folder, owner chưa xác nhận
NO_FOLDER = "no_folder"  # đang có trên kênh, không khớp folder nào

BADGES = {
    CONFIRMED: "✅",
    MARKED: "✅ đã đánh dấu, chưa có link",
    WAITING: "🕐 chờ đổi tên",
    UNCONFIRMED: "🔔 chưa xác nhận",
    NO_FOLDER: "▫️ chưa thuộc folder nào",
}
_TIMEFMT = "%Y-%m-%dT%H:%M:%SZ"
_WEEKDAYS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")


@dataclass(frozen=True)
class Entry:
    channel_slug: str
    number: int | None
    title: str
    published_at: str | None  # UTC, None là chưa rõ ngày
    url: str
    source: str
    video_id: str = ""


def parse_iso(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s or "", _TIMEFMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _local_entry(v, st: dict) -> Entry | None:
    """Video trong folder đã đăng hoặc đã đánh dấu; chưa thì None."""
    title = v.title or (v.titles[0] if v.titles else "") or v.slug_title or v.name
    url = v.youtube_url or st.get("video_url") or st.get("fb_url") or ""
    vid = v.youtube_video_id or st.get("video_id") or ""
    if st.get("manual_state") == us.MANUAL_WAITING_UPLOAD:
        return Entry(v.channel_slug, v.number, title, st.get("published_at") or st.get("due_at"), url, WAITING, vid)
    if v.kind == "old":
        done = bool(st.get("video_url")) or st.get("manual_state") == us.MANUAL_FB_DONE
    else:
        done = v.state == "published" or bool(st.get("video_url"))
    if not done:
        return None
    date_ = v.published_at or st.get("published_at")
    return Entry(v.channel_slug, v.number, title, date_, url, CONFIRMED if url else MARKED, vid)


def channel_entries(slug: str, videos, states: dict, remotes, proposals) -> list[Entry]:
    """`videos`: VideoItem của kênh; `states`: number → dòng DB; `remotes`: RemoteVideo; `proposals`: Proposal của kênh."""
    by_number: dict[int, Entry] = {}
    for v in videos:
        e = _local_entry(v, states.get(v.number) or {})
        if e:
            by_number[v.number] = e
    linked = {e.video_id: e.number for e in by_number.values() if e.video_id}
    number_of = {p.video_id: p.number for p in proposals if p.strength == um.STRONG and p.channel_slug == slug}
    extra: list[Entry] = []
    for r in remotes:
        url = f"https://www.youtube.com/watch?v={r.video_id}"
        if r.video_id in linked:  # đã xác nhận: tiêu đề thật trên kênh (file local có thể nêu nhiều phương án); ngày thật nếu local chưa có
            n = linked[r.video_id]
            by_number[n] = replace(by_number[n], title=r.title or by_number[n].title,
                                   published_at=by_number[n].published_at or r.published_at)
            continue
        n = number_of.get(r.video_id)
        if n is None:
            extra.append(Entry(slug, None, r.title, r.published_at, url, NO_FOLDER, r.video_id))
        elif n in by_number:  # đánh dấu tay chưa có link: YouTube cho biết ngày thật
            by_number[n] = replace(by_number[n], title=r.title or by_number[n].title,
                                   published_at=r.published_at or by_number[n].published_at, url=url,
                                   source=UNCONFIRMED, video_id=r.video_id)
        else:
            extra.append(Entry(slug, n, r.title, r.published_at, url, UNCONFIRMED, r.video_id))
    return [*by_number.values(), *extra]


def local_day(iso: str | None, tz: tzinfo) -> date | None:
    t = parse_iso(iso)
    return t.astimezone(tz).date() if t else None


def by_day(entries: list[Entry], tz: tzinfo) -> list[tuple[date | None, list[Entry]]]:
    """Ngày mới nhất trước, trong ngày thì giờ mới nhất trước; video chưa rõ ngày nằm cuối."""
    days: dict[date | None, list[Entry]] = {}
    for e in entries:
        days.setdefault(local_day(e.published_at, tz), []).append(e)
    out = [(d, sorted(days[d], key=lambda e: (e.published_at or "", e.channel_slug, e.number or 0), reverse=True))
           for d in sorted((d for d in days if d is not None), reverse=True)]
    if None in days:
        out.append((None, sorted(days[None], key=lambda e: (e.channel_slug, e.number or 0))))
    return out


def within_days(entries: list[Entry], days: int | None, today: date, tz: tzinfo) -> list[Entry]:
    """Giữ video trong `days` ngày gần nhất, tính cả hôm nay. `days` None là tất cả. Video chưa rõ ngày luôn được giữ."""
    if days is None:
        return list(entries)
    since = today - timedelta(days=days - 1)
    out = []
    for e in entries:
        d = local_day(e.published_at, tz)
        if d is None or d >= since:
            out.append(e)
    return out


def day_label(d: date | None, today: date) -> str:
    if d is None:
        return "Chưa rõ ngày"
    text = f"{_WEEKDAYS[d.weekday()]}, {d.strftime('%d/%m/%Y')}"
    if d == today:
        return f"Hôm nay · {text}"
    if d == today - timedelta(days=1):
        return f"Hôm qua · {text}"
    return text


def time_label(iso: str | None, tz: tzinfo) -> str:
    t = parse_iso(iso)
    return t.astimezone(tz).strftime("%H:%M") if t else "--:--"


def channel_counts(entries: list[Entry]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        out[e.channel_slug] = out.get(e.channel_slug, 0) + 1
    return out


def escape_md(text: str) -> str:
    """Tiêu đề đi vào markdown: không để ký tự đặc biệt (`$`, `*`, `[`, `:`…) đổi cách hiển thị."""
    out = text or ""
    for ch in ("\\", "`", "*", "_", "[", "]", "$", "~", ":", "#", "<", ">", "|"):
        out = out.replace(ch, "\\" + ch)
    return out
