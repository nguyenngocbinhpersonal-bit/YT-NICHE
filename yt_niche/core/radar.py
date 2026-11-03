"""Radar từ khoá: chọn danh mục Google Trends, loại xu hướng ngắn hạn, chấm điểm tiềm năng.

Phễu: truy vấn của danh mục (miễn phí) → bỏ thực thể ngắn hạn (người, bài hát, show, sự kiện) theo loại thực thể
của Google → bỏ từ khoá gần như không ai tìm trong 30 ngày gần nhất trên YouTube Search hoặc Google Search
→ chấm YouTube cho vài từ tốt nhất.
"""

from __future__ import annotations

import re
import unicodedata
from yt_niche import config as cfg
from yt_niche.core.stats import log_scale

# Danh mục ngắn hạn theo cây danh mục chính thức của Google Trends (id không đổi giữa các ngôn ngữ).
EXCLUDED_CATEGORIES = {
    16: "News",
    112: "Broadcast & Network News",
    184: "Celebrities & Entertainment News",
    507: "Gossip & Tabloid News",
    396: "Politics",
    398: "Campaigns & Elections",
    1077: "Sports News",
    63: "Weather",
    1209: "World News",
    572: "Local News",
    408: "Newspapers",
    784: "Business News",
    785: "Technology News",
    1253: "Health News",
    569: "Events & Listings",
    1273: "Live Sporting Events",
    1085: "Movie Listings & Theater Showtimes",
    891: "Concerts & Music Festivals",
    358: "TV Shows & Programs",
}
# Nhóm gốc bị chi phối bởi tin ngắn hạn: quét theo danh mục con (bỏ con ngắn hạn) thay vì cả nhóm.
SPLIT_ROOTS = {3}  # Arts & Entertainment

# Loại thực thể của Google cho thấy truy vấn là người, tác phẩm mới, đội / giải đấu, sự kiện.
SHORT_TERM_TYPES = re.compile(
    r"singer|rapper|musician|band|group|song|album|single|actor|actress|film|movie|tv |television|series|show|"
    r"episode|politician|president|minister|player|footballer|athlete|team|club|league|match|tournament|cup|"
    r"election|event|storm|hurricane|typhoon|earthquake|celebrity|model|influencer|youtuber|streamer|comedian|"
    r"personality|presenter|host|journalist|businessperson|entrepreneur|director|producer|k-pop|idol",
    re.I,
)

# Từ khoá vô ích cho kênh YouTube dù bền (xổ số, thời tiết...). Người dùng thêm được ở trang Radar.
DEFAULT_BLOCKLIST = ["xổ số", "xo so", "lottery", "lotto", "thời tiết", "weather", "karaoke", "login", "đăng nhập"]

PENDING, PASSED, EXCLUDED, IGNORED, WATCHING = "pending", "passed", "excluded", "ignored", "watching"
STATUS_LABEL = {
    PENDING: "chờ kiểm tra",
    PASSED: "đạt",
    EXCLUDED: "đã loại",
    IGNORED: "bỏ qua",
    WATCHING: "theo dõi",
}


def fold(text: str) -> str:
    """Bỏ dấu, chữ thường, gom khoảng trắng: 'Nguyễn Sỹ Cương' → 'nguyen sy cuong'."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


# ---------------------------------------------------------------------------
# Danh mục
# ---------------------------------------------------------------------------


def flatten(tree: dict) -> list[dict]:
    """[{id, name, depth, parent}] theo thứ tự cây; id lặp ở nhiều nhánh chỉ giữ lần đầu."""
    out, seen = [], set()

    def walk(node, depth, parent):
        if node["id"] not in seen:
            seen.add(node["id"])
            out.append({"id": node["id"], "name": node["name"], "depth": depth, "parent": parent})
        for child in node.get("children", []):
            walk(child, depth + 1, node["id"])

    for child in tree.get("children", []):
        walk(child, 1, 0)
    return out


def default_categories(tree: dict) -> list[int]:
    """Nhóm gốc trừ Tin tức; riêng Nghệ thuật & Giải trí thì lấy danh mục con trừ con ngắn hạn."""
    flat = flatten(tree)
    roots = [c for c in flat if c["depth"] == 1 and c["id"] not in EXCLUDED_CATEGORIES]
    out = [c["id"] for c in roots if c["id"] not in SPLIT_ROOTS]
    out += [c["id"] for c in flat if c["depth"] == 2 and c["parent"] in SPLIT_ROOTS and c["id"] not in EXCLUDED_CATEGORIES]
    return out


def selectable_categories(tree: dict, max_depth: int = 2) -> list[dict]:
    return [c for c in flatten(tree) if c["depth"] <= max_depth and c["id"] not in EXCLUDED_CATEGORIES]


# ---------------------------------------------------------------------------
# Chọn ứng viên từ một lần quét danh mục
# ---------------------------------------------------------------------------


def blocked(query: str, blocklist: list[str]) -> bool:
    q = f" {fold(query)} "
    return any(f" {fold(b)} " in q for b in blocklist if b.strip())


def pick_new(found: dict[str, list[tuple[str, int]]], known: set[str], blocklist: list[str], limit: int) -> list[dict]:
    """Từ khoá mới đáng kiểm tra: đang tăng từ RADAR_MIN_RISING%, xếp theo % tăng; thiếu thì lấy thêm từ hàng đầu."""
    # Có trong cả "hàng đầu" lẫn "đang tăng" = vừa nhiều người tìm vừa tăng → kiểm tra trước. Chỉ có ở "đang tăng"
    # thường là từ gần như không ai tìm (tăng từ 0), dễ bị loại vì "quá ít người tìm".
    in_top = {fold(q) for q, _ in found.get("top", [])}
    rising = sorted(
        ((q, v) for q, v in found.get("rising", []) if v >= cfg.RADAR_MIN_RISING),
        key=lambda x: (fold(x[0]) in in_top, x[1]), reverse=True,
    )
    out, seen = [], set()
    for kind, items in (("rising", rising), ("top", found.get("top", []))):
        for q, v in items:
            key = fold(q)
            if len(out) >= limit:
                return out
            if not key or key in known or key in seen or blocked(q, blocklist):
                continue
            seen.add(key)
            out.append({"keyword": " ".join(q.split()), "kind": kind, "value": v})
    return out


# ---------------------------------------------------------------------------
# Kiểm tra: loại thực thể và lượng tìm 30 ngày
# ---------------------------------------------------------------------------


def short_term_entity(query: str, entities: list[dict]) -> dict | None:
    """Thực thể ngắn hạn mà truy vấn đang nói tới (tiêu đề khớp truy vấn và loại là người / tác phẩm / sự kiện)."""
    q = fold(query)
    for e in entities:
        title = fold(e.get("title", ""))
        if title and (title in q or q in title) and SHORT_TERM_TYPES.search(e.get("type", "")):
            return e
    return None


SOURCE_LABEL = {"youtube": "YouTube Search", "web": "Google Search"}


def volume_30d(points: list) -> dict:
    """Lượng tìm 30 ngày của một nguồn. Trends chấm 0–100 so với ngày cao nhất trong khoảng: trung bình thấp hoặc
    nhiều ngày bằng 0 nghĩa là ít người tìm (Google làm tròn về 0 khi quá ít), lúc có lúc không."""
    values = [v for _, v in points]
    if not any(values):
        return {"ok": False, "avg": 0.0, "zeros": 1.0, "change": 0.0}
    avg = sum(values) / len(values)
    zeros = sum(v == 0 for v in values) / len(values)
    half = len(values) // 2
    first, last = values[:half] or values, values[half:]
    change = (sum(last) / len(last) - sum(first) / len(first)) / max(avg, 1)  # nửa sau so với nửa đầu
    ok = zeros <= cfg.RADAR_30D_MAX_ZERO_SHARE and avg >= cfg.RADAR_30D_MIN_AVG
    return {"ok": ok, "avg": round(avg, 1), "zeros": round(zeros, 2), "change": round(max(-1.0, min(1.0, change)), 2)}


def judge_volume(stats: dict[str, dict]) -> dict:
    """stats: {"youtube": volume_30d, "web": volume_30d} (nguồn trước đã loại thì không có nguồn sau).
    {"ok", "reason", "volume", "durability" 0–100, "trend_30d"}: thiếu lượng tìm ở nguồn nào cũng loại."""
    for source, s in stats.items():
        if s["ok"]:
            continue
        label = SOURCE_LABEL[source]
        if not s["avg"]:
            reason = f"{label}: không có dữ liệu 30 ngày gần nhất (gần như không ai tìm ở thị trường này)"
        elif s["zeros"] > cfg.RADAR_30D_MAX_ZERO_SHARE:
            reason = f"{label}: {s['zeros']:.0%} số ngày trong 30 ngày bằng 0 (quá ít người tìm)"
        else:
            reason = f"{label}: trung bình 30 ngày chỉ {s['avg']:.0f}/100 so với ngày cao nhất (lượng tìm thấp, lúc có lúc không)"
        return {"ok": False, "reason": reason, "volume": stats}
    avg = sum(s["avg"] for s in stats.values()) / len(stats)
    trend = stats.get("web", next(iter(stats.values())))["change"]
    reason = "; ".join(
        f"{SOURCE_LABEL[k]} trung bình {s['avg']:.0f}/100, {1 - s['zeros']:.0%} số ngày có người tìm" for k, s in stats.items()
    )
    # Độ đều: trung bình 50/100 trở lên (không dồn vào vài ngày) = 100 điểm.
    return {"ok": True, "reason": reason, "volume": stats, "durability": round(min(100.0, 2 * avg), 1), "trend_30d": trend}


# ---------------------------------------------------------------------------
# Điểm tiềm năng
# ---------------------------------------------------------------------------


def momentum(kind: str, value: int, trend_12m: float | None) -> float:
    if kind == "rising":
        return log_scale(max(value, 1), *cfg.RADAR_MOMENTUM_LOG_RANGE)
    return max(0.0, min(100.0, 50 + 100 * (trend_12m or 0)))  # truy vấn hàng đầu: theo xu hướng gần đây


def persistence(seen_days: int) -> float:
    return min(100.0, 25.0 * (seen_days - 1))


def potential(row: dict) -> float | None:
    """Điểm 0–100 cho từ khoá đã qua lọc lượng tìm; chưa chấm YouTube thì chia lại trọng số cho 3 phần còn lại."""
    shape = row.get("trend") or {}
    if row.get("status") == EXCLUDED or not shape.get("ok"):
        return None
    parts = {
        # trend_12m: từ khoá kiểm theo hình dạng 5 năm trước đây (đến 10/2026)
        "momentum": momentum(row["kind"], row["value"], shape.get("trend_30d", shape.get("trend_12m"))),
        "durability": shape["durability"],
        "persistence": persistence(row["seen_days"]),
    }
    if row.get("yt_score") is not None:
        parts["youtube"] = row["yt_score"]
    weights = {k: w for k, w in cfg.RADAR_WEIGHTS.items() if k in parts}
    return round(sum(parts[k] * w for k, w in weights.items()) / sum(weights.values()), 1)
