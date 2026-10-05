# K4-Track02-Day17 — Report cá nhân

>**Họ tên / MSSV:** Hoàng Thái Đạt / 2A202602959

>**Repo:** https://github.com/Liber72/K4-Track02-Day17-HoangThaiDat-2A202602959-DataPipelineEngineering

>**Commit bài nộp:**

>**AI đã dùng và phạm vi hỗ trợ:** Codex, dùng để  đọc code, tóm tắt giải thích phân tích baseline, gợi ý những cách sửa pipeline, đề xuất cách sửa.

>**Nguồn tham khảo:** README và các tài liệu trong docs/; Gợi ý của AI-Agent.

## 1. Ba lỗi

| | Silver | Late data | CDC delete |
|---|---|---|---|
| Triệu chứng | Baseline 24 hàng/12 ticket; T-91 có ba trạng thái; số chunks bị nhân lên. | u05 ngày 08-12 chỉ có (2 events, 1 click, 0 down); feature checksum c50b8851affe khác recompute 8630e04a61d1. | T-97 không bị đánh dấu xóa; snapshot mới nhất vẫn có 1 hàng, RAG còn 2 chunks. |
| Nguyên nhân | INSERT nối batch; dedup nội bộ không bảo đảm duy nhất giữa batch. | Lookback=0; event đến ngày 08-15 không cập nhật partition 08-12. | Staging chỉ lấy khóa từ after; delete có after=null bị lọc bỏ. |
| Cách sửa | silver.py: keyed MERGE, chỉ update nếu LSN mới hơn; dedup có tie-break ingest/offset. | config.py: LOOKBACK_DAYS=3 từ ceil(P99); giữ overwrite partition theo event time. | staging.py: coalesce khóa after/before/key; dữ liệu cá nhân vẫn lấy từ after nên thành null khi xóa. MERGE giữ tombstone và LSN. |
| Khái niệm | Silver có khóa, idempotency, thứ tự CDC. | Event time khác ingest time, late data, lookback. | CDC delete khác Kafka tombstone; xóa phải lan và replay không hồi sinh dữ liệu. |

## 2. Các con số

- Bronze 43 records: P50=0, P95=2,90, P99=3,00 ngày, max=3 → LOOKBACK_DAYS=3.
- Verify: 18/18 ALL PASS; pytest: 34 passed; dbt: PASS=19; parity: PARITY.
- Fresh build và 3 rerun: Gold 39e115c510ecdf526800eac227158a4f giống nhau, checksum file PASS.
- Sau sửa: T-91 high/closed/bug; u05 08-12 (5 events, 3 clicks, 1 down); T-97 tombstone, không còn trong Gold mới nhất/chunks.

## 3. Lựa chọn kỹ thuật

- Keyed MERGE cho trạng thái ticket; overwrite partition cho aggregate vì phải tính lại cả ngày khi event đến muộn.
- Tombstone giữ khóa/LSN để chặn batch cũ hồi sinh ticket; đổi lại phải quản lý retention và không coi tombstone là xóa toàn bộ dữ liệu nguồn.
- Snapshot dựng từ Bronze as-of và feedback đã đến lúc đó để tái lập, tránh dùng dữ liệu tương lai; không tự sửa version cũ.
- DuckDB phù hợp dữ liệu nhỏ trên một máy; dbt bổ sung contracts/tests và cách triển khai SQL độc lập. Spark chưa có lợi ích đủ bù vận hành.
- Lookback 3 ngày bao phủ P99 và max của seed; production cần phát hiện event vượt cửa sổ và backfill riêng.

## 4. Hai câu hỏi suy ngẫm

1. Snapshot cũ chứa T-97 là giới hạn có chủ đích của lab. Production cần registry lineage, thu hồi snapshot bị ảnh hưởng, tạo version đã xóa/redact, và xử lý Bronze/transcripts/cache/index/backup theo retention. Bất biến không phải lý do bỏ qua yêu cầu xóa; giữ audit không chứa nội dung cá nhân.
2. Đặt chốt PII Bronze→Silver: regex định danh kết hợp NER tiếng Việt cho tên/địa chỉ, quarantine khi không chắc; kiểm lại trước Gold. Đo precision/recall và tỷ lệ rò trên tập gán nhãn, ưu tiên recall dữ liệu nhạy cảm. Bronze phải giới hạn quyền và thời hạn lưu.

## 5. Output thực tế

Output bên dưới lấy từ lệnh chạy trên code đã sửa ngày 04/10/2026. Windows bật PYTHONUTF8=1; pytest/dbt chạy ngoài sandbox vì thư mục tạm và worker bị sandbox chặn. Venv ban đầu không có pip, nên cài dbt bằng uv pip install --python .venv/Scripts/python.exe -r requirements-dbt.txt. Giữ nguyên tests, seed, verify, rerun checker và checksum.


```text
python -m scripts.verify
=== verify.py — Day 17 pipeline contracts ===
  [OK ] Bronze  every daily batch landed as Parquet (7 days x 3 sources)
  [OK ] Bronze  re-landing a batch is a no-op (append-only, no duplicate file)
  [OK ] Bronze  Bronze keeps the raw truth: Kafka tombstone + redelivered events are still there
  [OK ] Silver  silver_tickets has exactly one row per ticket_id
  [OK ] Silver  T-91 shows its latest state: high / closed / bug
  [OK ] Silver  deleted ticket T-97 is a tombstone: is_deleted and no personal data left
  [OK ] Silver  no email / phone number survives past Bronze
  [OK ] Silver  silver_events has one row per event_id (Kafka redeliveries removed)
  [OK ] Silver  2 malformed events quarantined with a reason; the run did not halt
  [OK ] Gold    gold_feature_daily reconciles with a full recompute from Silver
  [OK ] Gold    u05's offline events of 08-12 (arrived 08-15) are counted on 08-12
  [OK ] Gold    LOOKBACK_DAYS covers measured P99 lateness (p99=3.00 days)
  [OK ] Gold    training set uses point-in-time priority (T-91 created as 'low')
  [OK ] Gold    late feedback creates a NEW snapshot version; the old one is untouched
  [OK ] Gold    latest training snapshot excludes the deleted ticket T-97
  [OK ] Gold    deletes propagate to the RAG index: no chunk of T-97
  [OK ] Gold    gold_doc_chunks: one row per chunk, and a re-run embeds 0 new chunks
  [OK ] Rerun   re-run 2026-08-12 three times -> Gold checksum identical to a fresh build

RESULT: 18/18 checks — ALL PASS
re-run checksums written to submission/checksums.txt
```

```text
python -m pytest -q -o addopts=''
..................................                                       [100%]
34 passed in 2.56s
```

```text
python -m scripts.rerun_check
# Lab 17 — re-run check for 2026-08-12

run                     gold_feature_daily    gold_training_set     gold_doc_chunks       gold (combined)
fresh build             8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f
re-run #1 of 2026-08-12 8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f
re-run #2 of 2026-08-12 8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f
re-run #3 of 2026-08-12 8630e04a61d1          9370ca77af23          cb9ebd12fdcc          39e115c510ecdf526800eac227158a4f

RESULT: PASS — 3 re-runs, identical checksums
```

```text
python main.py --lateness
event lateness over 43 Bronze records (calendar days): p50=0.00 p95=2.90 p99=3.00 max=3
-> lookback must be >= ceil(p99) = 3 day(s); config.LOOKBACK_DAYS = 3
```

```text
dbt build --profiles-dir . --event-time-start 2026-08-10 --event-time-end 2026-08-17 --no-use-colors (cwd: dbt_project)
03:42:57  Running with dbt=1.12.5
03:42:58  Registered adapter: duckdb=1.11.0
03:42:59  Found 5 models, 13 data tests, 2 sources, 502 macros, 1 unit test
03:42:59
03:42:59  Concurrency: 1 threads (target='dev')
03:42:59
03:42:59  1 of 19 START sql view model main.stg_events ................................... [RUN]
03:42:59  1 of 19 OK created sql view model main.stg_events .............................. [OK in 0.08s]
03:42:59  2 of 19 START sql view model main.stg_ticket_changes ........................... [RUN]
03:42:59  2 of 19 OK created sql view model main.stg_ticket_changes ...................... [OK in 0.03s]
03:42:59  3 of 19 START sql incremental model main.silver_events ......................... [RUN]
03:42:59  3 of 19 OK created sql incremental model main.silver_events .................... [OK in 0.10s]
03:42:59  4 of 19 START unit_test silver_tickets::silver_tickets_latest_change_wins_and_delete_is_tombstone  [RUN]
03:42:59  4 of 19 PASS silver_tickets::silver_tickets_latest_change_wins_and_delete_is_tombstone  [PASS in 0.14s]
03:42:59  8 of 19 START sql incremental model main.silver_tickets ........................ [RUN]
03:42:59  8 of 19 OK created sql incremental model main.silver_tickets ................... [OK in 0.10s]
03:42:59  5 of 19 START test not_null_silver_events_event_id ............................. [RUN]
03:42:59  5 of 19 PASS not_null_silver_events_event_id ................................... [PASS in 0.04s]
03:42:59  6 of 19 START test not_null_silver_events_user_id .............................. [RUN]
03:42:59  6 of 19 PASS not_null_silver_events_user_id .................................... [PASS in 0.02s]
03:42:59  7 of 19 START test unique_silver_events_event_id ............................... [RUN]
03:42:59  7 of 19 PASS unique_silver_events_event_id ..................................... [PASS in 0.03s]
03:42:59  9 of 19 START test accepted_values_silver_tickets_category__bug__billing__other  [RUN]
03:42:59  9 of 19 PASS accepted_values_silver_tickets_category__bug__billing__other ...... [PASS in 0.04s]
03:42:59  10 of 19 START test accepted_values_silver_tickets_priority__low__medium__high . [RUN]
03:42:59  10 of 19 PASS accepted_values_silver_tickets_priority__low__medium__high ....... [PASS in 0.03s]
03:42:59  11 of 19 START test accepted_values_silver_tickets_status__open__pending__closed  [RUN]
03:42:59  11 of 19 PASS accepted_values_silver_tickets_status__open__pending__closed ..... [PASS in 0.03s]
03:42:59  12 of 19 START test not_null_silver_tickets__lsn ............................... [RUN]
03:42:59  12 of 19 PASS not_null_silver_tickets__lsn ..................................... [PASS in 0.02s]
03:42:59  13 of 19 START test not_null_silver_tickets_is_deleted ......................... [RUN]
03:42:59  13 of 19 PASS not_null_silver_tickets_is_deleted ............................... [PASS in 0.02s]
03:42:59  14 of 19 START test not_null_silver_tickets_ticket_id .......................... [RUN]
03:43:00  14 of 19 PASS not_null_silver_tickets_ticket_id ................................ [PASS in 0.02s]
03:43:00  15 of 19 START test unique_silver_tickets_ticket_id ............................ [RUN]
03:43:00  15 of 19 PASS unique_silver_tickets_ticket_id .................................. [PASS in 0.04s]
03:43:00  16 of 19 START sql microbatch model main.gold_feature_daily .................... [RUN]
03:43:00  Batch 1 of 7 START batch 2026-08-10 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 1 of 7 OK created batch 2026-08-10 of main.gold_feature_daily .................. [OK in 0.04s]
03:43:00  Batch 2 of 7 START batch 2026-08-11 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 2 of 7 OK created batch 2026-08-11 of main.gold_feature_daily .................. [OK in 0.06s]
03:43:00  Batch 3 of 7 START batch 2026-08-12 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 3 of 7 OK created batch 2026-08-12 of main.gold_feature_daily .................. [OK in 0.04s]
03:43:00  Batch 4 of 7 START batch 2026-08-13 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 4 of 7 OK created batch 2026-08-13 of main.gold_feature_daily .................. [OK in 0.04s]
03:43:00  Batch 5 of 7 START batch 2026-08-14 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 5 of 7 OK created batch 2026-08-14 of main.gold_feature_daily .................. [OK in 0.04s]
03:43:00  Batch 6 of 7 START batch 2026-08-15 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 6 of 7 OK created batch 2026-08-15 of main.gold_feature_daily .................. [OK in 0.04s]
03:43:00  Batch 7 of 7 START batch 2026-08-16 of main.gold_feature_daily ....................... [RUN]
03:43:00  Batch 7 of 7 OK created batch 2026-08-16 of main.gold_feature_daily .................. [OK in 0.04s]
03:43:00  16 of 19 OK created sql microbatch model main.gold_feature_daily ............... [SUCCESS in 0.33s]
03:43:00  17 of 19 START test dbt_utils_free_unique_combination_gold_feature_daily_user_id__event_date  [RUN]
03:43:00  17 of 19 PASS dbt_utils_free_unique_combination_gold_feature_daily_user_id__event_date  [PASS in 0.10s]
03:43:00  18 of 19 START test not_null_gold_feature_daily_event_date ..................... [RUN]
03:43:00  18 of 19 PASS not_null_gold_feature_daily_event_date ........................... [PASS in 0.02s]
03:43:00  19 of 19 START test not_null_gold_feature_daily_user_id ........................ [RUN]
03:43:00  19 of 19 PASS not_null_gold_feature_daily_user_id .............................. [PASS in 0.02s]
03:43:00
03:43:00  Finished running 3 incremental models, 13 data tests, 1 unit test, 2 view models in 0 hours 0 minutes and 1.50 seconds (1.50s).
03:43:00
03:43:00  Completed successfully
03:43:00
03:43:00  Done. PASS=19 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=19
```

```text
python -m scripts.parity
=== parity: lite pipeline vs dbt ===
  [OK ] silver_tickets       lite 3c15dfd43701  dbt 3c15dfd43701
  [OK ] gold_feature_daily   lite 8630e04a61d1  dbt 8630e04a61d1
RESULT: PARITY — both implementations agree
```

```text
python -m scripts.bonus_llm
=== bonus: LLM labelling of 11 live tickets ===
  cost estimate before running: ~484 tokens = $0.0010 per full run
  uncached cost estimate before running: ~484 tokens = $0.0010
  uncached cost estimate before running: ~0 tokens = $0.0000
  uncached cost estimate before running: ~484 tokens = $0.0010
  [OK ] first run labels every live ticket
  [OK ] re-run with same model + prompt makes 0 LLM calls
  [OK ] every Gold label is bug / billing / other
  [OK ] off-schema answers go to llm_label_quarantine
  [OK ] new prompt version re-labels on purpose
  [OK ] labels carry their prompt version
BONUS PASS
```

## 6. Bonus và mở rộng
- B1: cache hash(input)+model+prompt version, lưu cả response bị reject để rerun không gọi lại; JSON schema nghiêm ngặt, quarantine và estimate trước chạy. Checker FakeLLM đạt BONUS PASS; phần mở rộng OpenAI thật được ghi ở mục 7.
- B2: [Thiết kế pipeline chatbot hỗ trợ học viên](../bonus/DESIGN.md), chọn brainstorm; không chọn Airflow. Thiết kế ghi rõ giả định và những phần chưa triển khai.

## 7. Mở rộng: gán nhãn bằng API OpenAI thật

Ngày 05/10/2026, chạy `python -m scripts.label_api` trên warehouse hiện tại.
Adapter trong `pipeline/llm_api.py` dùng OpenAI SDK 2.54.0 và Responses API,
model cấu hình `gpt-4o-mini`; phản hồi xác nhận snapshot `gpt-4o-mini-2024-07-18`.
Structured Outputs giới hạn nhãn `bug/billing/other`; bước validate cục bộ quyết
định response được vào `gold_ticket_labels` hay quarantine. Ticket đã xóa không
được gửi đến API. API key đọc từ `.env`, không lưu trong bằng chứng hoặc file mẫu.

Kết quả thực tế: **11 API calls, 11 nhãn hợp lệ, 0 quarantine**; lần chạy lại
dùng cache và tạo **0 API calls**. Usage do OpenAI trả về là **1.561 input tokens,
66 output tokens**. Chi phí ước tính từ usage là **0,00027375 USD**, theo giá
input 0,15 USD/triệu token và output 0,60 USD/triệu token; đây không phải hóa đơn.
Ước tính trước chạy là 0,000783 USD, dùng độ dài UTF-8 và giới hạn output 64 token/request.

Bằng chứng máy sinh: [real-api.json](evidence/real-api.json). Bảng `llm_api_usage`
lưu response ID, model snapshot, prompt version và token usage. Cache khóa theo
hash(input)+model+prompt version; tăng version khi đổi prompt. Lỗi mạng/API được
báo lỗi và giữ cache đã hoàn thành để tiếp tục ở lần sau.

`REAL API PASS` kiểm tra nhãn hợp lệ, độ phủ ticket và cache. So với 10 ticket đã
có nhãn người gán, có **9 nhãn khớp**. T-82 được LLM gán `bug` trong khi nhãn seed
là `other`; T-94 chưa có nhãn người gán. Cần review trường hợp bất đồng trước khi
dùng nhãn LLM làm dữ liệu training. Kết quả 9/10 trên seed nhỏ không phải đánh giá
độ chính xác production.

Output lấy từ lần gọi API thật:

```text
first run: 11 API calls; 11 labels; 0 quarantined
usage: 1561 input tokens, 66 output tokens
usage-based cost estimate: $0.000274
cache re-run: 0 API calls
REAL API PASS
```

Kiểm thử: bộ test gốc 34 test cùng 17 test bổ sung cho API đạt **51 passed**;
sau khi đơn giản hóa cách ước tính token, chạy lại 17 test API và đều đạt.
Các test bổ sung trong `api_tests/` dùng mock để kiểm schema, cache, đổi version/model,
quarantine, loại ticket đã xóa, cấu hình key và giữ Gold khi lỗi mạng. Bộ test
chấm gốc, seed và công cụ checksum được giữ nguyên. Checksum Gold lõi sau API
vẫn là `39e115c510ecdf526800eac227158a4f`.
