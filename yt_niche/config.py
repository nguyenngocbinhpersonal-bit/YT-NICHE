"""Shared configuration: reads .env and the constants used across the app."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "youtube.env")

ENV_PATH = ROOT_DIR / ".env"
SETTINGS_PATH = DATA_DIR / "settings.json"
RPM_TABLE_PATH = DATA_DIR / "rpm_table.csv"


def api_key() -> str:
    """Read at call time so a key just saved on the Settings page takes effect at once."""
    return (os.getenv("YOUTUBE_API_KEY") or os.getenv("YT_API_KEY") or "").strip()


def save_api_key(key: str) -> None:
    from dotenv import set_key

    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "YOUTUBE_API_KEY", key.strip(), quote_mode="never")
    os.environ["YOUTUBE_API_KEY"] = key.strip()


# AI (Settings → AI): OpenAI-compatible API; key in .env (COMMANDCODE_API_KEY), the rest stored in the DB (app_meta.ai_config).
AI_BASE_URL = "https://api.commandcode.ai/provider/v1"
AI_MAX_REQUESTS_PER_DAY = 200

# MCP (Claude Code drives the app, docs/NICHE_MAP_MCP_PLAN.md): YouTube units kept back for the UI;
# quota-spending tools refuse to run when they would eat into this reserve (unless force=true).
MCP_QUOTA_RESERVE = 1_500


# Markets: code → (relevanceLanguage, display name). This is only the default set (17 markets, 4 groups as in lesson 2.4);
# the real list lives in the `market` table in the DB, is edited under Settings → Markets, and is loaded into MARKETS /
# ENABLED_MARKETS / MARKET_GROUPS at startup (services/market_catalog.apply). MARKETS also holds disabled markets so old
# data can still be named; market pickers use ENABLED_MARKETS only.
MARKETS = {
    "US": ("en", "United States"),
    "GB": ("en", "United Kingdom"),
    "CA": ("en", "Canada"),
    "AU": ("en", "Australia"),
    "DE": ("de", "Germany"),
    "FR": ("fr", "France"),
    "JP": ("ja", "Japan"),
    "KR": ("ko", "South Korea"),
    "TW": ("zh-Hant", "Taiwan"),
    "VN": ("vi", "Vietnam"),
    "ID": ("id", "Indonesia"),
    "TH": ("th", "Thailand"),
    "PH": ("en", "Philippines"),
    "BR": ("pt", "Brazil"),
    "MX": ("es", "Mexico"),
    "AR": ("es", "Argentina"),
    "IN": ("hi", "India"),
}
MARKET_GROUPS = {
    "Tier 1 (high RPM)": ["US", "GB", "CA", "AU", "DE", "FR"],
    "East Asia": ["JP", "KR", "TW"],
    "Southeast Asia": ["VN", "ID", "TH", "PH"],
    "Latin America & India": ["BR", "MX", "AR", "IN"],
}
ENABLED_MARKETS = list(MARKETS)
DEFAULT_MARKETS = dict(MARKETS)
DEFAULT_MARKET_GROUPS = {k: list(v) for k, v in MARKET_GROUPS.items()}

# Default quota of one Google Cloud project for YouTube Data API v3.
YOUTUBE_DAILY_QUOTA = 10_000

# Unit cost per endpoint (YouTube Data API v3 documentation).
YOUTUBE_UNIT_COST = {
    "search": 100,
    "videos": 1,
    "channels": 1,
    "playlistItems": 1,
    "commentThreads": 1,
    "i18nRegions": 1,
    "i18nLanguages": 1,
}

# Format classification by duration (seconds).
SHORTS_MAX_SECONDS = 180
LONG_MIN_SECONDS = 240

DB_PATH = DATA_DIR / "yt_niche.db"

# Cache lifetimes (seconds).
TTL_SEARCH = 24 * 3600
TTL_VIDEOS = 6 * 3600
TTL_CHANNELS = 7 * 24 * 3600
TTL_CHANNEL_MEDIAN = 7 * 24 * 3600
TTL_TRENDS = 24 * 3600
TTL_SUGGEST = 24 * 3600
TTL_COMMENTS = 24 * 3600

# Number of parallel YouTube requests.
YOUTUBE_CONCURRENCY = 8

# YouTube quota resets at midnight Pacific time.
QUOTA_TIMEZONE = "America/Los_Angeles"

# ---- Scoring thresholds (lessons 2.1, 2.2). Items marked (*) are the app's own assumptions. ----

OUTLIER_OUTPERFORM = 3  # outperformer
OUTLIER_VIRAL = 10  # viral
OUTLIER_SUPER_VIRAL = 50  # super viral

SMALL_CHANNEL_MAX_SUBS = 100_000
BIG_CHANNEL_MIN_SUBS = 1_000_000
MIN_CHANNEL_SAMPLE = 5  # (*) minimum videos of the same format to trust a channel median
YOUNG_CHANNEL_MONTHS = 12

# Demand bar: log10(median views) from 3 (1K) → 0 points to 6 (1M) → 100 points.
DEMAND_LOG_RANGE = (3.0, 6.0)
# (*) Freshness bar when the time window is ≤ 30 days: log10(views/hour) from 0 (1/hour) to 3 (1,000/hour).
FRESHNESS_VPH_LOG_RANGE = (0.0, 3.0)
FRESHNESS_DAYS = 30

NICHE_WEIGHTS = {"demand": 0.35, "opportunity": 0.25, "freshness": 0.20, "competition": 0.20}
LABEL_GREEN_MIN = 70
LABEL_POTENTIAL_MIN = 50

CONFIDENCE_HIGH_MIN_N = 40
CONFIDENCE_HIGH_MAX_ESTIMATED = 0.30
CONFIDENCE_LOW_MAX_N = 15  # n < 15 → low
CONFIDENCE_LOW_MIN_ESTIMATED = 0.60  # > 60% estimated → low

SUPPLY_GOOD_OUTLIERS = 10
SUPPLY_GOOD_SMALL_CHANNELS = 5
SUPPLY_WEAK_SMALL_CHANNELS = 3  # below this → suggest digging a sub-niche

FORMAT_FIT_RATIO = 1.5  # (*) a format wins when its median is ≥ 1.5× the other
FORMAT_MIN_N = 5  # (*) each format needs at least 5 videos before comparing

# (*) Google Trends classification.
TRENDS_FAD_BASE_MAX = 10  # first 4 years average ≤ 10 ("almost zero")
TRENDS_FAD_RECENT_MIN = 20  # last 13 weeks average ≥ 20
TRENDS_FAD_RATIO = 3  # and ≥ 3× the base
TRENDS_SEASONAL_AUTOCORR = 0.5  # autocorrelation at a 52-week lag
TRENDS_FALLING_5Y = -0.30  # 5-year trend line falls ≥ 30% of the mean
TRENDS_SPARSE_ZERO_SHARE = 0.5  # > 50% of points are 0 → sparse data

# (*) Three-tier rating.
TIER_DEMAND_STRONG = 60
TIER_DEMAND_WEAK = 30
TIER_COMPETITION_WEAK = 60

# (*) RPM tiers (USD per 1,000 views) for the Money tier.
RPM_TIER_MEDIUM_MIN = 1.0
RPM_TIER_HIGH_MIN = 4.0

# (*) Judging the 10 test videos (lesson 2.5).
TEST_STANDOUT_RATIO = 2.0  # a video "clearly above the rest": views ≥ 2× the median of the test videos
TEST_CTR_LOW = 2.0  # CTR (%) below this is low (YouTube: half of all channels have a CTR of 2–10%)
TEST_RETENTION_LOW = 30.0  # % average view duration below this means viewers leave early
TEST_IMPRESSIONS_LOW = 500  # median impressions after 7 days below this is "almost none"

# ---- Sub-niches and long-tail keywords (lesson 2.3). Items marked (*) are the app's own assumptions. ----

SUBNICHE_MAX_LAYER = 3  # layer 4 and deeper is a video topic, not a niche
SUBNICHE_MIN_IDEAS = 30  # must be able to list 30 unique ideas
SUBNICHE_MIN_CHANNELS = 2  # (*) minimum different channels for a phrase to count as "repeated"
KEYWORD_LONG_MIN_WORDS = 4  # (*) long-tail keyword: 4 or more syllables / words (Vietnamese counts syllables)
KEYWORD_LONG_MIN_CHARS = 8  # (*) languages without spaces (Japanese, Korean, Thai, Chinese): 8 or more characters
KEYWORD_SAMPLE = 50  # number of YouTube results used to score one keyword (1 search page = 100 units)
KEYWORD_WINDOW_DAYS = 365  # (*) only videos published in the last year
KEYWORD_DEMAND_LOG_RANGE = (2.0, 5.0)  # (*) median result views: 100 → 0 points, 100K → 100 points
KEYWORD_BIG_CHANNEL_SUBS = 100_000  # (*) results from channels at this size and up are "big competitors"
KEYWORD_COMPETITION_WEIGHTS = {"big_channels": 0.5, "exact_title": 0.5}  # (*)
KEYWORD_LOW_DATA_N = 10  # (*) fewer than 10 results with views → "low data"
KEYWORD_VERY_GOOD = 70
KEYWORD_GOOD = 55
KEYWORD_AVERAGE = 40

# ---- Comparing niches and multi-market (lesson 2.4). ----

COMPARE_CLEAR_GAP = 10  # a gap of 10 points or more is a clear gap
COMPARE_TIE_GAP = 5  # less than 5 points counts as a tie
COMPARE_NOISE_GAP = 3  # a gap of 2–3 points is noise
COMPARE_SAMPLE_RATIO = 2.0  # (*) sample sizes 2× apart or more cannot be compared side by side
OPPORTUNITY_WEIGHTS = {"niche": 0.45, "search": 0.30, "money": 0.25}
OPPORTUNITY_WEIGHTS_NO_TRENDS = {"niche": 0.60, "money": 0.40}
OPPORTUNITY_GREEN_MIN = 65
OPPORTUNITY_RPM_LOG_RANGE = (-0.7, 1.0)  # (*) average RPM 0.2 USD → 0 points, 10 USD → 100 points

# ---- Tracking (lesson 2.5: re-dig the main niche every month to catch drop signals early). ----
REDIG_DAYS = 30


# ---------------------------------------------------------------------------
# Keyword radar: scan Google Trends categories spread over 24 hours
# ---------------------------------------------------------------------------
RADAR_INTERVAL_S = 240  # (*) starting gap between Trends ticks (±20%); then self-adjusting:
RADAR_MIN_INTERVAL_S = 60  # (*) fastest: 1 tick/minute
RADAR_MAX_INTERVAL_S = 1200  # slowest after repeated blocks: 20 minutes/tick
RADAR_SPEEDUP_EVERY = 10  # (*) every 10 ticks in a row without a block speed up by RADAR_SPEEDUP_PCT; a block (429) doubles the gap
RADAR_SPEEDUP_PCT = 20  # (*) % faster each time (gap between ticks × 0.8)
RADAR_EMPTY_RESCAN_DAYS = 7  # (*) market × category pairs with no data: rescan every 7 days
RADAR_BARREN_RESCAN_DAYS = 3  # (*) pairs scanned 3+ times without a passing keyword: every 3 days
RADAR_PER_SOURCE = 2  # (*) each category scan checks at most 2 new keywords
RADAR_MIN_RISING = 200  # (*) only consider "rising" keywords from +200%
RADAR_RESCAN_HOURS = 24  # each market × category pair is scanned at most once a day
RADAR_RECHECK_DAYS = 30  # a keyword whose Trends volume was checked is rechecked after 30 days
RADAR_YT_UNITS_PER_DAY = 1000  # (*) YouTube quota budget for scoring keywords (≈ 102 units/keyword)
RADAR_SPIKE_RECHECK_DAYS = 14  # a keyword that just spiked: recheck after 14 days if it is still listed
# Search volume of the last 30 days (daily) on YouTube Search and Google Search; missing on either source excludes it.
RADAR_30D_MAX_ZERO_SHARE = 0.5  # (*) more than half of the days at 0 → almost nobody searches it
RADAR_30D_MIN_AVG = 15  # (*) 30-day average below 15/100 (of the highest day) → low, on-and-off volume
RADAR_GOOD_MIN = 65  # (*) potential score from this level gets a ⭐
RADAR_WEIGHTS = {"momentum": 0.35, "durability": 0.25, "youtube": 0.25, "persistence": 0.15}  # (*)
RADAR_MOMENTUM_LOG_RANGE = (2.0, 4.0)  # log10 of % growth: 100% → 0 points, 10,000% → 100 points


# ---------------------------------------------------------------------------
# Similar videos & channels (like the Related feature of TubeBuddy / vidIQ)
# ---------------------------------------------------------------------------
TTL_INNERTUBE = 24 * 3600  # cache of YouTube's internal "Up next" / search pages
SIMILAR_SEED_VIDEOS = 4  # channel input: the 4 most viewed of the last 15 videos are the starting point
SIMILAR_MAX_QUERIES = 10  # (*) number of internal search queries (title + tags + autocomplete)
SIMILAR_ENRICH_CHANNELS = 60  # (*) candidate channels enriched through the API (≈ 3 units/channel when not cached)
SIMILAR_ENRICH_VIDEOS = 150  # (*) candidate videos with exact views/duration fetched (1 unit / 50 videos)
SIMILAR_RECENT_TITLES = 15  # most recent channel videos used for the content comparison
# (*) Weights: content similarity (text, normalized by the most similar candidate) and co-occurrence.
SIMILAR_CHANNEL_WEIGHTS = {"content": 0.6, "cooccur": 0.4}
SIMILAR_VIDEO_WEIGHTS = {"content": 0.65, "signals": 0.35}
SIMILAR_SIGNAL_CAP = 4  # (*) video signals: count at most 4 appearances
SIMILAR_MIN_CONTENT = 0.3  # (*) content similarity (vs the most similar candidate) below this is off-topic and dropped
# Co-occurrence signals: one appearance in "Up next" weighs twice as much as one search hit.
SIMILAR_SIGNAL_WEIGHTS = {"related": 2.0, "search": 1.0, "search_channel": 1.0}


# ---------------------------------------------------------------------------
# Trang Upload: dữ liệu video và pipeline sản xuất (cho phép ghi đè bằng biến môi trường)
# ---------------------------------------------------------------------------
SOCIAL_MEDIA_ROOT = Path(os.getenv("YT_NICHE_SOCIAL_MEDIA_ROOT") or r"D:\BUSINESS\SOCIAL_MEDIA")
YT_WF_ROOT = Path(os.getenv("YT_NICHE_YT_WF_ROOT") or r"D:\SOFTWARE\LOCAL_APP\YT_WF_001")
YT_WF_PYTHON = Path(os.getenv("YT_NICHE_YT_WF_PYTHON") or (YT_WF_ROOT / ".env" / "Scripts" / "python.exe"))
PIPECTL_PATH = Path(os.getenv("YT_NICHE_PIPECTL_PATH") or (YT_WF_ROOT / "shared" / "pipeline" / "pipectl.py"))
UPLOAD_EXCLUDED_DIRS = {"__PROJECT-OLD"}
