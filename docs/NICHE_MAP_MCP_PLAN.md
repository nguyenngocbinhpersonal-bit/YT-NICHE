# Bản đồ ngách — Claude điều khiển app qua MCP

> Trạng thái: **đã code xong bước 1–6** trừ hai việc cần máy người dùng: chạy thử 3 entry thật trong Claude Code (cần YouTube API key) và adapter Apify (cần tên actor). Mục đích: chốt kiến trúc, phân vai app / Claude, lược đồ dữ liệu và các câu hỏi
> còn mở trước khi viết dòng code đầu tiên.
>
> Bối cảnh pháp lý: token OAuth của gói Pro/Max không được dùng trong ứng dụng bên thứ ba. Hướng này giữ Claude ở trong
> Claude Code (nơi gói subscription được phép dùng) và biến app thành một MCP server local do Claude gọi. App không bao
> giờ cầm token của Claude.

---

## 1. Phân vai: máy làm việc cơ học, Claude làm việc phán đoán

| Việc | Ai làm | Vì sao |
|---|---|---|
| Lấy dữ liệu YouTube / gợi ý tìm kiếm / Trends, cache, đếm quota | **App** | Đã có: `sources/`, `services/fetch.py`, `services/keywords.py`, `services/similar.py`, `services/radar.py` |
| Tính điểm ngách, điểm từ khoá, outlier, 3 tầng | **App** | Công thức cố định, test được (`core/`) |
| Xếp **tiềm năng** của các ngách trên bản đồ | **Claude**, sau khi đọc bảng số thật (`map_compare`) | Số đã nằm trong DB; Claude so và xếp. App lưu kèm **ảnh chụp số liệu** lúc xếp để gắn cờ `stale` khi số đổi |
| Lưu trữ, lịch sử, vẽ bản đồ | **App** | Streamlit đọc cùng file SQLite |
| Từ entry (từ khoá / kênh / URL / ngữ cảnh) suy ra từ khoá chính, liên quan, đuôi dài, trend | **Claude** đề xuất → **App** kiểm bằng dữ liệu thật → Claude chốt | Claude giỏi mở rộng ngữ nghĩa; số liệu thật quyết định giữ hay bỏ |
| Đặt tên ngách, dựng taxonomy mẹ → con, gán động cơ khán giả, gộp ngách gần nhau | **Claude** (ghi vào DB kèm lý do) | Việc phân loại và đặt tên là phán đoán |
| Cập nhật đối thủ (kênh mới nổ, kênh rơi) | **App** quét → Claude đọc và quyết định có đổi phân loại không | |

Nguyên tắc 1: **một ngách = một ngôn ngữ**. `niche.language` cố định lúc tạo; service từ chối từ khoá, lần đào, ngách
mẹ và ngách gộp khác ngôn ngữ. Tool quét đối thủ trả `country` / `default_language` và cờ `language_mismatch` để Claude
loại kênh lẫn. Cùng chủ đề ở ngôn ngữ khác là ngách khác, so nhau trên `map_compare`.

Nguyên tắc 2: **mọi quyết định của Claude được lưu như một sự kiện có lý do**, không ghi đè thầm lặng. Người dùng đọc lại được
"vì sao ngách A bị gộp vào B hôm 12/11".

---

## 2. Kiến trúc

```
Claude Code (subscription)                 Streamlit (trình duyệt)
   │  skill: yt-niche                            │
   │  stdio MCP                                  │ đọc / vẽ
   ▼                                             ▼
yt_niche/mcp/server.py  ──── gọi ────▶  yt_niche/services/*  ◀──── yt_niche/ui/*
                                              │
                                              ▼
                                       data/yt_niche.db (SQLite, WAL)
```

- **MCP server**: Python, gói `mcp` (FastMCP), transport stdio. Khai báo trong `.mcp.json` ở gốc repo để Claude Code tự bật
  khi mở thư mục này. Server chỉ là lớp mỏng gọi `services/`, không chứa logic tính toán.
- **Skill**: `.claude/skills/yt-niche/SKILL.md` mô tả quy trình làm việc, định nghĩa 10 động cơ, tiêu chí gộp ngách, và
  cách tiết kiệm quota. Skill là "sách hướng dẫn" cho Claude; tool là "tay chân".
- **UI**: thêm trang `Bản đồ ngách` (Plotly treemap / bubble). Không thêm nút gọi AI nào trong Streamlit.
- **Đồng thời**: DB đã bật WAL, hai tiến trình (MCP server và Streamlit) đọc ghi cùng lúc được. Cần thêm
  `busy_timeout` trong `db/connection.py` để tránh lỗi "database is locked" khi Claude ghi lúc trang đang vẽ.

---

## 3. Lược đồ dữ liệu mới

Giữ nguyên các bảng cũ (`dig_run`, `keyword`, `channel`, `similar_run`, radar). Thêm:

```sql
niche (
  id, slug, name, language, parent_id,    -- cây: ngách lớn → ngách → ngách con; một ngách = một ngôn ngữ
  motivation_primary, motivation_secondary, -- 1 trong 10 động cơ, phụ có thể NULL
  status,                                 -- draft | active | merged | dropped
  merged_into,                            -- niche.id khi status = merged (gộp mềm, mở lại được)
  potential, potential_rationale, basis_json, ranked_at,  -- Claude xếp qua niche_rank; basis = ảnh chụp số lúc xếp
  summary,                                -- 2–3 câu Claude viết: khán giả, vấn đề, góc kể
  created_at, updated_at
)
niche_keyword (
  niche_id, region, keyword,
  kind,                                   -- main | related | longtail | trend
  demand, competition, score, n,          -- copy từ bảng keyword / radar lúc gắn, có scored_at
  scored_at, source                       -- suggest | az | radar | claude | manual
)
niche_run      (niche_id, run_id)         -- lần đào nào thuộc ngách nào
niche_channel  (niche_id, channel_id, role, note, updated_at)   -- role: leader | rising | fallen
niche_event (
  id, niche_id, kind,                     -- create | rename | move | merge | split | motivation | override | drop
  payload_json, rationale, actor,         -- actor: claude | user
  created_at
)
```

**Tiềm năng** (`niche.potential`, 0–100) do Claude xếp qua `niche_rank` sau khi đọc `map_compare`. App không có công
thức; thay vào đó lưu `basis_json` (ảnh chụp `signals` lúc xếp) và `ranked_at`. Khi thêm lần đào, hoặc điểm đào / nhu cầu
từ khoá main lệch quá 10 điểm so với ảnh chụp, ngách được gắn cờ `stale` và skill phải xếp lại. Ngách chưa có số
(`n_main_scored = 0` và `n_runs = 0`) không được xếp và vẽ nhỏ, mờ.

---

## 4. Bộ tool MCP (phiên bản 1)

Nhóm **đọc** (rẻ, không tốn quota):

| Tool | Trả về |
|---|---|
| `quota_status()` | unit YouTube đã dùng hôm nay, còn lại, trạng thái cooldown Trends, số request AI/ngày |
| `map_list(status?)` | toàn bộ cây ngách kèm potential, số từ khoá, số kênh |
| `niche_get(id)` | chi tiết một ngách: từ khoá theo loại, run, kênh, 10 sự kiện gần nhất |
| `runs_list(filter)` / `run_get(id)` | các lần đào đã có |
| `radar_keywords(status, region)` | từ khoá radar đang chờ hoặc đã đánh giá tốt |

Nhóm **lấy dữ liệu thật** (tốn quota, tool trả luôn chi phí đã tiêu):

| Tool | Gọi tới | Chi phí |
|---|---|---|
| `keywords_suggest(seed, region, az=false)` | `services.keywords.fetch_suggestions` | 0 unit |
| `keywords_score(region, keywords[])` | `services.keywords` (chấm bằng search thật) | ~102 unit / từ, tối đa 10 từ / lần |
| `niche_dig(topic, region, window, fmt, parent_run?)` | `services.runs.dig` | ~210–350 unit |
| `similar_from_url(url, region)` | `services.similar` | ~10–30 unit |
| `trends_classify(keywords[], region)` | `sources.trends` | 0 unit, dễ 429 |
| `competitors_refresh(niche_id)` | quét lại kênh trong `niche_channel` + kênh nhỏ nổ ở run mới | ~2 unit / kênh |

Nhóm **ghi quyết định** (Claude là actor, bắt buộc có `rationale`):

| Tool |
|---|
| `niche_upsert(name, parent_id?, motivation_primary, motivation_secondary?, summary, rationale)` |
| `niche_attach_keywords(niche_id, [{keyword, region, kind}], rationale)` |
| `niche_attach_run(niche_id, run_id)` |
| `niche_move(niche_id, new_parent_id, rationale)` |
| `niche_merge(source_id, target_id, rationale)` — gộp mềm, từ khoá và run chuyển sang target |
| `niche_set_motivation(niche_id, primary, secondary?, rationale)` |
| `niche_override_potential(niche_id, value, rationale)` |
| `niche_drop(niche_id, rationale)` |

Quy ước chung: tool ghi trả về bản ghi sau khi ghi; tool tốn quota từ chối chạy nếu quota còn dưới ngưỡng an toàn (mặc định
giữ lại 1.500 unit cho Streamlit) trừ khi gọi với `force=true`.

---

## 5. Quy trình trong skill (tóm tắt SKILL.md)

```
1. Nhận entry: từ khoá | kênh | URL video | mô tả ngữ cảnh.
2. quota_status() → quyết định ngân sách cho phiên này.
3. Suy ra ứng viên từ khoá:
   - từ khoá → keywords_suggest (0 unit) + đề xuất ngữ nghĩa của Claude
   - kênh / URL → similar_from_url → tiêu đề video thắng → Claude rút cụm → keywords_suggest
   - ngữ cảnh → Claude đề xuất 5–10 từ gốc → keywords_suggest từng từ
4. Phân loại ứng viên: main / related / longtail / trend (trend = radar hoặc trends_classify).
5. keywords_score cho tối đa 10 từ quan trọng nhất (có số thật mới được gắn kind=main).
6. niche_dig 1–3 từ main để có điểm ngách. Ngách đã có run trong 90 ngày thì không đào lại.
7. Dựng / cập nhật taxonomy: tìm ngách gần (map_list), quyết định tạo mới, gắn vào, hay gộp.
   Tiêu chí gộp: ≥ 60% từ khoá main+related trùng, cùng động cơ chính, cùng thị trường.
8. Gán động cơ (1 chính, tối đa 1 phụ) theo định nghĩa ở mục 6.
9. Ghi bằng tool ghi, mỗi lệnh kèm rationale 1–2 câu.
10. Kết thúc: báo người dùng quota đã tiêu, ngách nào thay đổi, ngách nào cần đào thêm.
```

Cập nhật định kỳ ("cập nhật ngách"): skill có chế độ `refresh` — với mỗi ngách active, chạy `competitors_refresh`, chấm lại
từ khoá main quá 30 ngày, `niche_refresh_numbers`, rồi `map_compare` và chỉ xếp lại ngách có cờ `stale`.

---

## 6. Mười động cơ khán giả — định nghĩa để Claude phân loại nhất quán

| Mã | Động cơ | Câu hỏi khán giả đang hỏi | Dấu hiệu trong từ khoá |
|---|---|---|---|
| learn | Học | "X là gì / hoạt động thế nào" | giải thích, là gì, lịch sử, cơ chế |
| earn | Kiếm tiền | "làm sao kiếm được tiền từ X" | kiếm tiền, đầu tư, nghề, thu nhập |
| fix | Sửa vấn đề | "X bị lỗi, sửa sao" | cách sửa, lỗi, không được, khắc phục |
| improve | Cải thiện | "làm X tốt hơn" | mẹo, tối ưu, nâng cao, thói quen |
| believe | Niềm tin & ý nghĩa | "điều này có ý nghĩa gì với tôi" | tâm linh, triết lý, đạo đức, mục đích |
| connect | Kết nối | "người khác giống tôi thế nào" | cộng đồng, vlog đời thường, phản ứng |
| escape | Trốn thoát | "cho tôi quên đi một lúc" | giải trí thuần, gaming, hài, ASMR |
| experience | Trải nghiệm | "cảm giác X ra sao" | review, thử, du lịch, unboxing |
| updated | Cập nhật | "có gì mới về X" | tin, cập nhật, mới nhất, tuần này |
| inspired | Cảm hứng | "cho tôi động lực" | câu chuyện, hành trình, động lực |

Quy tắc: nhìn vào **ý định của từ khoá main**, không phải chủ đề. "Tâm lý học" có thể là learn (giải thích hiệu ứng) hoặc
improve (thói quen tốt) tuỳ từ khoá thắng. Khi 2 động cơ ngang nhau, chọn cái có demand từ khoá cao hơn làm chính.

---

## 7. Trang Bản đồ ngách (Streamlit) — đã làm, giao diện tiếng Anh

Mã hoá theo quy tắc dữ liệu-hình (một biến một kênh; treemap là dạng mọi ô kề nhau nên không dùng 10 màu động cơ):

| Kênh | Mang gì |
|---|---|
| Diện tích | tiềm năng Claude xếp; chưa xếp → ô nhỏ, xám |
| Màu (chọn được) | **Opportunity** = nhu cầu − cạnh tranh, thang phân kỳ đỏ ↔ xám ↔ xanh (mặc định) · **Demand** xanh nhạt → đậm · **Data freshness** xanh cũ → mới |
| Viền đỏ + ⚠ | số liệu đã đổi so với lúc xếp (stale) |
| Nhóm (chọn được) | Motivation · Potential band · Niche tree · Language · Trend |

Bố cục: 5 thẻ số (số ngách, tiềm năng cao nhất, cần xếp lại, ngôn ngữ, độ mới), hai hàng bộ lọc, treemap bo góc với
nhãn thống nhất (tên, số tiềm năng lớn, động cơ · ngôn ngữ · opp), chú giải dưới biểu đồ, bảng chi tiết trong khung
(huy hiệu ngôn ngữ / động cơ / trạng thái, 5 số đo, lý do xếp hạng, 4 tab: Keywords với thanh tiến trình, Competitors,
Digs, Decision log), nút Unmerge cho ngách đã gộp, và bubble view (x = demand, y = 100 − competition, cỡ = tiềm năng,
màu = ngôn ngữ, tối đa 3 ngôn ngữ theo bảng màu phân loại đã kiểm). Theme app trong `.streamlit/config.toml`.

Chỉ trang này tiếng Anh; các trang còn lại của app vẫn tiếng Việt (việc dịch toàn app là bước riêng, chưa làm).

---

## 8. Phản biện ý tưởng (những chỗ tôi nghĩ bạn nên cân nhắc lại)

1. **Tiềm năng do Claude xếp** (đã chốt với người dùng): chấp nhận vì Claude xếp trên số thật đã nằm trong DB, không
   bịa. Điều kiện đi kèm để bản đồ vẫn tái lập được: mỗi lần xếp lưu ảnh chụp số liệu + lý do nêu con số; số đổi thì
   cờ `stale`; ngách không có số thì không xếp. Thang 0–100 là tương đối giữa các ngách, nên khi thêm ngách mới nên
   xếp lại cả bảng chứ không chỉ ngách mới.
2. **Quota là nút thắt thật.** Một ngách "đầy đủ" (10 từ chấm + 2 lần đào) ≈ 1.600 unit. Quota 10.000/ngày → **~6 ngách/ngày**.
   Bản đồ 50 ngách mất hơn một tuần, và cập nhật toàn bộ cũng mất chừng đó. Skill phải ưu tiên: chỉ chấm từ khoá main,
   dùng gợi ý tìm kiếm (0 unit) cho related/longtail, dùng radar cho trend. **Người dùng đã có Apify key** (thư mục
   `.secrets/`, không commit): bước sau thêm `sources/apify.py` làm nguồn thay thế cho search / chi tiết kênh khi quota
   YouTube cạn, tool lấy dữ liệu nhận tham số `source="youtube"|"apify"`. Cần biết actor Apify nào đang dùng để viết
   adapter đúng đầu ra.
3. **Gộp ngách là quyết định dễ sai, khó nhìn thấy.** Vì vậy gộp mềm (`merged_into`), có sự kiện, có nút tách lại trong UI.
   Không bao giờ xoá dòng.
4. **Mười động cơ không loại trừ nhau.** Ngách "đầu tư crypto cho người mới" vừa earn vừa learn. Cho phép 1 chính + 1 phụ,
   treemap vẽ theo chính. Nếu bạn muốn một ngách xuất hiện ở hai nhánh, treemap không làm được; bubble chart thì được.
5. **Entry bằng "ngữ cảnh của agent"** là hay nhưng mơ hồ nhất. Tôi đề xuất giới hạn: Claude được tự đề xuất từ gốc, nhưng
   từ khoá chỉ được gắn vào ngách khi có số liệu thật (suggest hoặc score). Không có số thì ở trạng thái `draft`.
6. **Trends 429** đã là vấn đề quen thuộc trong app. Skill không nên gọi `trends_classify` cho mọi từ; chỉ cho từ main.
7. **Hai tiến trình ghi SQLite**: cần `busy_timeout` và mọi tool ghi dùng transaction ngắn. Rủi ro thấp nhưng phải làm từ đầu.

---

## 9. Thứ tự triển khai đề xuất

| Bước | Nội dung | Ước lượng |
|---|---|---|
| 1 | ✅ Bảng mới + `services/niche_map.py` (CRUD, merge mềm, signals, rank + stale, event log) + 7 test | xong |
| 2 | ✅ `yt_niche/mcp/server.py` (28 tool: đọc, lấy dữ liệu có chặn quota, ghi) + `.mcp.json` | xong |
| 3 | ✅ Bọc suggest / score / dig / similar / competitors_refresh kèm chặn quota (gộp vào bước 2) | xong |
| 4 | ✅ `.claude/skills/yt-niche/SKILL.md` (bản 1) · ⏳ thử 3 entry thật trong Claude Code, chỉnh skill theo lỗi gặp | 1 buổi |
| 5 | ✅ Trang Bản đồ ngách: treemap 5 chế độ, bộ lọc, viền đỏ stale, chi tiết 4 tab, tách lại, bubble; 2 test UI | xong |
| 6 | ✅ Chế độ `refresh` trong skill · ⏳ chạy thử với API key thật trên máy người dùng · ⏳ `sources/apify.py` (cần tên actor + mẫu đầu ra) | nửa buổi |

Bước 1–2 chạy được là đã thử được ý tưởng cốt lõi với Claude Code mà chưa cần UI.

---

## 10. Quyết định đã chốt / mặc định đang dùng

| Câu hỏi | Quyết định |
|---|---|
| Tiềm năng | **Claude xếp** trên số thật (người dùng chốt). App lưu ảnh chụp + cờ stale |
| Thị trường / ngôn ngữ | **Một ngách = một ngôn ngữ** (người dùng chốt). Cây chung cho mọi ngôn ngữ; thị trường cùng ngôn ngữ (US/GB/CA/AU) dùng chung một ngách; treemap có chế độ nhóm theo ngôn ngữ |
| Độ sâu cây | Không giới hạn trong DB; treemap vẽ 3 tầng (tầng sâu hơn gộp vào tầng 3) |
| Quota | Giữ 1.500 unit cho giao diện (`config.MCP_QUOTA_RESERVE`); Apify là nguồn dự phòng ở bước sau |
| Vị trí skill | **Trong repo** `.claude/skills/yt-niche/` (`.gitignore` đã mở cho thư mục này; phần còn lại của `.claude/` vẫn bỏ qua) |
| Thư viện MCP | `mcp>=2,<3` (API `MCPServer`), cài bằng `pip install -e ".[mcp]"` |

## 11. Cách chạy thử bước 1–2

```bash
pip install -e ".[dev,mcp]"
python -m pytest -q tests/test_niche_map.py
```

Mở Claude Code trong thư mục repo: `.mcp.json` bật server `yt-niche`, skill `yt-niche` tự nạp. Thử:
"dùng skill yt-niche, xây ngách từ từ khoá 'tâm lý học' thị trường VN".
