"""Thu thập dữ liệu thô cho một ngách và in tóm tắt quota / cache.

Ví dụ:
    .venv/Scripts/python scripts/fetch_niche.py "kể chuyện lịch sử"
    .venv/Scripts/python scripts/fetch_niche.py "psychology" --region US --lang en --format long --no-trends
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yt_niche.config import DATA_DIR, YOUTUBE_DAILY_QUOTA  # noqa: E402
from yt_niche.db import quota  # noqa: E402
from yt_niche.db.connection import connect  # noqa: E402
from yt_niche.services.fetch import DigParams, fetch_niche  # noqa: E402
from yt_niche.sources.youtube import YouTubeError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("topic")
    parser.add_argument("--region", default="VN")
    parser.add_argument("--lang", default="vi")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--format", choices=["any", "long", "shorts"], default="any")
    parser.add_argument("--order", choices=["relevance", "viewCount"], default="relevance")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--no-trends", action="store_true")
    parser.add_argument("--trends-source", choices=["youtube", "web"], default="youtube")
    args = parser.parse_args()

    params = DigParams(
        topic=args.topic,
        region=args.region,
        language=args.lang,
        window_days=args.days,
        fmt=args.format,
        order=args.order,
        pages=args.pages,
        with_trends=not args.no_trends,
        trends_source=args.trends_source,
    )
    conn = connect()
    try:
        data = asyncio.run(fetch_niche(conn, params))
    except YouTubeError as e:
        sys.exit(f"[!] {e}")

    q = data["quota"]
    videos, channels, trends = data["videos"], data["channels"], data["trends"]
    print(f"\n=== '{args.topic}' | {args.region}/{args.lang} | {args.days} ngày | {args.format} ===")
    print(f"Thời gian: {data['elapsed_s']}s")
    print(f"Quota lần này: {q['units']} unit  {q['by_endpoint']}")
    print(f"Đọc từ cache: {q['cache_hits']}")
    used = quota.used_on(conn)
    print(f"Quota hôm nay (giờ PT): {used}/{YOUTUBE_DAILY_QUOTA} — còn {YOUTUBE_DAILY_QUOTA - used}")

    by_fmt = {f: sum(v["fmt"] == f for v in videos) for f in ("long", "mid", "shorts")}
    print(f"\nVideo: {len(videos)} {by_fmt}")
    print(
        f"Kênh: {len(channels)} | có trung vị dài: {sum(c['median_long'] is not None for c in channels)}"
        f" | có trung vị Shorts: {sum(c['median_short'] is not None for c in channels)}"
        f" | ẩn sub: {sum(c['subs_hidden'] for c in channels)}"
    )
    both = [c for c in channels if c["median_long"] and c["median_short"]]
    if both:
        ratio = statistics.median(c["median_long"] / c["median_short"] for c in both)
        print(f"Kênh làm cả 2 định dạng: {len(both)} | trung vị (median dài / median Shorts): {ratio:.2f}×")

    if trends:
        status = "cache" if trends.get("cached") else "mới"
        lens = {k: len(v) for k, v in trends["series"].items()}
        print(f"Trends ({trends['source']}, {status}): điểm dữ liệu {lens} | lỗi: {trends['errors'] or 'không'}")

    out_dir = DATA_DIR / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"fetch_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nĐã lưu: {out_file}")


if __name__ == "__main__":
    main()
