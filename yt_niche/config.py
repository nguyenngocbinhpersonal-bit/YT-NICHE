"""Cấu hình chung: đọc .env và các hằng số dùng nhiều nơi."""

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
    """Đọc lúc gọi để key vừa lưu ở trang Cài đặt có hiệu lực ngay."""
    return (os.getenv("YOUTUBE_API_KEY") or os.getenv("YT_API_KEY") or "").strip()


def save_api_key(key: str) -> None:
    from dotenv import set_key

    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "YOUTUBE_API_KEY", key.strip(), quote_mode="never")
    os.environ["YOUTUBE_API_KEY"] = key.strip()


# AI (Cài đặt → AI): API chuẩn OpenAI; key ở .env (COMMANDCODE_API_KEY), phần còn lại lưu trong DB (app_meta.ai_config).
AI_BASE_URL = "https://api.commandcode.ai/provider/v1"
AI_MAX_REQUESTS_PER_DAY = 200

# MCP (Claude Code điều khiển app, docs/NICHE_MAP_MCP_PLAN.md): số unit YouTube giữ lại cho giao diện;
# tool tốn quota từ chối chạy nếu sẽ ăn vào phần này (trừ khi force=true).
MCP_QUOTA_RESERVE = 1_500


# Thị trường: mã → (relevanceLanguage, tên hiển thị). Đây chỉ là bộ mặc định (17 thị trường, 4 nhóm như bài 2.4);
# danh sách thật nằm ở bảng `market` trong DB, sửa ở Cài đặt → Thị trường, và được nạp vào MARKETS / ENABLED_MARKETS /
# MARKET_GROUPS lúc khởi động (services/market_catalog.apply). MARKETS gồm cả thị trường đã tắt để tra tên dữ liệu cũ;
# các ô chọn thị trường chỉ dùng ENABLED_MARKETS.
MARKETS = {
    "US": ("en", "Mỹ"),
    "GB": ("en", "Anh"),
    "CA": ("en", "Canada"),
    "AU": ("en", "Úc"),
    "DE": ("de", "Đức"),
    "FR": ("fr", "Pháp"),
    "JP": ("ja", "Nhật Bản"),
    "KR": ("ko", "Hàn Quốc"),
    "TW": ("zh-Hant", "Đài Loan"),
    "VN": ("vi", "Việt Nam"),
    "ID": ("id", "Indonesia"),
    "TH": ("th", "Thái Lan"),
    "PH": ("en", "Philippines"),
    "BR": ("pt", "Brazil"),
    "MX": ("es", "Mexico"),
    "AR": ("es", "Argentina"),
    "IN": ("hi", "Ấn Độ"),
}
MARKET_GROUPS = {
    "Tier 1 (RPM cao)": ["US", "GB", "CA", "AU", "DE", "FR"],
    "Châu Á": ["JP", "KR", "TW"],
    "Đông Nam Á": ["VN", "ID", "TH", "PH"],
    "Mỹ Latin & Ấn Độ": ["BR", "MX", "AR", "IN"],
}
ENABLED_MARKETS = list(MARKETS)
DEFAULT_MARKETS = dict(MARKETS)
DEFAULT_MARKET_GROUPS = {k: list(v) for k, v in MARKET_GROUPS.items()}

# Quota mặc định của một project Google Cloud cho YouTube Data API v3.
YOUTUBE_DAILY_QUOTA = 10_000

# Chi phí unit của từng endpoint (theo tài liệu YouTube Data API v3).
YOUTUBE_UNIT_COST = {
    "search": 100,
    "videos": 1,
    "channels": 1,
    "playlistItems": 1,
    "commentThreads": 1,
    "i18nRegions": 1,
    "i18nLanguages": 1,
}

# Phân loại định dạng theo thời lượng (giây).
SHORTS_MAX_SECONDS = 180
LONG_MIN_SECONDS = 240

DB_PATH = DATA_DIR / "yt_niche.db"

# Thời gian sống của cache (giây).
TTL_SEARCH = 24 * 3600
TTL_VIDEOS = 6 * 3600
TTL_CHANNELS = 7 * 24 * 3600
TTL_CHANNEL_MEDIAN = 7 * 24 * 3600
TTL_TRENDS = 24 * 3600
TTL_SUGGEST = 24 * 3600
TTL_COMMENTS = 24 * 3600

# Số request YouTube chạy song song.
YOUTUBE_CONCURRENCY = 8

# Quota YouTube reset lúc 0h giờ Thái Bình Dương.
QUOTA_TIMEZONE = "America/Los_Angeles"

# ---- Ngưỡng tính điểm (bài 2.1, 2.2). Mục đánh dấu (*) là giả định của app. ----

OUTLIER_OUTPERFORM = 3  # vượt trội
OUTLIER_VIRAL = 10  # nổ view
OUTLIER_SUPER_VIRAL = 50  # siêu viral

SMALL_CHANNEL_MAX_SUBS = 100_000
BIG_CHANNEL_MIN_SUBS = 1_000_000
MIN_CHANNEL_SAMPLE = 5  # (*) số video tối thiểu cùng định dạng để tin trung vị kênh
YOUNG_CHANNEL_MONTHS = 12

# Thanh Nhu cầu: log10(trung vị view) từ 3 (1K) → 0 điểm tới 6 (1M) → 100 điểm.
DEMAND_LOG_RANGE = (3.0, 6.0)
# (*) Thanh Độ mới khi khoảng thời gian ≤ 30 ngày: log10(view/giờ) từ 0 (1/giờ) tới 3 (1.000/giờ).
FRESHNESS_VPH_LOG_RANGE = (0.0, 3.0)
FRESHNESS_DAYS = 30

NICHE_WEIGHTS = {"demand": 0.35, "opportunity": 0.25, "freshness": 0.20, "competition": 0.20}
LABEL_GREEN_MIN = 70
LABEL_POTENTIAL_MIN = 50

CONFIDENCE_HIGH_MIN_N = 40
CONFIDENCE_HIGH_MAX_ESTIMATED = 0.30
CONFIDENCE_LOW_MAX_N = 15  # n < 15 → thấp
CONFIDENCE_LOW_MIN_ESTIMATED = 0.60  # > 60% ước tính → thấp

SUPPLY_GOOD_OUTLIERS = 10
SUPPLY_GOOD_SMALL_CHANNELS = 5
SUPPLY_WEAK_SMALL_CHANNELS = 3  # dưới mức này → gợi ý đào ngách con

FORMAT_FIT_RATIO = 1.5  # (*) một định dạng thắng khi trung vị gấp ≥ 1,5 lần
FORMAT_MIN_N = 5  # (*) mỗi định dạng cần ít nhất 5 video mới so

# (*) Phân loại Google Trends.
TRENDS_FAD_BASE_MAX = 10  # 4 năm đầu trung bình ≤ 10 ("gần như bằng 0")
TRENDS_FAD_RECENT_MIN = 20  # 13 tuần gần nhất trung bình ≥ 20
TRENDS_FAD_RATIO = 3  # và gấp ≥ 3 lần nền
TRENDS_SEASONAL_AUTOCORR = 0.5  # tự tương quan độ trễ 52 tuần
TRENDS_FALLING_5Y = -0.30  # đường xu hướng 5 năm giảm ≥ 30% so với trung bình
TRENDS_SPARSE_ZERO_SHARE = 0.5  # > 50% điểm bằng 0 → dữ liệu thưa

# (*) Chấm 3 tầng.
TIER_DEMAND_STRONG = 60
TIER_DEMAND_WEAK = 30
TIER_COMPETITION_WEAK = 60

# (*) Bậc RPM (USD / 1.000 view) cho tầng Tiền.
RPM_TIER_MEDIUM_MIN = 1.0
RPM_TIER_HIGH_MIN = 4.0

# (*) Đánh giá 10 video thử (bài 2.5).
TEST_STANDOUT_RATIO = 2.0  # video "vượt rõ mức chung": lượt xem ≥ 2 lần trung vị các video thử
TEST_CTR_LOW = 2.0  # CTR (%) dưới mức này là thấp (YouTube: nửa số kênh có CTR 2–10%)
TEST_RETENTION_LOW = 30.0  # % thời lượng xem trung bình dưới mức này là người xem rời sớm
TEST_IMPRESSIONS_LOW = 500  # trung vị lượt hiển thị sau 7 ngày dưới mức này là "gần như không có"

# ---- Ngách con và từ khoá dài (bài 2.3). Mục đánh dấu (*) là giả định của app. ----

SUBNICHE_MAX_LAYER = 3  # tầng 4 trở xuống là chủ đề video, không phải ngách
SUBNICHE_MIN_IDEAS = 30  # phải liệt kê được 30 ý tưởng không trùng
SUBNICHE_MIN_CHANNELS = 2  # (*) số kênh khác nhau tối thiểu để một cụm từ được coi là "lặp lại"
KEYWORD_LONG_MIN_WORDS = 4  # (*) từ khoá dài: từ 4 âm tiết / từ trở lên (tiếng Việt tính theo âm tiết)
KEYWORD_LONG_MIN_CHARS = 8  # (*) ngôn ngữ không cách chữ (Nhật, Hàn, Thái, Trung): từ 8 ký tự trở lên
KEYWORD_SAMPLE = 50  # số kết quả YouTube dùng để chấm một từ khoá (1 trang search = 100 unit)
KEYWORD_WINDOW_DAYS = 365  # (*) chỉ xét video đăng trong 1 năm gần nhất
KEYWORD_DEMAND_LOG_RANGE = (2.0, 5.0)  # (*) trung vị view kết quả: 100 → 0 điểm, 100K → 100 điểm
KEYWORD_BIG_CHANNEL_SUBS = 100_000  # (*) kết quả từ kênh từ mức này là "đối thủ lớn"
KEYWORD_COMPETITION_WEIGHTS = {"big_channels": 0.5, "exact_title": 0.5}  # (*)
KEYWORD_LOW_DATA_N = 10  # (*) dưới 10 kết quả có lượt xem → "ít dữ liệu"
KEYWORD_VERY_GOOD = 70
KEYWORD_GOOD = 55
KEYWORD_AVERAGE = 40

# ---- So ngách và đa thị trường (bài 2.4). ----

COMPARE_CLEAR_GAP = 10  # chênh từ 10 điểm là khác biệt rõ
COMPARE_TIE_GAP = 5  # chênh dưới 5 điểm coi như ngang
COMPARE_NOISE_GAP = 3  # chênh 2–3 điểm là nhiễu
COMPARE_SAMPLE_RATIO = 2.0  # (*) cỡ mẫu chênh từ 2 lần trở lên thì không so ngang được
OPPORTUNITY_WEIGHTS = {"niche": 0.45, "search": 0.30, "money": 0.25}
OPPORTUNITY_WEIGHTS_NO_TRENDS = {"niche": 0.60, "money": 0.40}
OPPORTUNITY_GREEN_MIN = 65
OPPORTUNITY_RPM_LOG_RANGE = (-0.7, 1.0)  # (*) RPM trung bình 0,2 USD → 0 điểm, 10 USD → 100 điểm

# ---- Theo dõi (bài 2.5: mỗi tháng đào lại ngách chính để phát hiện sớm tín hiệu bỏ ngách). ----
REDIG_DAYS = 30


# ---------------------------------------------------------------------------
# Radar từ khoá: quét danh mục Google Trends rải đều trong 24 giờ
# ---------------------------------------------------------------------------
RADAR_INTERVAL_S = 240  # (*) khoảng cách khởi đầu giữa các lượt Trends (±20%); sau đó tự điều chỉnh:
RADAR_MIN_INTERVAL_S = 60  # (*) nhanh nhất: 1 lượt/phút
RADAR_MAX_INTERVAL_S = 1200  # chậm nhất sau nhiều lần bị chặn: 20 phút/lượt
RADAR_SPEEDUP_EVERY = 10  # (*) cứ 10 lượt liền không bị chặn thì nhanh thêm RADAR_SPEEDUP_PCT; bị chặn (429) thì chậm gấp đôi
RADAR_SPEEDUP_PCT = 20  # (*) % nhanh thêm mỗi lần (khoảng cách giữa các lượt × 0,8)
RADAR_EMPTY_RESCAN_DAYS = 7  # (*) cặp thị trường × danh mục không có dữ liệu: 7 ngày quét lại một lần
RADAR_BARREN_RESCAN_DAYS = 3  # (*) cặp đã quét từ 3 lần mà chưa ra từ khoá đạt: 3 ngày một lần
RADAR_PER_SOURCE = 2  # (*) mỗi lần quét một danh mục, kiểm tra tối đa 2 từ khoá mới
RADAR_MIN_RISING = 200  # (*) chỉ xét từ khoá "đang tăng" từ +200%
RADAR_RESCAN_HOURS = 24  # mỗi cặp thị trường × danh mục quét tối đa 1 lần/ngày
RADAR_RECHECK_DAYS = 30  # từ khoá đã kiểm tra lượng tìm Trends thì 30 ngày sau mới kiểm lại
RADAR_YT_UNITS_PER_DAY = 1000  # (*) ngân sách quota YouTube để chấm từ khoá (≈ 102 unit/từ)
RADAR_SPIKE_RECHECK_DAYS = 14  # từ khoá vừa tăng vọt: kiểm lại sau 14 ngày nếu vẫn còn trong danh sách
# Lượng tìm 30 ngày gần nhất (theo ngày) trên YouTube Search và Google Search; thiếu ở nguồn nào cũng loại.
RADAR_30D_MAX_ZERO_SHARE = 0.5  # (*) quá nửa số ngày bằng 0 → gần như không ai tìm
RADAR_30D_MIN_AVG = 15  # (*) trung bình 30 ngày dưới 15/100 (so với ngày cao nhất) → lượng tìm thấp, lúc có lúc không
RADAR_GOOD_MIN = 65  # (*) điểm tiềm năng từ mức này được đánh dấu ⭐
RADAR_WEIGHTS = {"momentum": 0.35, "durability": 0.25, "youtube": 0.25, "persistence": 0.15}  # (*)
RADAR_MOMENTUM_LOG_RANGE = (2.0, 4.0)  # log10 % tăng: 100% → 0 điểm, 10.000% → 100 điểm


# ---------------------------------------------------------------------------
# Video & kênh tương tự (kiểu Related của TubeBuddy / vidIQ)
# ---------------------------------------------------------------------------
TTL_INNERTUBE = 24 * 3600  # cache trang "Up next" / search nội bộ của YouTube
SIMILAR_SEED_VIDEOS = 4  # đầu vào là kênh: lấy 4 video nhiều view nhất trong 15 video gần nhất làm điểm xuất phát
SIMILAR_MAX_QUERIES = 10  # (*) số truy vấn search nội bộ (tiêu đề + tag + autocomplete)
SIMILAR_ENRICH_CHANNELS = 60  # (*) số kênh ứng viên lấy thông tin chi tiết qua API (≈ 3 unit/kênh nếu chưa cache)
SIMILAR_ENRICH_VIDEOS = 150  # (*) số video ứng viên lấy view/thời lượng chính xác (1 unit / 50 video)
SIMILAR_RECENT_TITLES = 15  # số video gần nhất của kênh dùng để so nội dung
# (*) Trọng số: độ giống nội dung (so chữ, đã chuẩn hoá theo ứng viên giống nhất) và độ đồng xuất hiện.
SIMILAR_CHANNEL_WEIGHTS = {"content": 0.6, "cooccur": 0.4}
SIMILAR_VIDEO_WEIGHTS = {"content": 0.65, "signals": 0.35}
SIMILAR_SIGNAL_CAP = 4  # (*) tín hiệu video: tính tối đa 4 lần xuất hiện
SIMILAR_MIN_CONTENT = 0.3  # (*) độ giống nội dung (so với ứng viên giống nhất) dưới mức này thì loại là lạc đề
# Tín hiệu đồng xuất hiện: một lần nằm trong "Up next" nặng gấp đôi một lần ra trong search.
SIMILAR_SIGNAL_WEIGHTS = {"related": 2.0, "search": 1.0, "search_channel": 1.0}
