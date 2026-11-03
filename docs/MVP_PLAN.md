# YT Niche — Kế hoạch MVP (Module 1 Đào ngách + Module 4 Chốt ngách)

> App cá nhân, chạy local. Dựa trên phương pháp chặng "Chọn ngách" (bài 2.1–2.5) của DeepNiche.
> Phạm vi MVP: **Đào ngách** (bài 2.1 + 2.2) và **Bảng chấm chốt ngách** (bài 2.5).
> Ngoài phạm vi MVP: ngách con & từ khoá (2.3), đa thị trường (2.4), theo dõi 10 video thử bằng OAuth.

---

## 1. Công nghệ

| Lớp | Lựa chọn | Lý do |
|---|---|---|
| Ngôn ngữ | Python 3.11 (đã có trên máy) | pytrends, pandas, Google API client đều là Python |
| Engine tính điểm | Hàm thuần trong `core/` | Test được độc lập, không phụ thuộc UI/API |
| Lưu trữ | SQLite (qua SQLModel) | 1 file, không cần server |
| UI | Streamlit | Nhanh nhất cho app cá nhân: bảng, biểu đồ, form có sẵn. Engine tách riêng nên sau này đổi sang FastAPI + React được |
| Dữ liệu | YouTube Data API v3 (API key), pytrends | |
| LLM | Không gọi API trong MVP: nút "Copy prompt" kèm dữ liệu | Không tốn phí; dán vào Claude/ChatGPT |

## 2. Cấu trúc thư mục

```
YT_NICHE/
  pyproject.toml
  .env                      # YOUTUBE_API_KEY=...
  data/
    yt_niche.db
    rpm_table.csv           # RPM theo thị trường × nhóm chủ đề (tự sửa được)
  yt_niche/
    config.py               # ngưỡng, trọng số mặc định (1 chỗ duy nhất)
    core/                   # thuần, không I/O
      stats.py              # median, log-scale, clamp
      outlier.py            # outlier ×, nhãn vượt trội/nổ/siêu viral
      niche_score.py        # 4 thanh con + điểm ngách + nhãn
      confidence.py         # độ tin cậy
      format_split.py       # tách Dài/Shorts + nhãn "hợp định dạng"
      trends_classify.py    # bền / trào lưu / đang rơi / theo mùa
      three_tier.py         # Cầu/Cung/Tiền → Mạnh/Vừa/Yếu → THỬ/SỬA/BỎ
      scorecard.py          # bảng 10 tiêu chí, cờ đỏ, chọn chính/dự phòng
    sources/
      youtube.py            # search/videos/channels/playlistItems + đếm quota
      trends.py             # pytrends + retry/backoff
      rpm.py                # đọc rpm_table.csv
    db/
      models.py
      repo.py
      cache.py              # cache HTTP theo key + TTL
    services/
      dig.py                # điều phối 1 lần đào: fetch → tính → lưu snapshot
    prompts/
      three_tier.md         # prompt bài 2.1
      read_results.md       # prompt bài 2.2
      scorecard_review.md   # prompt bài 2.5 – phản biện bảng chấm
      test_plan.md          # prompt bài 2.5 – kế hoạch 10 video
    ui/
      app.py
      pages/
        1_Dao_ngach.py
        2_Ngach_da_luu.py
        3_Bang_cham.py
        4_Cai_dat.py
  tests/
    fixtures/               # JSON API đã ghi sẵn, test không tốn quota
```

---

## 3. Module 1: Đào ngách

### 3.1 Đầu vào
- Chủ đề (2–4 từ)
- Thị trường: `regionCode` + `relevanceLanguage` (mặc định VN / vi)
- Khoảng thời gian: 7 / 30 / 90 / 365 ngày → `publishedAfter`
- Định dạng: Video dài / Shorts / Mọi độ dài
- Nhóm chủ đề (để tra RPM), ô "Kèm Google Trends", số trang search (1–2)

### 3.2 Luồng gọi API và chi phí quota (10.000 unit/ngày)

| Bước | Endpoint | Unit |
|---|---|---|
| 1. Lấy bộ mẫu video (50/trang × 2 trang) | `search.list` (order=viewCount hoặc relevance) | 200 |
| 2. View, thời lượng, ngày đăng | `videos.list` (50 id/lần) | 2 |
| 3. Sub, ngày tạo kênh, số video, playlist uploads | `channels.list` (50 id/lần) | 2 |
| 4. Trung vị view của từng kênh (30 video gần nhất) | `playlistItems.list` + `videos.list` | ~2 × số kênh chưa cache |
| **Tổng lần đầu** | | **~300–350** |
| **Tổng khi kênh đã cache** | | **~210** |

→ Khoảng **30–45 lần đào/ngày**. Hiển thị quota đã dùng hôm nay trên thanh bên.
Cache: kết quả search 24h, thông tin kênh và trung vị kênh 7 ngày.

### 3.3 Công thức (lấy trong `config.py`, chỉnh được)

**Phân loại định dạng:** Shorts ≤ 180 giây; video dài ≥ 240 giây; khoảng 181–239 giây bỏ khỏi phép so Dài/Shorts.

**Outlier** = view video ÷ trung vị view của kênh đó (chỉ tính video cùng định dạng).
- ≥ 3×: Vượt trội · ≥ 10×: Nổ view · ≥ 50×: Siêu viral

**4 thanh con (0–100):**
- **Nhu cầu** = clamp((log10(trung vị view) − 3) / 3 × 100). 1K view → 0, 1M view → 100.
- **Cơ hội** = số video vượt trội đến từ kênh nhỏ (< 100K sub) ÷ tổng video vượt trội × 100 (0 nếu không có video vượt trội).
- **Độ mới** = % video trong mẫu đăng trong 30 ngày gần nhất. Nếu khoảng thời gian đã ≤ 30 ngày thì dùng trung vị view/giờ theo thang log: 1 view/giờ → 0, 1.000 view/giờ → 100. *(Mốc view/giờ là giả định của app, khóa học không công bố.)*
- **Cạnh tranh** = % video trong mẫu thuộc kênh > 1M sub.

**Điểm ngách** = 0,35·Nhu cầu + 0,25·Cơ hội + 0,20·Độ mới + 0,20·(100 − Cạnh tranh)
- 70–100: Ngách xanh · 50–69: Tiềm năng · < 50: Ngách đỏ

**Độ tin cậy** (một số được tính là "ước tính" khi kênh ẩn sub, video ẩn view, hoặc kênh không lấy được trung vị):
- Cao: n ≥ 40 và ≤ 30% ước tính
- Thấp: n < 15 hoặc > 60% ước tính → gắn nhãn "Mẫu còn ít", **khoá nút "Đưa vào bảng chấm"**
- Vừa: các trường hợp còn lại

**Kênh nhỏ đang nổ:** sub < 100K và có ít nhất 1 video trong mẫu ≥ 3×. Thẻ kênh hiện: sub, tuổi kênh (tháng), số video, video tốt nhất, tỉ lệ ×. Ưu tiên kênh dưới 12 tháng tuổi.
Tín hiệu cung tốt: ≥ 10 video vượt trội và ≥ 5 kênh nhỏ nổ. Dưới 3 kênh nhỏ nổ → gợi ý "đào ngách con".

**Dài/Shorts** (khi chọn Mọi độ dài): mỗi định dạng hiện n, trung vị view, số video vượt trội. Gắn nhãn "Ngách hợp video dài" hoặc "Ngách hợp Shorts" khi một bên thắng cả trung vị (≥ 1,5×) lẫn số video vượt trội.

**Thu nhập/video (≈)** = trung vị view × RPM_thấp…RPM_cao (theo thị trường × nhóm chủ đề) ÷ 1000, hiển thị dạng khoảng.

### 3.4 Phân loại Google Trends
Lấy 3 chuỗi: `today 5-y`, `today 12-m`, `today 3-m` (nguồn YouTube hoặc Web, geo theo thị trường).

1. **Trào lưu:** trung bình 4 năm đầu của chuỗi 5 năm ≤ 10 **và** trung bình 3 tháng gần nhất ≥ 3 lần mức đó.
2. **Theo mùa:** tự tương quan độ trễ 52 tuần ≥ 0,5 (đỉnh lặp lại hằng năm). Hiện tháng đỉnh và nhắc "chuẩn bị video trước đỉnh 4–6 tuần".
3. **Đang rơi:** độ dốc 5 năm < 0 có ý nghĩa **và** độ dốc 12 tháng < 0.
4. **Bền:** các trường hợp còn lại (đi ngang hoặc lên).

Trends lỗi (429 hoặc không có dữ liệu) → ghi "chưa có Trends", vẫn chạy được phần còn lại. Có retry với backoff và cache 24h.

### 3.5 Chấm 3 tầng (bài 2.1)
- **Cầu:** Mạnh nếu Nhu cầu ≥ 60 và Trends Bền; Yếu nếu Nhu cầu < 30 hoặc Trends Đang rơi; còn lại Vừa. Chưa có Trends → tối đa Vừa.
- **Cung:** Mạnh nếu ≥ 10 video vượt trội và ≥ 5 kênh nhỏ nổ; Yếu nếu < 3 kênh nhỏ nổ hoặc Cạnh tranh > 60; còn lại Vừa.
- **Tiền:** kết hợp bậc RPM (tự động) và số sản phẩm affiliate người dùng nhập (0 → Yếu, 1–2 → Vừa, ≥ 3 → Mạnh). Lấy mức trung bình làm tròn xuống.
- **Kết luận:** không tầng nào Yếu → **THỬ** · có 1 tầng Yếu → **THỬ SAU KHI SỬA** (kèm gợi ý sửa theo bảng 4 trường hợp thiếu tầng) · từ 2 tầng Yếu → **BỎ**.

*(Ngưỡng Mạnh/Vừa/Yếu là giả định của app, khóa học để người dùng tự chấm. Khi tự động chấm sai, người dùng được ghi đè.)*

### 3.6 Màn hình Đào ngách
1. Form đầu vào → nút **Đào**
2. Khối điểm: điểm ngách + nhãn + độ tin cậy + 4 thanh con
3. Khối 3 tầng: Mạnh/Vừa/Yếu kèm con số làm lý do, ô nhập affiliate, dòng kết luận
4. Bảng video vượt trội (sắp theo ×, có link mở video)
5. Lưới thẻ kênh nhỏ đang nổ, kèm checkbox "đã mở xem, kênh tự làm nội dung"; có ít nhất 3 kênh được tick thì mới coi là đã kiểm tra
6. Khung Dài/Shorts
7. Biểu đồ Trends 3 tháng / 1 năm / 5 năm + nhãn phân loại
8. Nút: **Lưu ngách** · **Xuất Excel** · **Copy prompt 2.1 / 2.2** · **Đưa vào bảng chấm**
9. Dòng ghi chép một câu tự sinh, ví dụ: *"Kể chuyện lịch sử — 75/100 — 21 vượt trội / 12 kênh nhỏ nổ — 42–85 USD/video (≈) — THỬ"*

**Ngách đã lưu:** danh sách các lần đào (snapshot), có nút **Đào lại**, hiện số mới cạnh số cũ.

---

## 4. Module 4: Bảng chấm chốt ngách

### 4.1 Luồng làm việc
1. **Tạo bảng chấm** → chọn 2–5 ngách đã đào.
   - Cảnh báo nếu các lần đào khác thị trường, khoảng thời gian, định dạng, hoặc cách nhau quá 3 ngày (yêu cầu so "cùng thiết lập, cùng ngày" của bài 2.4).
2. **Đặt trọng số** cho 10 tiêu chí (mặc định theo bảng dưới), tổng phải đúng 100 → **Khoá trọng số** (lưu thời điểm khoá).
   - Sau khi khoá, muốn đổi trọng số phải tạo phiên bản mới; phiên bản cũ vẫn lưu lại để đối chiếu.
3. **Chấm điểm 1–5** cho từng ô. App gợi ý sẵn ô tự động, người dùng xác nhận hoặc sửa. Mỗi ô có trường "căn cứ".
4. **Kết quả:** điểm chốt, cờ đỏ, ngách chính, ngách dự phòng, lời hứa kênh.
5. **Kế hoạch thử:** checklist 10 video + mốc video 5 và 10 (nhập tay, không OAuth).

### 4.2 10 tiêu chí

| Tiêu chí | Trọng số | Gợi ý tự động |
|---|---|---|
| Nhu cầu | 15 | Xếp hạng trung vị view giữa các ngách trong bảng (cao nhất = 5) |
| Xu hướng | 10 | Bền = 5, Theo mùa = 4, Trào lưu = 2, Đang rơi = 2, không có Trends = 3 |
| Kênh nhỏ đang nổ | 15 | ≥ 10 → 5; 5–9 → 4; 3–4 → 3; 1–2 → 2; 0 → 1. Cộng 1 nếu có kênh nổ dưới 6 tháng tuổi (tối đa 5) |
| Cạnh tranh (chấm ngược) | 10 | Cạnh tranh ≤ 10 → 5; ≤ 25 → 4; ≤ 40 → 3; ≤ 60 → 2; còn lại → 1 |
| Giá quảng cáo | 10 | Bậc RPM của thị trường × nhóm chủ đề |
| Tiền ngoài quảng cáo | 10 | Nhập tay (lấy từ số affiliate ở Module 1 làm gợi ý) |
| Sản xuất bền | 10 | Nhập tay |
| Rủi ro chính sách (chấm ngược) | 10 | Nhập tay; nhắc nếu nhóm chủ đề là y tế / tài chính / nhạy cảm |
| Mức thích của bạn | 5 | Nhập tay |
| Từ khoá dài | 5 | Nhập tay (Module 2 chưa có) |

**Điểm chốt** = Σ(trọng số × điểm) ÷ 5, thang 0–100.

### 4.3 Luật quyết định
- **Cờ đỏ:** có ô nào bằng 1 → loại khỏi vị trí ngách chính, bất kể tổng điểm.
- **Ngách chính:** điểm chốt cao nhất trong các ngách không có ô nào dưới 2.
- **Hoà** (chênh < 5 điểm): ngách có tổng "Sản xuất bền" + "Tiền ngoài quảng cáo" cao hơn thắng.
- **Ngách dự phòng:** ngách có điểm cao kế tiếp. Người dùng tick "gần khán giả / phong cách với ngách chính"; nếu không tick thì cảnh báo.
- **Lời hứa kênh:** bắt buộc 1 câu trước khi xuất kết quả.
- **Kế hoạch 10 video:** chia 3 trụ, mỗi trụ ít nhất 3 video. Mỗi video có ô nhập số sau 7 ngày (hiển thị, CTR, view, thời lượng xem trung bình, % giữ chân, sub mới).
  - Mốc video 5: hiện checklist sửa tiêu đề / ảnh bìa / hook.
  - Mốc video 10: gợi ý GIỮ / CHỈNH / CHUYỂN theo luật bài 2.5, ví dụ: hiển thị thấp → CHỈNH tiêu đề / ảnh bìa; CTR ổn nhưng giữ chân thấp → CHỈNH hook; sau khi đã chỉnh vẫn không video nào nhích → CHUYỂN.
- **Copy prompt:** "Phản biện bảng chấm" và "Kế hoạch 10 video thử", có sẵn dữ liệu bảng.

---

## 5. Mô hình dữ liệu (SQLite)

- `dig_run`: id, topic, market, language, window_days, format, category, created_at, niche_score, label, confidence, demand, opportunity, freshness, competition, trends_class, income_low, income_high, tier_demand, tier_supply, tier_money, verdict, affiliates_json, note, params_json
- `video`: run_id, video_id, channel_id, title, published_at, duration_s, views, outlier, fmt
- `channel`: channel_id, title, subs, subs_hidden, created_at, video_count, uploads_playlist, median_long, median_short, fetched_at
- `trend_series`: run_id, timeframe, source, series_json, fetched_at
- `api_cache`: key, response_json, expires_at
- `quota_log`: date, endpoint, units
- `scorecard`: id, name, version, weights_json, locked_at, promise, main_run_id, backup_run_id
- `scorecard_cell`: scorecard_id, run_id, criterion, score, auto_score, rationale
- `test_video`: scorecard_id, idx, pillar, title, published_at, impressions, ctr, views, avd, retention, new_subs, note

---

## 6. Các bước triển khai

| # | Mốc | Việc chính | Xong khi |
|---|---|---|---|
| 0 | **Spike kiểm chứng rủi ro** | Tạo API key; gọi thử search/videos/channels và đo unit thật; chạy pytrends từ máy bạn (VN), thử cả `gprop='youtube'` | Biết chính xác unit cho mỗi lần đào; Trends trả dữ liệu hoặc đã có phương án dự phòng |
| 1 | Khung dự án | pyproject, config, SQLite models, cache, quota log | `pytest` chạy; DB tạo được |
| 2 | Nguồn dữ liệu | `sources/youtube.py`, `trends.py`, `rpm.py`; ghi fixtures | Đào 1 chủ đề ra JSON đầy đủ; lần thứ 2 đọc từ cache |
| 3 | Engine tính điểm | Toàn bộ `core/` + unit test bằng fixtures | Test bao phủ mọi ngưỡng (3×/10×/50×, 70/50, 40/15, 4 loại Trends) |
| 4 | UI Đào ngách | Trang 1 + 2 + Cài đặt (API key, RPM, ngưỡng) | Đào → xem → lưu → đào lại so sánh được |
| 5 | Bảng chấm | Trang 3: khoá trọng số, gợi ý tự động, cờ đỏ, chọn chính/dự phòng | Làm lại đúng các ví dụ của bài 2.5: P 84 điểm có ô 1 → chọn Q 76 điểm; ví dụ "Tiền & Tâm Lý" ra 81/72/71 nếu nhập cùng điểm ô |
| 6 | Hoàn thiện | Xuất Excel, copy prompt, kế hoạch 10 video nhập tay | Chạy trọn quy trình: đào 3 ngách → chấm → chốt → in kế hoạch |

**Thứ tự quan trọng:** làm Mốc 0 trước. Nếu quota hoặc Trends không như kỳ vọng thì phải sửa thiết kế từ sớm.

## 7. Cần bạn chuẩn bị / quyết định
1. **YouTube Data API key:** Google Cloud Console → bật YouTube Data API v3 → tạo API key.
2. **Bảng RPM khởi đầu:** app tạo sẵn bảng số ước tính thô cho VN/US/JP/… theo nhóm chủ đề; bạn tự chỉnh dần theo số thật của mình.
3. **Thị trường mặc định:** đề xuất VN (vi).

## 8. Giả định app tự đặt (khóa học không công bố)
- Mốc view/giờ cho Độ mới; ngưỡng tự chấm Mạnh/Vừa/Yếu; quy tắc số cho 4 loại Trends; ngưỡng 1,5× để gắn nhãn Dài/Shorts; tiêu chí "kênh nhỏ nổ" = < 100K sub và có video ≥ 3×.
- Tất cả nằm trong `config.py` để chỉnh. Điểm của app **sẽ không trùng khớp** với DeepNiche, chỉ dùng để so các ngách với nhau trong cùng app.

---

## 9. Mở rộng sau MVP: Module 2 (Ngách con & từ khoá, bài 2.3) và Module 3 (So sánh, bài 2.4)

### Module 2: trang 🌿 Ngách con & từ khoá
- **Cụm lặp lại ở video thắng:** cụm 2–6 từ trong tiêu đề video vượt trội, xuất hiện ở ≥ 2 kênh (*), bỏ cụm chỉ gồm từ của chủ đề gốc.
- **Gợi ý tìm kiếm YouTube** (autocomplete, miễn phí, cache 24h, có mở rộng A–Z) chia 3 cột:
  - Ngách con: từ gốc ngắn + 1–3 từ.
  - Từ khoá dài: ≥ 4 từ / âm tiết (*), hoặc ≥ 8 ký tự với tiếng Nhật, Hàn, Thái, Trung.
  - Từ khoá ngắn: các gợi ý còn lại.
- **Đào sâu:**
  - Tối đa 3 ngách con mỗi lần, mỗi ngách ghi đúng một bộ lọc (đối tượng / vấn đề / góc kể / thị trường).
  - Lưu ngách mẹ và tầng (cột `parent_id`, `layer`, `filter_type` của `dig_run`).
  - Tầng ≥ 4 bị cảnh báo là chủ đề video.
  - "Mẫu còn ít" hoặc 0 video vượt trội → "quá hẹp, lùi lên một tầng".
- **Phiếu "đủ sống"** (5 ô):
  - còn video ≥ 3×
  - ≥ 1 kênh nhỏ nổ
  - 30 ý tưởng không trùng (bắt cả ý gần trùng ≥ 80% từ giống nhau)
  - lời hứa một câu
  - còn đường ra tiền (RPM vừa/cao hoặc có affiliate)
- **Điểm từ khoá dài** (*):
  - Điểm = 50% Nhu cầu + 50% (100 − Cạnh tranh).
  - Nhu cầu: trung vị view của 50 kết quả trong 1 năm, thang log 100 → 100K.
  - Cạnh tranh: 50% tỉ lệ kết quả từ kênh ≥ 100K sub + 50% tỉ lệ tiêu đề nhắm đúng từ khoá.
  - Dưới 10 kết quả có view → "ít dữ liệu".
  - Nhãn: ≥ 70 Rất tốt · 55–69 Tốt · 40–54 Trung bình.
  - Chi phí: ≈ 102 unit/từ, tối đa 10 từ mỗi lần chấm.
- **Bảng chấm:** ô "Từ khoá dài" được gợi ý tự động từ số từ khoá ≥ 55 không mang nhãn ít dữ liệu (≥ 10 → 5 điểm).

### Module 3: trang 🌍 So sánh
- **So ngách (7 cột):**
  - Tô ô tốt nhất mỗi cột; loại phương án tin cậy thấp.
  - Đọc chênh điểm: ≥ 10 rõ · 5–9 vừa · < 5 ngang · ≤ 3 nhiễu.
  - Hiện tỉ lệ lượt xem cao nhất / thấp nhất.
  - Cảnh báo khi khác thiết lập, đào cách nhau > 3 ngày, hoặc cỡ mẫu chênh ≥ 2 lần.
  - Ngang điểm thì quyết bằng Trends → kênh nhỏ nổ → affiliate.
  - Kết luận 3 câu: ngách dẫn đầu, cột tạo khác biệt, rủi ro lớn nhất; kèm "ngõ" của ngách thua.
  - Tạo bảng chấm ngay từ các ngách đang so.
- **Đa thị trường:**
  - 17 thị trường chia 4 nhóm; chạy 2–10 nước mỗi lần, cùng thiết lập.
  - App không tự dịch: có prompt dịch, chỗ dán "US: …", và nút kiểm tra từ khoá có người gõ không bằng gợi ý YouTube.
  - Điểm cơ hội = 45% điểm ngách + 30% tìm kiếm + 25% kiếm tiền; chưa có Trends thì 60% điểm ngách + 40% giá quảng cáo. Từ 65 là vùng xanh.
  - Tìm kiếm (*) = mức Trends 12 tháng ÷ phân vị 90 của 5 năm.
  - Kiếm tiền (*) = log RPM, 0,2 → 10 USD.
  - Hết quota giữa chừng thì dừng, giữ các nước đã xong và đào tiếp sau.
- **Prompt:** so ngách, so nhiều nước, kiểm tra nội dung chuẩn bản xứ.

(*) giả định của app, chỉnh trong Cài đặt → "Ngách con & từ khoá" và "So sánh".

## 10. Bổ sung sau khi đối chiếu lại khóa học

- **Phiếu 7 ô** (bài 2.2) hiện trên mỗi lần đào. Phiếu chỉ tính là đủ khi độ tin cậy vừa hoặc cao và đã có Trends. Trang So sánh cảnh báo khi một ngách chưa đủ 7 ô.
- **Lời khuyên hiện trên mỗi lần đào:**
  - theo vùng điểm (xanh / tiềm năng / đỏ) và theo độ tin cậy, kể cả cách xử lý "Mẫu còn ít"
  - ý nghĩa dấu ≈ (số ước tính)
  - chủ đề nên dài 2–4 từ
  - điều kiện bật kiếm tiền
  - định dạng thắng: dài làm chính, Shorts làm mồi; Shorts thì tính tiền bằng affiliate
- **Kênh nhỏ nổ:** đánh dấu kênh re-up và ghi "điểm lặp lại" (góc kể, thời lượng, ảnh bìa). Các nhận xét này được đưa vào prompt.
- **Đào lại riêng định dạng thắng** chỉ bằng 1 nút, rồi so điểm của các định dạng.
- **Bình luận dạng câu hỏi** ở 10 video vượt trội (bài 2.1), khoảng 1 unit mỗi video:
  - Xét từng câu: câu có dấu hỏi, có cụm hỏi rõ nghĩa, hoặc xin làm thêm video.
  - Với ngách kể chuyện, nhiều câu hỏi chỉ là câu hỏi tu từ.
- **So Google Trends ngách mẹ với tối đa 4 ngách con** trên cùng một thang. Ngách con đi lên khi ngách mẹ đi ngang hoặc giảm được đánh dấu ⭐.
- **Kế hoạch 10 video:** gợi ý từ khoá dài từ 55 điểm của ngách chính cùng các video vượt trội. Từ khoá được điền vào các tiêu đề còn trống và đưa vào prompt.
- **Theo dõi:**
  - Nhắc đào lại ngách chính, dự phòng và ngách trong bảng chấm sau mỗi 30 ngày, hiện ở thanh bên và trang 🧭 Tiến độ chặng.
  - Trang này cũng chấm 10 mục trong checklist tổng kết của chặng.
- **Còn khác với DeepNiche:**
  - App chỉ lấy dữ liệu từ YouTube, nên lời khuyên "thêm nền tảng" khi mẫu còn ít được thay bằng "nới khoảng thời gian / đổi từ khoá".
  - App không tự dịch từ khoá.


## 11. Radar từ khoá và thị trường theo ngôn ngữ

- **Hạt giống chính thống:** cây danh mục Google Trends (1.427 danh mục). Mỗi lượt quét một cặp thị trường × danh mục
  và lấy truy vấn hàng đầu / đang tăng của cả danh mục (không cần từ khoá, nguồn Web vì lọc danh mục trên nguồn YouTube
  không đáng tin). Luôn bỏ Tin tức, Người nổi tiếng, Tin đồn, Chính trị, Thời tiết, Sự kiện, TV Shows...; Nghệ thuật &
  Giải trí quét theo danh mục con.
- **Phễu lọc:** từ chặn → loại thực thể của Google (người, bài hát, show, đội, sự kiện) → hình dạng 5 năm (quá ít người
  tìm / tăng đột biến rồi tắt / vừa tăng vọt: kiểm lại sau 14 ngày / trào lưu / đang rơi) → điểm từ khoá YouTube trong
  ngân sách quota mỗi ngày (mặc định 1.000 unit).
- **Điểm tiềm năng:** 0,35 đà tăng + 0,25 độ bền + 0,25 YouTube + 0,15 số ngày lặp lại; ⭐ từ 65.
- **Chống 429:** mỗi lượt cách nhau ≈ 240 giây ±20% (≈ 360 lượt/ngày), còn từ khoá chờ thì kiểm tra trước; bị chặn thì
  nghỉ 10 phút. Chạy bằng thread nền khi app mở, hoặc Windows Task Scheduler (scripts/radar_tick.py, 2 phút/lần, tự bỏ
  qua khi chưa đến lượt); khoá trong DB chống chạy trùng.
- **Thị trường (một nơi duy nhất):** bảng `market` trong DB, sửa ở Cài đặt → Thị trường: toàn bộ 111 khu vực và 83 ngôn
  ngữ theo danh sách i18nRegions / i18nLanguages của YouTube (lưu ở app_meta), tick để bật, chọn ngôn ngữ, đặt nhóm; có
  "bật nhanh theo ngôn ngữ". Lúc khởi động nạp vào config: MARKETS (cả thị trường đã tắt, để tra dữ liệu cũ),
  ENABLED_MARKETS (mọi ô chọn thị trường dùng danh sách này), MARKET_GROUPS. Lần đầu tự tạo 17 thị trường mặc định và
  chuyển các dòng của bảng custom_market cũ. Bảng RPM chưa có số cho nước mới.
