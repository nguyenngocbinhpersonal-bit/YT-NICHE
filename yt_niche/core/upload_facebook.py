"""Link và ngày cho kênh Facebook (đánh dấu tay). Hàm thuần, không IO.

App không đọc được Facebook nên đây chỉ là kiểm hình dạng link, không kiểm video có thật. Link Facebook chỉ nằm trong DB app,
không bao giờ đi vào `--url` của pipectl (đó là link YouTube).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

VN_TZ = timezone(timedelta(hours=7))
FB_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.com", "www.fb.com", "fb.watch"}
# Đoạn đầu của đường dẫn không phải tên trang
_RESERVED_FIRST = {"reel", "reels", "watch", "share", "permalink.php", "photo", "photo.php", "groups", "stories", "video.php",
                   "story.php", "videos", "events", "profile.php", "people", "hashtag", "login", "l.php"}
_TIMEFMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class FbCheck:
    ok: bool
    url: str = ""  # link đã bỏ khoảng trắng đầu cuối
    error: str = ""  # lý do từ chối (ok = False)
    warnings: list[str] = field(default_factory=list)  # vàng: dạng link chưa thử bằng link thật
    red: list[str] = field(default_factory=list)  # đỏ: không chặn


def _split(url: str):
    s = (url or "").strip()
    if not s:
        return s, None
    if "://" not in s:
        s = "https://" + s
    try:
        return s, urlparse(s)
    except ValueError:
        return s, None


def _parts(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def page_slug(page_url: str) -> str:
    """Tên trang từ URL trang (`/CoNhanChiThuat/` → `conhanchithuat`); rỗng nếu không rút ra được."""
    _, u = _split(page_url)
    if u is None:
        return ""
    parts = _parts(u.path)
    return parts[0].lower() if parts and parts[0].lower() not in _RESERVED_FIRST else ""


def parse_facebook_url(url: str, page_url: str = "") -> FbCheck:
    """Kiểm link video Facebook. Chỉ gọi khi owner có nhập link (link là tùy chọn)."""
    s, u = _split(url)
    if not s:
        return FbCheck(False, "", "Link trống.")
    if u is None or u.scheme not in ("http", "https"):
        return FbCheck(False, s, "Link không hợp lệ.")
    host = (u.hostname or "").lower()
    if host not in FB_HOSTS:
        if "youtube" in host or host == "youtu.be":
            return FbCheck(False, s, "Đây là link YouTube, không phải Facebook.")
        return FbCheck(False, s, f"«{host or s}» không phải Facebook (chỉ nhận facebook.com, fb.com, fb.watch).")
    parts = _parts(u.path)
    q = parse_qs(u.query)
    first = parts[0].lower() if parts else ""

    if host == "fb.watch":
        if not parts:
            return FbCheck(False, s, "Link fb.watch thiếu mã video.")
        return FbCheck(True, s)
    if not parts:
        return FbCheck(False, s, "Link này chỉ tới Facebook, không phải một video.")
    if first == "watch":
        if not (q.get("v") or [""])[0]:
            return FbCheck(False, s, "Link /watch thiếu mã video (?v=…).")
        return FbCheck(True, s)
    if first in ("reel", "share"):
        if len(parts) < 2:
            return FbCheck(False, s, f"Link /{first} thiếu mã.")
        return _with_page(FbCheck(True, s), parts, page_url)
    if len(parts) == 1 and first not in ("video.php", "permalink.php", "story.php", "photo.php"):
        return FbCheck(False, s, "Link này chỉ tới trang, không phải một video.")
    res = FbCheck(True, s)
    known = len(parts) >= 3 and parts[1].lower() in ("videos", "posts")
    if not known:
        res.warnings.append("Dạng link này chưa được thử bằng link thật. Kiểm lại link trước khi lưu.")
    return _with_page(res, parts, page_url)


def _with_page(res: FbCheck, parts: list[str], page_url: str) -> FbCheck:
    want = page_slug(page_url)
    first = parts[0].lower() if parts else ""
    if want and first and first not in _RESERVED_FIRST and first != want:
        res.red.append(f"Link thuộc trang «{parts[0]}», khác trang đã khai («{want}»). Kiểm xem có nhầm kênh không.")
    return res


def link_key(url: str) -> str:
    """Khoá để so hai link Facebook có trỏ cùng một chỗ không (bỏ www/m/web, dấu / cuối, tham số theo dõi)."""
    s, u = _split(url)
    if u is None:
        return s.lower()
    host = (u.hostname or "").lower()
    if host in FB_HOSTS and host != "fb.watch":
        host = "facebook.com"
    path = "/".join(_parts(u.path)).lower()
    v = (parse_qs(u.query).get("v") or [""])[0]
    return f"{host}/{path}" + (f"?v={v}" if v else "")


def vn_today(now: datetime) -> date:
    return now.astimezone(VN_TZ).date()


def date_to_iso(d: date, now: datetime) -> str:
    """Ngày owner chọn → 12:00 giờ VN, UTC dạng `%Y-%m-%dT%H:%M:%SZ`. Không cho ngày sau hôm nay (giờ VN)."""
    if d > vn_today(now):
        raise ValueError("Ngày đăng không được sau hôm nay.")
    return datetime(d.year, d.month, d.day, 12, tzinfo=VN_TZ).astimezone(timezone.utc).strftime(_TIMEFMT)


def parse_iso(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s or "", _TIMEFMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TIMEFMT)


def check_iso_not_future(iso: str, now: datetime) -> None:
    """Ngày nhập tay (đã ở dạng ISO) không được sau hôm nay theo giờ VN. Ném ValueError."""
    dt = parse_iso(iso)
    if dt is None:
        raise ValueError("Ngày đăng sai định dạng.")
    if dt.astimezone(VN_TZ).date() > vn_today(now):
        raise ValueError("Ngày đăng không được sau hôm nay.")
