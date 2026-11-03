"""Spike mốc 0: kiểm tra pytrends có lấy được Google Trends từ máy này không.

Thử 3 khung thời gian (5 năm, 12 tháng, 3 tháng) × 2 nguồn (Web, YouTube).

Ví dụ:
    .venv/Scripts/python scripts/spike_trends.py "kể chuyện lịch sử" --geo VN
"""

from __future__ import annotations

import argparse
import sys
import time

from pytrends_modern.request import TrendReq

TIMEFRAMES = ["today 5-y", "today 12-m", "today 3-m"]
SOURCES = {"web": "", "youtube": "youtube"}


def fetch_with_retry(trends: TrendReq, topic: str, tf: str, geo: str, gprop: str, attempts: int = 3):
    """Tự retry khi bị 429 (Google giới hạn tốc độ), chờ 5s, 10s, ..."""
    for attempt in range(1, attempts + 1):
        try:
            trends.build_payload([topic], timeframe=tf, geo=geo, gprop=gprop)
            return trends.interest_over_time()
        except Exception as e:
            if "429" not in str(e) or attempt == attempts:
                raise
            print(f"         429, thử lại lần {attempt + 1} sau {5 * attempt}s...")
            time.sleep(5 * attempt)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic")
    parser.add_argument("--geo", default="VN")
    parser.add_argument("--hl", default="vi-VN")
    parser.add_argument("--pause", type=float, default=2.0, help="nghỉ giữa các request (giây)")
    args = parser.parse_args()

    try:
        # Không dùng retries của pytrends: bản 4.9.2 truyền method_whitelist, urllib3 2.x đã bỏ tham số này.
        trends = TrendReq(hl=args.hl, tz=-420, timeout=(10, 30))
    except Exception as e:  # pytrends lỗi ngay khi lấy cookie
        sys.exit(f"[!] Không khởi tạo được pytrends: {type(e).__name__}: {e}")

    print(f"\n=== Trends spike: '{args.topic}' | geo={args.geo} ===\n")
    ok = fail = 0
    for source_name, gprop in SOURCES.items():
        for tf in TIMEFRAMES:
            t0 = time.perf_counter()
            try:
                df = fetch_with_retry(trends, args.topic, tf, args.geo, gprop)
                secs = time.perf_counter() - t0
                if df.empty:
                    print(f"  [rỗng] {source_name:<8} {tf:<11} {secs:5.1f}s  (không đủ lượng tìm kiếm)")
                    fail += 1
                else:
                    s = df[args.topic]
                    print(
                        f"  [OK]   {source_name:<8} {tf:<11} {secs:5.1f}s  "
                        f"{len(s):>3} điểm  min={s.min():>3} max={s.max():>3} "
                        f"đầu={s.iloc[: len(s) // 4].mean():5.1f} cuối={s.iloc[-len(s) // 4 :].mean():5.1f}"
                    )
                    ok += 1
            except Exception as e:
                secs = time.perf_counter() - t0
                print(f"  [LỖI]  {source_name:<8} {tf:<11} {secs:5.1f}s  {type(e).__name__}: {str(e)[:120]}")
                fail += 1
            time.sleep(args.pause)

    print(f"\nKết quả: {ok} thành công / {fail} thất bại")


if __name__ == "__main__":
    main()
