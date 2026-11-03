"""Ngưỡng chỉnh được ở trang Cài đặt: lưu đè trong data/settings.json, áp vào yt_niche.config lúc chạy."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from yt_niche import config as cfg


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str  # int | float | pair | weights
    help: str = ""


EDITABLE: list[Setting] = [
    Setting("OUTLIER_OUTPERFORM", "Vượt trội từ (×)", "Outlier", "float"),
    Setting("OUTLIER_VIRAL", "Nổ view từ (×)", "Outlier", "float"),
    Setting("OUTLIER_SUPER_VIRAL", "Siêu viral từ (×)", "Outlier", "float"),
    Setting("MIN_CHANNEL_SAMPLE", "Số video tối thiểu để tin trung vị kênh", "Outlier", "int"),
    Setting("SMALL_CHANNEL_MAX_SUBS", "Kênh nhỏ: dưới (sub)", "Kênh", "int"),
    Setting("BIG_CHANNEL_MIN_SUBS", "Kênh khổng lồ: trên (sub)", "Kênh", "int"),
    Setting("YOUNG_CHANNEL_MONTHS", "Kênh trẻ: dưới (tháng)", "Kênh", "int"),
    Setting("NICHE_WEIGHTS", "Trọng số điểm ngách", "Điểm ngách", "weights", "Tổng phải bằng 1"),
    Setting("DEMAND_LOG_RANGE", "Nhu cầu: log10 trung vị view (0 điểm, 100 điểm)", "Điểm ngách", "pair", "3 = 1K view, 6 = 1M view"),
    Setting("FRESHNESS_VPH_LOG_RANGE", "Độ mới: log10 view/giờ (0 điểm, 100 điểm)", "Điểm ngách", "pair"),
    Setting("LABEL_GREEN_MIN", "Ngách xanh từ", "Điểm ngách", "float"),
    Setting("LABEL_POTENTIAL_MIN", "Ngách tiềm năng từ", "Điểm ngách", "float"),
    Setting("CONFIDENCE_HIGH_MIN_N", "Tin cậy cao: số video tối thiểu", "Độ tin cậy", "int"),
    Setting("CONFIDENCE_HIGH_MAX_ESTIMATED", "Tin cậy cao: tỉ lệ ước tính tối đa", "Độ tin cậy", "float"),
    Setting("CONFIDENCE_LOW_MAX_N", "Tin cậy thấp: dưới (video)", "Độ tin cậy", "int"),
    Setting("CONFIDENCE_LOW_MIN_ESTIMATED", "Tin cậy thấp: tỉ lệ ước tính trên", "Độ tin cậy", "float"),
    Setting("SUPPLY_GOOD_OUTLIERS", "Cung tốt: số video vượt trội", "3 tầng", "int"),
    Setting("SUPPLY_GOOD_SMALL_CHANNELS", "Cung tốt: số kênh nhỏ nổ", "3 tầng", "int"),
    Setting("SUPPLY_WEAK_SMALL_CHANNELS", "Cung yếu: dưới (kênh nhỏ nổ)", "3 tầng", "int"),
    Setting("TIER_DEMAND_STRONG", "Cầu mạnh: Nhu cầu từ", "3 tầng", "float"),
    Setting("TIER_DEMAND_WEAK", "Cầu yếu: Nhu cầu dưới", "3 tầng", "float"),
    Setting("TIER_COMPETITION_WEAK", "Cung yếu: Cạnh tranh trên (%)", "3 tầng", "float"),
    Setting("RPM_TIER_MEDIUM_MIN", "RPM vừa từ (USD)", "3 tầng", "float"),
    Setting("RPM_TIER_HIGH_MIN", "RPM cao từ (USD)", "3 tầng", "float"),
    Setting("FORMAT_FIT_RATIO", "Dài/Shorts: thắng khi trung vị gấp", "Dài/Shorts", "float"),
    Setting("FORMAT_MIN_N", "Dài/Shorts: số video tối thiểu mỗi bên", "Dài/Shorts", "int"),
    Setting("TRENDS_FAD_BASE_MAX", "Trào lưu: nền 4 năm tối đa", "Trends", "float"),
    Setting("TRENDS_FAD_RECENT_MIN", "Trào lưu: 13 tuần gần nhất tối thiểu", "Trends", "float"),
    Setting("TRENDS_FAD_RATIO", "Trào lưu: gấp nền ít nhất", "Trends", "float"),
    Setting("TRENDS_SEASONAL_AUTOCORR", "Theo mùa: tự tương quan 52 tuần từ", "Trends", "float"),
    Setting("TRENDS_FALLING_5Y", "Đang rơi: xu hướng 5 năm từ (âm)", "Trends", "float"),
    Setting("SUBNICHE_MIN_CHANNELS", "Cụm lặp lại: số kênh khác nhau tối thiểu", "Ngách con & từ khoá", "int"),
    Setting("KEYWORD_LONG_MIN_WORDS", "Từ khoá dài: từ (số từ / âm tiết)", "Ngách con & từ khoá", "int"),
    Setting("KEYWORD_LONG_MIN_CHARS", "Từ khoá dài (Nhật, Hàn, Thái, Trung): từ (ký tự)", "Ngách con & từ khoá", "int"),
    Setting("KEYWORD_WINDOW_DAYS", "Chấm từ khoá: xét video trong (ngày)", "Ngách con & từ khoá", "int"),
    Setting("KEYWORD_DEMAND_LOG_RANGE", "Từ khoá: log10 trung vị view (0 điểm, 100 điểm)", "Ngách con & từ khoá", "pair", "2 = 100 view, 5 = 100K view"),
    Setting("KEYWORD_BIG_CHANNEL_SUBS", "Từ khoá: đối thủ lớn từ (sub)", "Ngách con & từ khoá", "int"),
    Setting("KEYWORD_COMPETITION_WEIGHTS", "Từ khoá: trọng số cạnh tranh", "Ngách con & từ khoá", "weights", "Kênh lớn và tiêu đề nhắm đúng; tổng phải bằng 1"),
    Setting("KEYWORD_LOW_DATA_N", "Từ khoá \"ít dữ liệu\": dưới (kết quả)", "Ngách con & từ khoá", "int"),
    Setting("COMPARE_SAMPLE_RATIO", "So ngách: cảnh báo khi cỡ mẫu chênh từ (lần)", "So sánh", "float"),
    Setting("OPPORTUNITY_RPM_LOG_RANGE", "Điểm cơ hội: log10 RPM (0 điểm, 100 điểm)", "So sánh", "pair", "-0,7 ≈ 0,2 USD; 1 = 10 USD"),
    Setting("REDIG_DAYS", "Nhắc đào lại ngách đang theo dõi sau (ngày)", "10 video thử", "int"),
    Setting("RADAR_INTERVAL_S", "Radar: khoảng cách khởi đầu giữa các lượt (giây)", "Radar từ khoá", "int", "Sau đó app tự nhanh dần khi Google không chặn, chậm gấp đôi khi bị chặn"),
    Setting("RADAR_MIN_INTERVAL_S", "Radar: nhanh nhất (giây/lượt)", "Radar từ khoá", "int", "60 giây ≈ 1.440 lượt/ngày"),
    Setting("RADAR_SPEEDUP_EVERY", "Radar: tăng tốc sau (lượt liền không bị chặn)", "Radar từ khoá", "int"),
    Setting("RADAR_SPEEDUP_PCT", "Radar: mỗi lần tăng tốc nhanh thêm (%)", "Radar từ khoá", "int"),
    Setting("RADAR_EMPTY_RESCAN_DAYS", "Radar: cặp không có dữ liệu quét lại sau (ngày)", "Radar từ khoá", "int"),
    Setting("RADAR_BARREN_RESCAN_DAYS", "Radar: cặp chưa ra từ khoá đạt (sau 3 lần) quét lại sau (ngày)", "Radar từ khoá", "int"),
    Setting("RADAR_PER_SOURCE", "Radar: số từ khoá mới kiểm tra mỗi danh mục", "Radar từ khoá", "int"),
    Setting("RADAR_MIN_RISING", "Radar: chỉ xét từ khoá đang tăng từ (%)", "Radar từ khoá", "int"),
    Setting("RADAR_YT_UNITS_PER_DAY", "Radar: quota YouTube để chấm từ khoá mỗi ngày (unit)", "Radar từ khoá", "int", "≈ 102 unit mỗi từ khoá"),
    Setting("RADAR_30D_MAX_ZERO_SHARE", "Radar: loại khi số ngày bằng 0 trong 30 ngày vượt (tỉ lệ)", "Radar từ khoá", "float", "Xét riêng YouTube Search và Google Search; 0,5 = quá nửa số ngày"),
    Setting("RADAR_30D_MIN_AVG", "Radar: loại khi trung bình 30 ngày dưới (thang 0–100)", "Radar từ khoá", "float", "So với ngày cao nhất trong 30 ngày; thấp = lúc có lúc không, ít người tìm"),
    Setting("RADAR_GOOD_MIN", "Radar: đánh dấu ⭐ từ điểm tiềm năng", "Radar từ khoá", "float"),
    Setting("RADAR_WEIGHTS", "Radar: trọng số điểm tiềm năng", "Radar từ khoá", "weights", "Đà tăng, độ đều 30 ngày, YouTube, số ngày lặp lại; tổng phải bằng 1"),
    Setting("TEST_STANDOUT_RATIO", "Video thử vượt rõ: lượt xem gấp trung vị", "10 video thử", "float"),
    Setting("TEST_CTR_LOW", "CTR thấp: dưới (%)", "10 video thử", "float"),
    Setting("TEST_RETENTION_LOW", "Giữ chân thấp: dưới (% thời lượng)", "10 video thử", "float"),
    Setting("TEST_IMPRESSIONS_LOW", "Hiển thị gần như không có: dưới (lượt, sau 7 ngày)", "10 video thử", "int"),
]

DEFAULTS = {s.key: copy.deepcopy(getattr(cfg, s.key)) for s in EDITABLE}
_BY_KEY = {s.key: s for s in EDITABLE}


def load_overrides() -> dict:
    try:
        return json.loads(cfg.SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _coerce(setting: Setting, value):
    if setting.kind == "int":
        return int(value)
    if setting.kind == "float":
        return float(value)
    if setting.kind == "pair":
        return (float(value[0]), float(value[1]))
    return {k: float(v) for k, v in value.items()}


def validate(values: dict) -> list[str]:
    errors = []
    for key in ("NICHE_WEIGHTS", "KEYWORD_COMPETITION_WEIGHTS", "RADAR_WEIGHTS"):
        w = values.get(key)
        if w and abs(sum(w.values()) - 1) > 1e-6:
            errors.append(f"{_BY_KEY[key].label} phải có tổng = 1 (đang là {sum(w.values()):.2f})")
    for key in ("DEMAND_LOG_RANGE", "FRESHNESS_VPH_LOG_RANGE", "KEYWORD_DEMAND_LOG_RANGE", "OPPORTUNITY_RPM_LOG_RANGE"):
        if key in values and values[key][0] >= values[key][1]:
            errors.append(f"{_BY_KEY[key].label}: mốc 0 điểm phải nhỏ hơn mốc 100 điểm")
    order = [values.get(k, getattr(cfg, k)) for k in ("OUTLIER_OUTPERFORM", "OUTLIER_VIRAL", "OUTLIER_SUPER_VIRAL")]
    if not order[0] < order[1] < order[2]:
        errors.append("Ngưỡng outlier phải tăng dần: vượt trội < nổ view < siêu viral")
    return errors


def apply(overrides: dict) -> None:
    """Đặt lại mặc định rồi áp phần lưu đè vào module config."""
    for key, value in DEFAULTS.items():
        setattr(cfg, key, copy.deepcopy(value))
    for key, value in overrides.items():
        if key in _BY_KEY:
            setattr(cfg, key, _coerce(_BY_KEY[key], value))


def save(values: dict) -> None:
    """Chỉ lưu những giá trị khác mặc định."""
    overrides = {k: v for k, v in values.items() if k in _BY_KEY and _coerce(_BY_KEY[k], v) != DEFAULTS[k]}
    cfg.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg.SETTINGS_PATH.write_text(json.dumps(overrides, ensure_ascii=False, indent=1), encoding="utf-8")
    apply(overrides)


def init() -> None:
    apply(load_overrides())
