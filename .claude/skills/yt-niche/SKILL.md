---
name: yt-niche
description: Dựng và cập nhật bản đồ ngách YouTube bằng MCP server yt-niche. Dùng khi người dùng đưa một từ khoá, một kênh, một URL video, hoặc mô tả ngữ cảnh và muốn xây ngách, phân loại ngách theo động cơ khán giả, gộp ngách, xếp tiềm năng, hoặc cập nhật đối thủ. Cũng dùng khi được hỏi "ngách nào tiềm năng nhất", "cập nhật bản đồ", "ngách này thuộc động cơ gì".
---

# Bản đồ ngách YouTube qua MCP

Bạn là người phán đoán; app là người đo. Bạn không bao giờ tự bịa số: mọi con số (nhu cầu, cạnh tranh, điểm đào,
trend) đến từ tool của MCP server `yt-niche`. Bạn đặt tên, dựng cây, gán động cơ, gộp, và xếp tiềm năng **sau khi**
đọc số thật từ DB. Mỗi quyết định ghi kèm `rationale` 1–2 câu bằng tiếng Việt.

## Một ngách = một ngôn ngữ

Mỗi ngách có `language` cố định (vi, en, ja…), chọn ngay khi tạo. Mọi dữ liệu của ngách phải cùng ngôn ngữ đó;
app chặn ở DB nhưng bạn phải chọn đúng từ đầu để không tốn quota vào dữ liệu bỏ đi:

- **Chọn ngôn ngữ ở bước 1**, trước khi lấy bất kỳ dữ liệu nào. Entry là từ khoá → ngôn ngữ của từ khoá. Entry là
  kênh / URL → ngôn ngữ tiêu đề video gốc. Entry là ngữ cảnh → hỏi người dùng nếu chưa rõ.
- Mọi lời gọi `keywords_suggest`, `keywords_score`, `niche_dig`, `similar_from_url` dùng **region thuộc ngôn ngữ đó**
  (`niche_get` trả `regions_allowed`; en = US, GB, CA, AU, PH; vi = VN; ja = JP…). Không gọi region khác ngôn ngữ
  "để xem thêm".
- Đọc kết quả `similar_from_url`: kênh có `country` ngoài `regions_allowed`, hoặc tiêu đề / tag bằng ngôn ngữ khác,
  thì **bỏ**: không lấy làm đối thủ, không rút từ khoá từ nó. `competitors_refresh` gắn cờ `language_mismatch`
  cho kênh khai quốc gia / ngôn ngữ khác ngách; kênh như vậy không đưa vào `niche_set_channels` trừ khi bạn đã xem và
  thấy nội dung đúng ngôn ngữ (ghi rõ trong rationale).
- Cùng chủ đề ở ngôn ngữ khác là **ngách khác**, tạo riêng ("Tâm lý học" vi và "Psychology facts" en là hai ngách).
  Không gộp, không làm con của nhau. Muốn so hai ngôn ngữ thì so hai ngách trên `map_compare`.
- Từ khoá dịch: dùng `translate_keywords` của app (hoặc tự dịch) rồi lấy số thật ở thị trường đích; không gắn từ
  khoá tiếng Việt vào ngách tiếng Anh dù nghĩa giống nhau.

## Ngân sách trước, dữ liệu sau

1. Gọi `quota_status` đầu phiên. `spendable` là số unit YouTube được tiêu trong phiên này.
2. Chi phí: `keywords_suggest` 0 unit; `keywords_score` ≈102 unit/từ; `niche_dig` ≈210–350; `similar_from_url` ≈10–30;
   `competitors_refresh` ≈2/kênh. Tool tự từ chối nếu ăn vào phần giữ cho giao diện; chỉ dùng `force=true` khi người
   dùng đồng ý rõ ràng.
3. Ưu tiên rẻ trước: gợi ý tìm kiếm (0 unit) cho related/longtail, `radar_keywords` cho trend, `keywords_list`
   và `runs_list` để tái dùng số đã có. Một ngách đã có lần đào trong 90 ngày thì `niche_dig` tự trả lại lần đó.

## Quy trình xây / cập nhật một ngách

```
1. Nhận entry: từ khoá | kênh | URL video | mô tả ngữ cảnh. Chốt ngôn ngữ và region sẽ dùng xuyên suốt.
2. Rút từ gốc (seed), cùng ngôn ngữ với entry:
   - từ khoá        → chính nó + 2–4 biến thể bạn suy ra
   - kênh / URL     → similar_from_url → đọc seed_titles, queries, keywords của kênh gốc → rút 3–6 cụm lặp lại
   - ngữ cảnh       → bạn đề xuất 5–10 từ gốc, nêu rõ là giả thuyết
3. keywords_suggest cho từng seed (0 unit). Gom ứng viên.
4. Phân loại ứng viên theo ý định:
   main     = 2–5 từ ngắn, đại diện ngách, sẽ chấm bằng số thật
   related  = cùng chủ đề, khác góc
   longtail = ≥ 4 từ, câu hỏi / tình huống cụ thể
   trend    = có trong radar_keywords hoặc gắn sự kiện / sản phẩm mới
5. keywords_score cho tối đa 10 từ main (chỉ từ main mới cần số thật). Từ nào low_data thì hạ xuống related.
6. niche_find_by_keywords với các từ main → đã có ngách chứa chúng chưa?
   - có, cùng ngôn ngữ, trùng ≥ 60% main+related, cùng động cơ chính → gắn vào ngách đó (hoặc niche_merge nếu là 2 ngách)
   - có, trùng ít                                         → tạo ngách con hoặc ngách anh em
   - không                                                 → niche_create
7. niche_dig 1–2 từ main quan trọng nhất (nếu chưa có run) → niche_attach_run.
8. niche_attach_keywords (kind đúng, số sẽ được copy tự động).
9. niche_set_motivation theo bảng dưới. Ghi 1 chính, tối đa 1 phụ.
10. Kênh: từ run (small_boom_channels, top_outliers) và similar_from_url → niche_set_channels với role.
11. Kết thúc phiên: map_compare → niche_rank cho các ngách vừa chạm tới và ngách bị stale.
12. Báo người dùng: quota đã tiêu, ngách nào tạo / gộp / đổi hạng, ngách nào còn thiếu số.
```

## Xếp tiềm năng (niche_rank)

Chỉ xếp sau khi đọc `map_compare`. Thang 0–100 là **so sánh tương đối giữa các ngách trên bản đồ**, không phải điểm
tuyệt đối. Cân nhắc theo thứ tự: nhu cầu từ khoá main và trung vị view → cạnh tranh (càng thấp càng tốt) và số kênh
nhỏ đang nổ → trend (bền > theo mùa > trào lưu > đang rơi) → điểm đào và verdict 3 tầng → tiền (income_per_video).
Ngách thiếu số (`n_main_scored = 0` và `n_runs = 0`) không xếp; nói rõ là thiếu dữ liệu. Ngách `stale = true` phải
xếp lại. `rationale` nêu con số bạn dựa vào, ví dụ: "demand 68, competition 22, 4 kênh nhỏ nổ, trend bền; cao hơn
Tâm lý tiền bạc (demand 51, competition 40)".

## Mười động cơ khán giả

| Mã | Khán giả đang hỏi | Dấu hiệu trong từ khoá main |
|---|---|---|
| learn | X là gì, hoạt động thế nào | là gì, giải thích, lịch sử, cơ chế, tại sao |
| earn | làm sao kiếm tiền từ X | kiếm tiền, đầu tư, nghề, thu nhập, bán |
| fix | X hỏng, sửa sao | cách sửa, lỗi, không được, khắc phục |
| improve | làm X tốt hơn | mẹo, tối ưu, nâng cao, thói quen, luyện |
| believe | điều này có ý nghĩa gì với tôi | tâm linh, triết lý, đạo đức, mục đích sống |
| connect | người giống tôi sống thế nào | vlog, một ngày của, phản ứng, cộng đồng |
| escape | cho tôi quên đi một lúc | giải trí thuần, gaming, hài, ASMR, nhạc |
| experience | cảm giác X ra sao | review, thử, du lịch, unboxing, ăn thử |
| updated | có gì mới về X | tin, cập nhật, mới nhất, tuần này, leak |
| inspired | cho tôi động lực | câu chuyện, hành trình, từ 0 đến, động lực |

Nhìn vào **ý định của từ khoá main**, không phải chủ đề. Hai động cơ ngang nhau → chọn cái có demand cao hơn làm chính.

## Tiêu chí gộp (niche_merge)

Gộp khi cả ba: cùng ngôn ngữ, trùng ≥ 60% từ khoá main+related, cùng động cơ chính. Gộp là gộp mềm, tách lại
được bằng `niche_unmerge`, nhưng vẫn phải nêu rõ trong rationale hai ngách trùng ở đâu. Không gộp ngách mẹ vào con.

## Chế độ cập nhật ("cập nhật bản đồ", "refresh")

1. `map_list` → với mỗi ngách active: `competitors_refresh` (rẻ), đọc kênh nào tăng / rơi → `niche_set_channels`.
2. Từ khoá main có `scored_at` quá 30 ngày → `keywords_score` lại (tôn trọng quota), rồi `niche_refresh_numbers`.
3. `map_compare` → xếp lại mọi ngách `stale = true`. Không chạm ngách không đổi.
4. Báo: ngách nào lên / xuống hạng và vì sao.

## Những điều không làm

- Không trộn ngôn ngữ: không gắn từ khoá / lần đào / kênh khác ngôn ngữ vào ngách; không gộp ngách khác ngôn ngữ.
- Không gắn `kind=main` cho từ chưa có số. Không xếp hạng ngách chưa có số.
- Không đào lại ngách đã có run < 90 ngày trừ khi người dùng yêu cầu `fresh`.
- Không xoá gì; `niche_drop` và `niche_merge` đều giữ dữ liệu.
- Không gọi `force=true` mà không hỏi người dùng.
