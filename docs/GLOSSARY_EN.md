# English glossary — single source of truth for the Vietnamese → English migration

Every agent translating a file MUST use these exact strings. Constants that are stored in the database or compared
in tests are marked **(stored)**: their new value is fixed here and `db/connection.py` migrates old rows.

## 1. Product terms

| Vietnamese | English (use exactly) | Notes |
|---|---|---|
| Đào ngách / lần đào | **Dig** (verb/noun: "dig a niche", "a dig", "dig run") | the app's core action |
| Ngách | niche | |
| Ngách con | sub-niche | |
| Ngách mẹ | parent niche | |
| Tầng (ngách con tầng 2) | layer | "layer 2" |
| Bảng chấm | scorecard | |
| Chốt ngách | lock in the niche / final decision | |
| Ngách chính / ngách dự phòng | main niche / backup niche | |
| Kênh nhỏ nổ (kênh nhỏ đang nổ) | small booming channel | |
| Video vượt trội | outperformer video | |
| Nổ view | viral | |
| Siêu viral | super viral | |
| Trung vị view | median views | |
| Điểm ngách | niche score | |
| 4 thanh con: Nhu cầu / Cơ hội / Độ mới / Cạnh tranh | four bars: Demand / Opportunity / Freshness / Competition | |
| 3 tầng: Cầu / Cung / Tiền | three tiers: Demand / Supply / Money | |
| Độ tin cậy | confidence | |
| Mẫu còn ít | small sample | |
| Ước tính (≈) | estimate (≈) | |
| Từ khoá dài / ngắn | long-tail keyword / short keyword | |
| Gợi ý tìm kiếm | search suggestions | |
| Radar từ khoá | Keyword radar | |
| Video & kênh tương tự | Similar videos & channels | |
| So sánh (đa thị trường) | Compare (multi-market) | |
| 10 video thử | 10 test videos | |
| Tiến độ chặng | Stage progress | |
| Cài đặt | Settings | |
| Ngách đã lưu | Saved niches | |
| Ngách con & từ khoá | Sub-niches & keywords | |
| Bản đồ ngách | Niche map | |
| Thị trường | market | region code stays (VN, US…) |
| Định dạng: Video dài / Shorts / Mọi độ dài | format: Long-form / Shorts / Any length | |
| Khoảng thời gian | time window | "30 days" |
| Nhóm chủ đề (RPM) | topic category (RPM) | |
| Thu nhập/video (≈) | income per video (≈) | |
| Sản phẩm affiliate gắn tự nhiên | affiliate products that fit naturally | |
| Lời hứa kênh | channel promise | |
| Trụ nội dung | content pillar | |
| Quota (unit) | quota (units) | keep "units" |
| Bài 2.1 / 2.2 / 2.3 / 2.4 / 2.5 (khoá học DeepNiche) | lesson 2.1 / 2.2 / … | keep the numbers |
| Chặng "Chọn ngách" | the "Pick a niche" stage | |
| Cờ đỏ | red flag | |
| Trọng số | weight | |
| Khoá (bảng chấm) | lock | |
| Ý tưởng | idea | |
| Góc kể | angle | |
| Đối tượng | audience | |
| Thị trường ít người làm | under-served market | |

## 2. Constants — exact values

### core/niche_score.py **(stored: dig_run.label, result_json.labels)**
```
LABEL_GREEN = "Green niche"
LABEL_POTENTIAL = "Promising niche"
LABEL_RED = "Red niche"
```
### core/analyze.py **(stored in result_json.labels)**
```
FEW_SAMPLES = "Small sample"
```
### core/confidence.py **(stored: dig_run.confidence, result_json.confidence)**
```
HIGH, MEDIUM, LOW = "high", "medium", "low"
```
### core/money.py **(stored: result_json.rpm_tier)**
```
RPM_LOW, RPM_MEDIUM, RPM_HIGH = "low", "medium", "high"
```
### core/three_tier.py **(stored: dig_run.verdict, result_json.three_tier.*)**
```
STRONG, MEDIUM, WEAK = "Strong", "Medium", "Weak"
TRY, TRY_AFTER_FIX, DROP = "TRY", "TRY AFTER FIXING", "DROP"
FIXES = {
    "demand": "Weak Demand: widen one layer up, or switch to a topic people actually search for.",
    "supply": "Weak Supply: go down 2–3 sub-niche layers or change the audience.",
    "money": "Weak Money: change market or add affiliate products that match the need.",
}
```
### core/trends_classify.py **(stored: result_json.trends.class, radar_keyword.trend_class)**
```
STABLE, FAD, FALLING, SEASONAL = "evergreen", "fad", "declining", "seasonal"
ADVICE = {
    STABLE: "Evergreen demand: you can build a whole channel on it.",
    FAD: "Fad: make a few quick videos, do not build a channel on it.",
    FALLING: "Declining: only enter if the score is still high; needs a fresh angle and a backup niche.",
    SEASONAL: "Seasonal: prepare videos 4–6 weeks before the peak.",
}
```
### core/outlier.py **(stored: result_json.outliers[].level)**
```
LEVEL_OUTPERFORM = "Outperformer"
LEVEL_VIRAL = "Viral"
LEVEL_SUPER_VIRAL = "Super viral"
```
### core/format_split.py **(stored: result_json.format_split.fit_label)**
```
FIT_LABEL = {LONG: "Fits long-form", SHORTS: "Fits Shorts"}
```
### core/reading.py
```
FORMAT_LABEL = {LONG: "long-form", SHORTS: "Shorts"}
```
### core/compare.py
```
ABILITY = {"long": "Long-form", "shorts": "Shorts", "both": "Both"}
COLUMN labels: score "Niche score", confidence "Confidence", median_views "Median views",
  doors "Outperformers / small booming", trends "Trends 1y / 5y", income "Income per video (≈)", format "Winning format"
```
### core/keyword_score.py
```
LOW_DATA = "low data"
```
### core/radar.py
```
STATUS labels: PENDING "to check", PASSED "passed", EXCLUDED "excluded", IGNORED "ignored", WATCHING "watching"
DEFAULT_BLOCKLIST keeps the Vietnamese words (they are real search terms to block) and adds nothing.
```
### core/test_plan.py **(stored? no — computed)**
```
KEEP, ADJUST, SWITCH = "KEEP", "ADJUST", "SWITCH"
DROP_FLAGS = {
    "trend_and_booms_gone": "Google Trends 1-year line keeps falling and small booming channels vanished on re-dig",
    "demonetized": "Repeatedly limited ads or forced to work around copyright",
    "template_only": "Only works by repeating the exact same template",
    "no_money_path": "No path to money within 12 months and no product to recommend",
    "hate_it": "You hate making this niche",
}
```
### core/scorecard.py (keys unchanged; labels + guides in English)
```
SENSITIVE_CATEGORIES = ("Finance", "Health", "Spirituality")   # substring match against category names below
CRITERIA labels:
  demand "Demand" · trend "Trend" · small_channels "Small booming channels" · competition "Competition (reverse)"
  ad_price "Ad price" · money_other "Money beyond ads" · production "Sustainable production"
  policy_risk "Policy risk (reverse)" · passion "Your interest" · long_keywords "Long-tail keywords"
```
### core/subniche.py
```
FILTERS = {"audience": "Audience", "problem": "Problem", "angle": "Angle / format", "market": "Market / language"}
SUBNICHE, LONG, SHORT = "Sub-niche", "Long-tail keyword", "Short keyword"
```
### services/report.py
```
PROMPTS = {"three_tier": "Three-tier check (lesson 2.1)", "read_results": "Read dig results (lesson 2.2)"}
FMT_LABEL = {"any": "Any length", "long": "Long-form", "shorts": "Shorts"}
```
### services/tracking.py
```
MAIN, BACKUP, SHORTLIST = "main niche", "backup", "on scorecard"
```
### services/market_catalog.py **(stored: market.group_name)**
```
OTHER_GROUP = "Other"
```
### services/ai.py
```
TIERS = {"fast": "Fast (classify, translate)", "deep": "Deep (analysis, ideas)"}
KEYWORD_TYPES labels: evergreen "Evergreen", seasonal "Seasonal", how_to "How-to / skill", product "Product / service",
  news "News / event", person "Celebrity", sports_match "Match / tournament", entertainment_release "New film / music / show",
  brand_launch "Product / game launch", other_short "Other short-lived"
EFFORTS: "" "Default (not sent)", off "Thinking off", low "Low", medium "Medium", high "High", xhigh "Very high", max "Max"
```
### services/niche_map.py
```
MOTIVATIONS = {
  "learn": "Learn — what X is, how it works", "earn": "Earn — how to make money from X",
  "fix": "Fix a problem — X is broken, how to fix it", "improve": "Improve — do X better",
  "believe": "Believe & meaning — what this means to me", "connect": "Connect — how people like me live",
  "escape": "Escape — let me forget for a while", "experience": "Experience — what X feels like",
  "updated": "Stay updated — what's new about X", "inspired": "Feel inspired — give me motivation",
}
```
### config.py **(stored: market.name, market.group_name via DEFAULT_MARKET_GROUPS)**
```
MARKETS names: US "United States", GB "United Kingdom", CA "Canada", AU "Australia", DE "Germany", FR "France",
  JP "Japan", KR "South Korea", TW "Taiwan", VN "Vietnam", ID "Indonesia", TH "Thailand", PH "Philippines",
  BR "Brazil", MX "Mexico", AR "Argentina", IN "India"
MARKET_GROUPS = {
    "Tier 1 (high RPM)": ["US", "GB", "CA", "AU", "DE", "FR"],
    "East Asia": ["JP", "KR", "TW"],
    "Southeast Asia": ["VN", "ID", "TH", "PH"],
    "Latin America & India": ["BR", "MX", "AR", "IN"],
}
```
### sources/rpm.py + data/rpm_table.csv **(stored: dig_run.category, market_batch.category, scorecard via category)**
```
"Giải trí / kể chuyện" → "Entertainment / storytelling"
"Lịch sử"              → "History"
"Giáo dục"             → "Education"
"Tài chính / kinh doanh" → "Finance / business"
"Công nghệ"            → "Technology"
"Du lịch"              → "Travel"
"Game"                 → "Gaming"
"Nấu ăn"               → "Cooking"
"Sức khỏe"             → "Health"
"Thiếu nhi"            → "Kids"
"Tâm linh / tôn giáo"  → "Spirituality / religion"
"Tâm lý / phát triển bản thân" → "Psychology / self-improvement"
"Âm nhạc / thư giãn"   → "Music / relaxation"
```
`sources/rpm.load_table()` must rename these categories on read (so a user-edited CSV with Vietnamese names keeps
working) and `data/rpm_table.csv` in the repo is rewritten in English.

### settings.py
Group names: "Outlier", "Channels", "Niche score", "Confidence", "Three tiers", "Long/Shorts", "Trends", "Keywords",
"Compare", "Radar", "Test videos". Labels are free English, short, same meaning.

## 3. Database migration (owned by the core/db agent, in `db/connection.py::_migrate`)

Add `_migrate_language(conn)` that runs once (flag in `app_meta`, key `lang_migrated_en` = "1") and maps old
Vietnamese stored values to the new constants. Exact-match only; unknown values untouched.

| Table.column | Mapping |
|---|---|
| dig_run.label | Ngách xanh→Green niche, Ngách tiềm năng→Promising niche, Ngách đỏ→Red niche |
| dig_run.confidence | cao→high, vừa→medium, thấp→low |
| dig_run.verdict | THỬ→TRY, THỬ SAU KHI SỬA→TRY AFTER FIXING, BỎ→DROP |
| dig_run.category, market_batch.category | the 13 categories above |
| dig_run.result_json | walk the JSON; replace string leaves that exactly equal any key of the combined map (labels, confidence, rpm_tier, three_tier levels/verdict, trends.class, outliers[].level, format_split.fit_label). Also `three_tier.*.reason`, `trends.reason`, `note` are free text: leave as is. |
| radar_keyword.trend_class | bền→evergreen, trào lưu→fad, đang rơi→declining, theo mùa→seasonal |
| market.name | per MARKETS above (by region code) |
| market.group_name | Tier 1 (RPM cao)→Tier 1 (high RPM), Châu Á→East Asia, Đông Nam Á→Southeast Asia, Mỹ Latin & Ấn Độ→Latin America & India, Khác→Other |

## 4. Style rules for all translated text

- Plain, short, product English. No machine-translation flavour. "Dig" is the verb for the app's search+score action.
- Sentence case for labels and buttons ("Dig", "Save to scorecard"), Title Case only for page titles.
- Keep numbers, units and emoji exactly as they were.
- Lesson references stay: "(lesson 2.3)".
- Prompts in `yt_niche/prompts/*.md` are rewritten in English for an English-speaking LLM; keep the same structure,
  placeholders (`{...}`) and output format rules.
- Comments and docstrings are translated too, so the codebase ends up in one language.
- Tests: update expected strings to the constants above; prefer importing the constant over a literal.
