"""Fake data for the engine tests."""

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def video(vid="v", channel="C", views=1000, fmt="long", days_ago=5):
    return {
        "video_id": vid,
        "channel_id": channel,
        "title": vid,
        "published_at": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": 600 if fmt == "long" else 60,
        "fmt": fmt,
        "views": views,
    }


def channel(cid="C", subs=5000, median_long=1000, n_long=20, median_short=None, n_short=0, created="2026-01-01T00:00:00Z"):
    return {
        "channel_id": cid,
        "title": cid,
        "subs": subs,
        "subs_hidden": 0 if subs is not None else 1,
        "created_at": created,
        "video_count": 30,
        "median_long": median_long,
        "n_long": n_long,
        "median_short": median_short,
        "n_short": n_short,
    }


def enriched(views=1000, subs=5000, outlier=None, level=None, fmt="long", channel="C", days_ago=5, estimated=False):
    v = video(channel=channel, views=views, fmt=fmt, days_ago=days_ago)
    return {**v, "subs": subs, "channel_median": 1000, "outlier": outlier, "level": level, "estimated": estimated}


def weekly(values, start="2021-10-17"):
    d = datetime.fromisoformat(start)
    return [[(d + timedelta(weeks=i)).strftime("%Y-%m-%d"), v] for i, v in enumerate(values)]
