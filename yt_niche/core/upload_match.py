"""Khớp video local (folder) với video trên kênh YouTube theo tiêu đề. Hàm thuần, không IO.

Mạnh = tiêu đề chuẩn hoá bằng nhau với một tiêu đề local. Yếu = gần giống (SequenceMatcher) hoặc khớp theo slug folder.
Chỉ đề xuất; việc ghi local luôn do owner xác nhận.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

STRONG = "strong"
WEAK = "weak"

# Ước lượng từ lần đo trên kênh thật (đề xuất sai của đối thủ 0,60-0,69; tiêu đề sửa nhẹ 0,84-0,94). Xem lại ở điểm dừng.
WEAK_RATIO = 0.75

_DASHES = dict.fromkeys(map(ord, "—–‒―−‐‑"), "-")


@dataclass(frozen=True)
class MatchItem:
    channel_slug: str
    number: int
    kind: str  # pipeline | old
    titles: tuple[str, ...] = ()
    slug_title: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.channel_slug, self.number)


@dataclass(frozen=True)
class RemoteVideo:
    video_id: str
    channel_id: str
    title: str
    published_at: str | None = None


@dataclass(frozen=True)
class Proposal:
    channel_slug: str
    number: int
    video_id: str
    strength: str  # strong | weak
    score: float
    reason: str  # title | similar | slug | ambiguous


@dataclass
class ProposeResult:
    proposals: list[Proposal]
    unmatched_remotes: list[RemoteVideo]  # video trên kênh chưa thuộc item nào


@dataclass(frozen=True)
class CrossWarning:
    channel_slug: str  # kênh local có tiêu đề này
    number: int
    other_slug: str  # kênh local khác mà video remote đang nằm
    video_id: str
    title: str


def norm(title: str) -> str:
    """NFKC, casefold, '-' và '—' coi như nhau, chỉ giữ token chữ/số, nối bằng một dấu cách."""
    s = unicodedata.normalize("NFKC", title or "").translate(_DASHES).casefold()
    return " ".join(re.findall(r"\w+", s))


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def norm_slug(s: str) -> str:
    return norm(_strip_accents(s or ""))


def _pair(item: MatchItem, remote: RemoteVideo) -> tuple[str, float, str] | None:
    """(strength, score, reason) hoặc None nếu không khớp."""
    rn = norm(remote.title)
    if not rn:
        return None
    best, strong = 0.0, False
    for t in item.titles:
        tn = norm(t)
        if not tn:
            continue
        if tn == rn:
            strong = True
            best = 1.0
            break
        best = max(best, SequenceMatcher(None, tn, rn).ratio())
    if strong:
        return (STRONG, 1.0, "title")
    if best >= WEAK_RATIO:
        return (WEAK, best, "similar")
    sn = norm_slug(item.slug_title)
    if sn:
        r_plain = norm_slug(remote.title)
        ratio = SequenceMatcher(None, sn, r_plain).ratio()
        stoks, rtoks = set(sn.split()), set(r_plain.split())
        if ratio >= WEAK_RATIO or (len(stoks) >= 2 and stoks <= rtoks):
            return (WEAK, max(ratio, 0.5), "slug")  # slug luôn yếu
    return None


def propose(
    items: list[MatchItem],
    remotes: list[RemoteVideo],
    rejected: set[tuple[str, int, str]] | frozenset = frozenset(),
) -> ProposeResult:
    """Đề xuất cặp (item, video remote). Mỗi video remote chỉ gán cho một item."""
    cand: list[tuple[MatchItem, RemoteVideo, str, float, str]] = []
    for it in items:
        for rv in remotes:
            if (it.channel_slug, it.number, rv.video_id) in rejected:
                continue
            m = _pair(it, rv)
            if m:
                cand.append((it, rv, *m))

    # Mạnh mơ hồ (nhiều item cùng khớp một video, hoặc một item khớp nhiều video) → hạ thành yếu.
    per_remote: dict[str, int] = {}
    per_item: dict[tuple[str, int], int] = {}
    for it, rv, st, _, _ in cand:
        if st == STRONG:
            per_remote[rv.video_id] = per_remote.get(rv.video_id, 0) + 1
            per_item[it.key] = per_item.get(it.key, 0) + 1
    fixed = []
    for it, rv, st, sc, why in cand:
        if st == STRONG and (per_remote[rv.video_id] > 1 or per_item[it.key] > 1):
            st, sc, why = WEAK, min(sc, 0.99), "ambiguous"
        fixed.append((it, rv, st, sc, why))

    # Mỗi video remote giữ cặp tốt nhất.
    best: dict[str, tuple] = {}
    for c in fixed:
        it, rv, st, sc, _ = c
        rank = (st == STRONG, sc)
        cur = best.get(rv.video_id)
        if cur is None or rank > (cur[2] == STRONG, cur[3]):
            best[rv.video_id] = c
    proposals = [
        Proposal(it.channel_slug, it.number, rv.video_id, st, sc, why) for it, rv, st, sc, why in best.values()
    ]
    proposals.sort(key=lambda p: (p.channel_slug, p.number, p.strength != STRONG, -p.score, p.video_id))
    matched = {p.video_id for p in proposals}
    return ProposeResult(proposals, [rv for rv in remotes if rv.video_id not in matched])


def cross_channel(
    items_by_channel: dict[str, list[MatchItem]],
    remotes_by_channel: dict[str, list[RemoteVideo]],
) -> list[CrossWarning]:
    """Tiêu đề của kênh A khớp mạnh với video đang nằm trên kênh B (B ≠ A) → cảnh báo đỏ."""
    out: list[CrossWarning] = []
    for slug_a, items in items_by_channel.items():
        for slug_b, remotes in remotes_by_channel.items():
            if slug_a == slug_b:
                continue
            by_norm: dict[str, RemoteVideo] = {}
            for rv in remotes:
                n = norm(rv.title)
                if n:
                    by_norm.setdefault(n, rv)
            for it in items:
                for t in it.titles:
                    rv = by_norm.get(norm(t))
                    if rv is not None:
                        out.append(CrossWarning(slug_a, it.number, slug_b, rv.video_id, rv.title))
                        break
    return out
