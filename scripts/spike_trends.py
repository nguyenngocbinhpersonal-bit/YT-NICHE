"""Spike 0: check whether pytrends can fetch Google Trends from this machine.

Tries 3 time ranges (5 years, 12 months, 3 months) × 2 sources (Web, YouTube).

Example:
    .venv/Scripts/python scripts/spike_trends.py "vietnamese history stories" --geo VN
"""

from __future__ import annotations

import argparse
import sys
import time

from pytrends_modern.request import TrendReq

TIMEFRAMES = ["today 5-y", "today 12-m", "today 3-m"]
SOURCES = {"web": "", "youtube": "youtube"}


def fetch_with_retry(trends: TrendReq, topic: str, tf: str, geo: str, gprop: str, attempts: int = 3):
    """Retries on 429 (Google rate limit), waiting 5s, 10s, ..."""
    for attempt in range(1, attempts + 1):
        try:
            trends.build_payload([topic], timeframe=tf, geo=geo, gprop=gprop)
            return trends.interest_over_time()
        except Exception as e:
            if "429" not in str(e) or attempt == attempts:
                raise
            print(f"         429, retry {attempt + 1} in {5 * attempt}s...")
            time.sleep(5 * attempt)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic")
    parser.add_argument("--geo", default="VN")
    parser.add_argument("--hl", default="vi-VN")
    parser.add_argument("--pause", type=float, default=2.0, help="pause between requests (seconds)")
    args = parser.parse_args()

    try:
        # Do not use pytrends' retries: version 4.9.2 passes method_whitelist, which urllib3 2.x dropped.
        trends = TrendReq(hl=args.hl, tz=-420, timeout=(10, 30))
    except Exception as e:  # pytrends fails right away while fetching the cookie
        sys.exit(f"[!] Could not initialise pytrends: {type(e).__name__}: {e}")

    print(f"\n=== Trends spike: '{args.topic}' | geo={args.geo} ===\n")
    ok = fail = 0
    for source_name, gprop in SOURCES.items():
        for tf in TIMEFRAMES:
            t0 = time.perf_counter()
            try:
                df = fetch_with_retry(trends, args.topic, tf, args.geo, gprop)
                secs = time.perf_counter() - t0
                if df.empty:
                    print(f"  [empty] {source_name:<8} {tf:<11} {secs:5.1f}s  (not enough search volume)")
                    fail += 1
                else:
                    s = df[args.topic]
                    print(
                        f"  [OK]    {source_name:<8} {tf:<11} {secs:5.1f}s  "
                        f"{len(s):>3} points  min={s.min():>3} max={s.max():>3} "
                        f"first={s.iloc[: len(s) // 4].mean():5.1f} last={s.iloc[-len(s) // 4 :].mean():5.1f}"
                    )
                    ok += 1
            except Exception as e:
                secs = time.perf_counter() - t0
                print(f"  [ERROR] {source_name:<8} {tf:<11} {secs:5.1f}s  {type(e).__name__}: {str(e)[:120]}")
                fail += 1
            time.sleep(args.pause)

    print(f"\nResult: {ok} succeeded / {fail} failed")


if __name__ == "__main__":
    main()
