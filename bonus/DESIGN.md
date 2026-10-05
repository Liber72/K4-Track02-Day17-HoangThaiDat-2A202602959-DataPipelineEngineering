# Thiết kế pipeline dữ liệu cho chatbot hỗ trợ học viên Việt Nam

## Bài toán và ràng buộc

Thiết kế đề xuất mở rộng lab CSKH thành chatbot hỗ trợ học viên tra cứu lịch học, chính sách học phí, hướng dẫn nộp bài và lỗi môi trường lập trình. Đây là tình huống giả định để phân tích kiến trúc, không phải hệ thống đã triển khai. Nguồn gồm tài liệu Markdown/PDF do coach quản lý, ticket hỗ trợ và feedback sau câu trả lời. Dữ liệu có tiếng Việt, tài liệu sửa nhiều phiên bản, lịch học thay đổi và thông tin cá nhân trong ticket. Giả định quy mô ban đầu là 10.000 tài liệu, 50.000 lượt hỏi mỗi tháng, độ tươi tài liệu cần trong một giờ; chưa có yêu cầu cập nhật từng giây. Mục tiêu là trả lời có nguồn, không lộ dữ liệu học viên khác, và tạo bộ đánh giá từ lỗi thật.

Các số liệu trên là giả định thiết kế cần xác nhận với người vận hành. Prototype hiện có là pipeline lab, chưa kết nối LMS hay xử lý PDF thật. Không đưa dữ liệu học viên thật vào repo public.

## 1. Nguồn, phiên bản và độ tươi

Chọn batch mỗi giờ cho tài liệu và ticket; giữ nguyên payload, thời điểm cập nhật nguồn, thời điểm tiếp nhận và phiên bản schema ở Bronze. Với tài liệu cần thêm document_id, content_hash, khóa học và phạm vi truy cập. Nguồn có CDC thì dùng sequence/LSN để chọn trạng thái mới nhất; nguồn không có CDC dùng version do hệ thống xuất cung cấp. Không coi thời gian nhận file là bằng chứng tài liệu mới hơn.

Batch giảm chi phí vận hành và dễ backfill hơn streaming, nhưng thay đổi chính sách có thể xuất hiện chậm một giờ. Cho phép thao tác refresh có kiểm soát khi coach sửa thông tin khẩn. Loại phương án Kafka streaming ngay từ đầu vì chưa có yêu cầu độ trễ đủ chặt để bù chi phí quản lý broker, consumer và replay. Khi lượng dữ liệu tăng, đo thời gian batch trước khi thay kiến trúc.

## 2. Hợp đồng chất lượng và PII

Silver kiểm schema, encoding UTF-8, khóa tài liệu, timestamp, phạm vi lớp và tỷ lệ văn bản trích xuất được. PDF scan có quá ít text chuyển sang hàng đợi OCR; tài liệu không đọc được vào quarantine với nguyên nhân và phiên bản extractor. Không âm thầm bỏ dòng lỗi. Theo dõi tỷ lệ quarantine theo nguồn và báo người phụ trách khi vượt ngưỡng đã thống nhất; ngưỡng ban đầu cần điều chỉnh sau khi quan sát dữ liệu.

Email và số điện thoại được che bằng regex; tên, địa chỉ và mã học viên cần thêm bộ phát hiện thực thể tiếng Việt và quy tắc riêng cho định danh. Đặt chốt trước Silver và kiểm lại trước Gold phục vụ model. Đánh đổi là có thể che nhầm tên thư viện hoặc tổ chức, làm giảm chất lượng retrieval. Đo precision/recall trên tập gán nhãn có kiểm soát, ưu tiên recall cho dữ liệu nhạy cảm và review các trường hợp không chắc chắn. Bronze chứa bản gốc nên phải giới hạn truy cập và có thời hạn lưu; che PII ở Silver không giải quyết toàn bộ rủi ro.

## 3. Retrieval, phiên bản và phân quyền

Chọn retrieval kết hợp từ khóa và vector cho câu hỏi tra cứu chính sách hoặc hướng dẫn. Từ khóa giữ được tên lệnh, mã bài lab và mã lỗi; vector hỗ trợ cách diễn đạt khác nhau bằng tiếng Việt. Mỗi chunk mang document_id, phiên bản, ngày hiệu lực và quyền truy cập. Lọc theo quyền ngay trong truy vấn retrieval, sau đó kiểm lại trước khi dựng prompt. Câu trả lời cần dẫn nguồn và từ chối kết luận khi nguồn mâu thuẫn hoặc không đủ bằng chứng.

Chưa chọn knowledge graph vì phần lớn câu hỏi là tra cứu trực tiếp, chưa chứng minh lợi ích của entity resolution và traversal nhiều bước. Graph có thể phù hợp sau này cho quan hệ môn tiên quyết và lộ trình học. Cache embedding dùng hash văn bản cộng model version; đổi model tạo namespace mới, không trộn vector khác phiên bản. Không dùng vector hash của lab như embedding production.

## 4. Replay, late data và xóa dữ liệu

Ticket hiện tại dùng keyed MERGE với điều kiện version mới hơn; feedback là fact bất biến, dedup bằng event_id. Feature theo event time được overwrite partition trong cửa sổ lookback chọn từ P99 lateness đo ở Bronze. Bắt đầu đo thay vì mặc định mọi feedback đến ngay. Event vượt cửa sổ phải được ghi nhận và đưa vào backfill có kiểm soát, vì P99 không bảo đảm bao phủ mọi trường hợp.

Xóa ticket tạo tombstone giữ khóa/version để replay batch cũ không hồi sinh dữ liệu. Xóa phải lan sang chunks, index, cache có nội dung nhạy cảm và datasets phụ thuộc. Snapshot train bất biến giúp tái lập nhưng không được dùng làm lý do giữ dữ liệu khi có yêu cầu xóa hợp lệ. Thiết kế registry lineage theo ticket_id, thu hồi snapshot bị ảnh hưởng, tạo bản đã làm sạch với version mới và ghi audit. Backup cũng phải tuân thủ thời hạn lưu và quy trình xóa khi khôi phục.

## 5. Flywheel và chống leakage

Trace lưu câu hỏi đã che PII, nguồn được truy xuất, model/prompt version, thời gian phản hồi và feedback. Feedback không tự động trở thành nhãn đúng: người review xác nhận lỗi trước khi đưa vào golden eval. Chia train/eval theo thời gian và nhóm câu hỏi tương tự; loại prompt trùng hoặc gần trùng eval khỏi dữ liệu DPO. Feature dùng ASOF JOIN để tránh dùng trạng thái ticket trong tương lai.

Đánh đổi là review tốn công và làm dữ liệu học chậm hơn, nhưng giảm nguy cơ củng cố câu trả lời sai. Đánh giá retrieval recall, độ đúng của trích dẫn, tỷ lệ rò PII và chất lượng câu trả lời trên eval cố định. Chỉ phát hành model/prompt mới khi vượt tiêu chí đã thống nhất; feedback online bổ sung chứ không thay thế eval.

## 6. Scale và chi phí

Ở quy mô nhỏ, DuckDB/dbt đủ cho batch trên một máy và dễ kiểm chứng checksum. Khi tăng 10 lần, đo dung lượng Parquet, thời gian OCR, thời gian embedding và số chunk trước. Gom file nhỏ theo nguồn/ngày, chỉ OCR tài liệu thay đổi và chỉ embed hash chưa có. Chi phí lớn dự kiến nằm ở OCR/embedding và suy luận chatbot, nhưng đây là giả thuyết cần kiểm bằng telemetry.

Khi vượt bộ nhớ hoặc SLA batch, chuyển xử lý từng stage sang worker có hàng đợi và storage phù hợp; giữ contract và khóa idempotency để đối chiếu trước/sau. Không chuyển toàn bộ sang Spark chỉ vì số tài liệu tăng. Ước tính token và chi phí trước mỗi đợt backfill, giới hạn số job đồng thời và dừng phát hành dữ liệu Gold khi chất lượng chưa đạt.

## Sơ đồ kiến trúc

```text
LMS docs / tickets / feedback
              |
     Hourly export + source version
              |
       Bronze immutable payload
              |
      Schema / OCR / PII gates ----> Quarantine + review
              |
       Silver keyed data + history
              |
      +-------+-------------------+
      |                           |
Gold chunks + ACL         Point-in-time features
      |                           |
Hash/model embedding cache   Versioned datasets
      |                           |
Hybrid retrieval -> chatbot -> redacted traces
                                  |
                         Review + decontamination
                                  |
                             Eval / DPO

Deletion registry -> revoke affected indexes/caches/datasets
```

Thiết kế này cần được xác nhận bằng một pilot với dữ liệu giả lập hoặc được phép sử dụng. Lab hiện chứng minh keyed writes, late data, delete propagation và rerun; các phần OCR, phân quyền, hybrid retrieval và vận hành thực tế là công việc triển khai tiếp theo.
