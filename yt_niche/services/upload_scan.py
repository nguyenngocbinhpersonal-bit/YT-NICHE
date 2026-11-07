"""Quét `SOCIAL_MEDIA_ROOT` cho trang Upload: phân loại folder, khoá video, chọn file, nội dung thẻ.

Chỉ đọc file local: không gọi API, không đọc nội dung mp4 (chỉ `stat`, `samefile`), không ghi gì, không xoá gì.
`runs_json` (kết quả `pipectl runs --json`) do người gọi đưa vào để test không phải gọi pipectl.
"""

import filecmp
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from yt_niche import config

PUBLISHED_SUFFIX = "__PUBLISHED"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
LOGO_STEMS = {"logo", "avatar"}
THUMB_SUBDIRS = ("thumbnail", "thumbnail_frames")
# Thứ tự ưu tiên của file mô tả cũ (meta_file); phần tử đầu tiên tìm được là file hiện nguyên văn.
META_NAMES = ("YOUTUBE.md", "MO-TA.md", "titles.md", "description.txt")
YOUTUBE_LINK_RE = re.compile(r"(?:youtube\.com/(?:watch|shorts|live|embed)|youtu\.be/)", re.IGNORECASE)
VIDEO_NUM_RE = re.compile(r"video-(\d+)", re.IGNORECASE)
NAME_SUFFIX_RE = re.compile(r"(?:__PUBLISHED|_{1,2}DONE)$")


# ---------------------------------------------------------------------------
# Kiểu dữ liệu
# ---------------------------------------------------------------------------
@dataclass
class ChannelInfo:
    slug: str
    name: str = ""
    language: str = ""


@dataclass
class FileRef:
    rel: str  # đường dẫn tương đối theo folder video, dùng dấu /
    size: int = 0
    source: str = ""  # thumbnail: root | thumbnail | thumbnail_frames


@dataclass
class VideoItem:
    channel_slug: str
    number: int
    kind: str  # pipeline | old
    folder: Path
    name: str
    slug_title: str = ""
    titles: list[str] = field(default_factory=list)
    mp4s: list[FileRef] = field(default_factory=list)
    thumbnails: list[FileRef] = field(default_factory=list)
    # pipeline
    state: str | None = None
    lifecycle: str | None = None
    created: str | None = None
    published_at: str | None = None
    youtube_url: str | None = None
    youtube_video_id: str | None = None
    title: str | None = None  # title.primary
    title_en: str | None = None
    description: str | None = None  # description.primary, nguyên byte
    description_en: str | None = None  # description.en, nguyên byte
    disclaimer: object = None
    tags: list = field(default_factory=list)
    chapters: list = field(default_factory=list)
    description_has_youtube_link: bool = False
    # video cũ
    meta_file: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.channel_slug, self.number)

    @property
    def is_published_folder(self) -> bool:
        return self.name.endswith(PUBLISHED_SUFFIX)


@dataclass
class OtherFolder:
    channel_slug: str
    name: str
    path: Path
    kind: str  # junk | brand | other
    file_count: int = 0
    logo: str | None = None  # đường dẫn tương đối của logo/avatar (brand)


@dataclass
class ChannelScan:
    info: ChannelInfo
    videos: list[VideoItem] = field(default_factory=list)
    junk: list[OtherFolder] = field(default_factory=list)
    brand: list[OtherFolder] = field(default_factory=list)
    other: list[OtherFolder] = field(default_factory=list)


@dataclass
class ScanResult:
    root: Path
    channels: dict[str, ChannelScan] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        c = {"pipeline": 0, "old": 0, "junk": 0, "brand": 0, "other": 0}
        for ch in self.channels.values():
            for v in ch.videos:
                c[v.kind] += 1
            c["junk"] += len(ch.junk)
            c["brand"] += len(ch.brand)
            c["other"] += len(ch.other)
        c["total"] = sum(c.values())
        return c

    def videos(self) -> list[VideoItem]:
        return [v for ch in self.channels.values() for v in ch.videos]

    def get(self, key: tuple[str, int]) -> VideoItem | None:
        ch = self.channels.get(key[0])
        if not ch:
            return None
        return next((v for v in ch.videos if v.number == key[1]), None)


# ---------------------------------------------------------------------------
# Kênh
# ---------------------------------------------------------------------------
def channel_info(slug: str, yt_wf_root: Path | None = None) -> ChannelInfo:
    """`channel_name`, `language` từ producer-<slug>/config.json; thiếu file thì để trống."""
    root = Path(yt_wf_root) if yt_wf_root else config.YT_WF_ROOT
    p = root / ".claude" / "skills" / f"producer-{slug}" / "config.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return ChannelInfo(slug, str(d.get("channel_name") or ""), str(d.get("language") or ""))
    except (OSError, ValueError, AttributeError):
        return ChannelInfo(slug)


# ---------------------------------------------------------------------------
# Tiện ích file
# ---------------------------------------------------------------------------
def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _walk_files(folder: Path) -> list[str]:
    out: list[str] = []
    try:
        for dirpath, _dirs, files in os.walk(folder):
            out.extend(os.path.join(dirpath, f) for f in files)
    except OSError:
        pass
    return out


def _list_files(folder: Path) -> list[Path]:
    try:
        return sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
    except OSError:
        return []


def _rel(p: Path, base: Path) -> str:
    return p.relative_to(base).as_posix()


def find_mp4s(folder: Path) -> list[FileRef]:
    """mp4 ở gốc folder; hard link của cùng một file chỉ giữ một (tên đứng trước theo bảng chữ cái)."""
    kept: list[Path] = []
    for p in _list_files(folder):
        if p.suffix.lower() != ".mp4":
            continue
        try:
            if any(os.path.samefile(p, q) for q in kept):
                continue
        except OSError:
            pass
        kept.append(p)
    out = []
    for p in kept:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append(FileRef(_rel(p, folder), size))
    return out


def _is_thumbnails_collage(p: Path) -> bool:
    return p.stem.lower() == "thumbnails"


def _dedupe_images(paths: list[Path]) -> list[Path]:
    """Gộp ảnh trùng nội dung (cùng cỡ rồi so byte); giữ tên có stem dài hơn (có hậu tố như `_A`)."""
    groups: list[list[Path]] = []
    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        for g in groups:
            try:
                if g[0].stat().st_size == size and filecmp.cmp(g[0], p, shallow=False):
                    g.append(p)
                    break
            except OSError:
                continue
        else:
            groups.append([p])
    kept = [sorted(g, key=lambda p: (-len(p.stem), p.name.lower()))[0] for g in groups]
    return sorted(kept, key=lambda p: p.name.lower())


def find_thumbnails(folder: Path) -> list[FileRef]:
    """Ảnh ở gốc; không có thì `thumbnail/`, rồi `thumbnail_frames/`. Luôn bỏ `thumbnails.*` (ảnh ghép)."""
    for sub in ("", *THUMB_SUBDIRS):
        base = folder / sub if sub else folder
        cands = [p for p in _list_files(base) if p.suffix.lower() in IMAGE_EXTS and not _is_thumbnails_collage(p)]
        if cands:
            out = []
            for p in _dedupe_images(cands):
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                out.append(FileRef(_rel(p, folder), size, sub or "root"))
            return out
    return []


# ---------------------------------------------------------------------------
# Tiêu đề của video cũ (mọi phương án)
# ---------------------------------------------------------------------------
def _clean_title(s: str) -> str:
    s = re.sub(r"←.*$", "", s)
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\s*★\s*$", "", s)
    # đuôi ghi chú độ dài/số thứ tự "(42)", "(video 15)" không thuộc tiêu đề; "(Full Story)" thì giữ
    s = re.sub(r"\s*\((?:video\s*)?\d+\)\s*$", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_youtube_md(text: str) -> list[str]:
    """Danh sách đánh số của mục Titolo/Title/Tiêu đề; chỉ khi không có danh sách mới lấy heading."""
    titles: list[str] = []
    heading: list[str] = []
    lines = text.splitlines()
    if lines:
        m = re.match(r"^#\s*YouTube(?:\s+kit)?\s*[—–-]\s*(.+)$", lines[0].strip(), re.IGNORECASE)
        if m:
            heading.append(_clean_title(m.group(1)))
    in_section = False
    for ln in lines[1:]:
        if ln.startswith("## "):
            if in_section:
                break
            if re.match(r"^##\s*(tit|tiêu đề)", ln, re.IGNORECASE):
                in_section = True
            continue
        if in_section:
            m = re.match(r"^\s*\d+[.)]\s*(.+)$", ln)
            if m:
                titles.append(_clean_title(m.group(1)))
    titles = [t for t in titles if t]
    return titles or [t for t in heading if t]


def parse_mo_ta_md(text: str) -> list[str]:
    titles: list[str] = []
    first_heading = True
    for ln in text.splitlines():
        s = ln.strip()
        m = re.match(r"^\*\*Tiêu đề:\*\*\s*(.+)$", s)
        if m:
            full = _clean_title(m.group(1))
            titles.append(full)
            head = re.split(r"\s+[·|]\s+", full)[0].strip()
            if head and head != full:
                titles.append(head)
        elif first_heading and s.startswith("# "):
            first_heading = False
            h = re.sub(r"\s*·\s*mô tả để đăng\s*$", "", s[2:].strip())
            titles.append(_clean_title(h))
    return [t for t in titles if t]


def parse_titles_md(text: str) -> list[str]:
    titles: list[str] = []
    col = 1
    header_seen = False
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if not header_seen:
            header_seen = True
            for i, c in enumerate(cells):
                if re.search(r"title|tiêu đề|titolo|titol", c, re.IGNORECASE):
                    col = i
                    break
            continue
        if col < len(cells) and cells[col]:
            titles.append(_clean_title(cells[col]))
    return [t for t in titles if t]


_PARSERS = {"YOUTUBE.md": parse_youtube_md, "MO-TA.md": parse_mo_ta_md, "titles.md": parse_titles_md}


def _find_meta_files(folder: Path) -> list[Path]:
    """Mỗi tên trong META_NAMES: ở gốc trước, không có thì ở folder con cấp 1. Theo thứ tự META_NAMES."""
    found: list[Path] = []
    try:
        subdirs = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        subdirs = []
    for name in META_NAMES:
        for base in (folder, *subdirs):
            p = base / name
            if p.is_file():
                found.append(p)
                break
    return found


def old_titles(folder: Path) -> tuple[list[str], str | None]:
    """(mọi phương án tiêu đề, meta_file tương đối). Parser hỏng hoặc thiếu file thì danh sách rỗng."""
    titles: list[str] = []
    files = _find_meta_files(folder)
    for p in files:
        parser = _PARSERS.get(p.name)
        if not parser:
            continue
        try:
            for t in parser(_read_text(p)):
                if t not in titles:
                    titles.append(t)
        except Exception:  # parser hỏng không được làm hỏng cả lần quét
            continue
    meta = _rel(files[0], folder) if files else None
    return titles, meta


# ---------------------------------------------------------------------------
# Phân loại
# ---------------------------------------------------------------------------
def _video_number(name: str) -> int | None:
    m = VIDEO_NUM_RE.search(name)
    return int(m.group(1)) if m else None


def _slug_title(name: str) -> str:
    n = NAME_SUFFIX_RE.sub("", name)
    m = VIDEO_NUM_RE.search(n)
    rest = n[m.end():] if m else n
    return rest.strip("-_ ").replace("-", " ").replace("_", " ").strip()


def _pick_localized(d: object) -> str | None:
    """publish.json lưu {primary, en}; lấy `primary`, giữ nguyên từng byte."""
    if isinstance(d, dict):
        v = d.get("primary")
        return v if isinstance(v, str) else None
    return d if isinstance(d, str) else None


def _runs_by_dir(runs_json: dict | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in (runs_json or {}).get("runs", []):
        rd = r.get("run_dir")
        if rd:
            out[os.path.normcase(os.path.normpath(rd))] = r
    return out


def _pipeline_item(slug: str, folder: Path, run: dict | None) -> VideoItem | None:
    proj = _read_json(folder / "project.json")
    number = None
    vid = proj.get("video_id") or (run or {}).get("video_id")
    try:
        number = int(str(vid))
    except (TypeError, ValueError):
        number = _video_number(folder.name)
    if number is None:
        return None
    pub = _read_json(folder / "artifacts" / "publish.json")
    title = pub.get("title")
    item = VideoItem(
        channel_slug=slug,
        number=number,
        kind="pipeline",
        folder=folder,
        name=folder.name,
        slug_title=_slug_title(folder.name),
        mp4s=find_mp4s(folder),
        thumbnails=find_thumbnails(folder),
        state=(run or {}).get("state"),
        lifecycle=(run or {}).get("lifecycle"),
        created=(run or {}).get("created"),
        published_at=proj.get("published_at"),
        youtube_url=proj.get("youtube_url"),
        youtube_video_id=proj.get("youtube_video_id"),
        title=_pick_localized(title),
        title_en=title.get("en") if isinstance(title, dict) and isinstance(title.get("en"), str) else None,
        description=_pick_localized(pub.get("description")),
        description_en=pub["description"].get("en")
        if isinstance(pub.get("description"), dict) and isinstance(pub["description"].get("en"), str)
        else None,
        disclaimer=pub.get("disclaimer"),
        tags=pub.get("tags") if isinstance(pub.get("tags"), list) else [],
        chapters=pub.get("chapters") if isinstance(pub.get("chapters"), list) else [],
    )
    item.titles = [item.title] if item.title else []
    item.description_has_youtube_link = bool(item.description and YOUTUBE_LINK_RE.search(item.description))
    return item


def _find_logo(folder: Path) -> str | None:
    for p in _list_files(folder):
        if p.stem.lower() in LOGO_STEMS and p.suffix.lower() in IMAGE_EXTS:
            return _rel(p, folder)
    return None


def scan_channel(
    root: Path, slug: str, runs_by_dir: dict[str, dict] | None = None, yt_wf_root: Path | None = None
) -> ChannelScan:
    cdir = Path(root) / slug
    runs_by_dir = runs_by_dir or {}
    ch = ChannelScan(channel_info(slug, yt_wf_root))
    try:
        subs = sorted((p for p in cdir.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return ch
    names = {p.name for p in subs}
    for folder in subs:
        name = folder.name
        try:
            if (folder / "project.json").is_file():
                run = runs_by_dir.get(os.path.normcase(os.path.normpath(str(folder))))
                item = _pipeline_item(slug, folder, run)
                if item:
                    ch.videos.append(item)
                else:
                    ch.other.append(OtherFolder(slug, name, folder, "other"))
                continue
            if name.lower() == "brand":
                ch.brand.append(OtherFolder(slug, name, folder, "brand", logo=_find_logo(folder)))
                continue
            # chỉ folder có anh em __PUBLISHED mới đáng duyệt cây (cây video có hàng chục nghìn file)
            if (name + PUBLISHED_SUFFIX) in names:
                files = _walk_files(folder)
                if not any(f.lower().endswith(".mp4") for f in files):
                    ch.junk.append(OtherFolder(slug, name, folder, "junk", file_count=len(files)))
                    continue
            number = _video_number(name)
            if number is None:
                ch.other.append(OtherFolder(slug, name, folder, "other"))
                continue
            titles, meta = old_titles(folder)
            ch.videos.append(
                VideoItem(
                    channel_slug=slug,
                    number=number,
                    kind="old",
                    folder=folder,
                    name=name,
                    slug_title=_slug_title(name),
                    titles=titles,
                    mp4s=find_mp4s(folder),
                    thumbnails=find_thumbnails(folder),
                    meta_file=meta,
                )
            )
        except OSError:  # folder không đọc được: vào nhóm other, không làm hỏng lần quét
            ch.other.append(OtherFolder(slug, name, folder, "other"))
    ch.videos.sort(key=lambda v: (v.number, v.name))
    return ch


def scan(root: Path | str, runs_json: dict | None, yt_wf_root: Path | None = None) -> ScanResult:
    root = Path(root)
    result = ScanResult(root)
    runs = _runs_by_dir(runs_json)
    try:
        chans = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return result
    for cdir in chans:
        if cdir.name in config.UPLOAD_EXCLUDED_DIRS:
            continue
        result.channels[cdir.name] = scan_channel(root, cdir.name, runs, yt_wf_root)
    return result
