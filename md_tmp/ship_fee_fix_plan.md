## Kế hoạch fix ship_fee (intent, templates, routing, tagAgent)

Ngày: 2025-09-18

### Mục tiêu
- Giảm ngưỡng lặp yêu cầu freeship xuống 2 lần: chuyển action sang `tagAgent` thay vì tiếp tục trả lời freeship.
- Sửa nhầm intent: câu như “bạn miễn phí ship đi” phải vào `ask_freeship` (service nhận `intent=ship_fee`, `wants_free=True`).
- Câu có xu hướng rút lui/không mua vì không freeship (ví dụ: “Thế thôi chào shop nhé.”) không rơi vào smalltalk, mà phản hồi theo hướng giữ khách với ưu đãi phí ship.
- Trả lời câu hỏi phí ship phong phú hơn, nhưng vẫn có biến thể nêu rõ phí đơn hiện tại theo mẫu: “Dạ phí ship đơn hiện tại của mình là {fee} ạ”, kèm thêm nhiều lựa chọn mềm mại.
- Xử lý than phiền phí ship (chưa đòi freeship): ưu tiên nói câu khách yêu cầu rồi `tagAgent`.

### Phạm vi thay đổi (tổng quan)
- `ship_fee/intent.py`: mở rộng regex, ưu tiên `wants_free`/`cancel_threat` > smalltalk, nhận diện complaint.
- `ship_fee/templates.py`: thêm nhóm templates, nhiều biến thể (random) với `{fee}`; ưu tiên câu complaint đặc thù.
- `ship_fee/service.py`: routing theo ngữ cảnh; complaint → nói câu ưu tiên rồi `tagAgent`; repeat freeship ≥ 2 → `tagAgent`.
- `ship_fee/counter.py` + `ship_fee/config.py`: cấu hình ngưỡng lặp → 2.
- Tests: thêm case intent/routing/templates.

### Intent & Rule updates (ship_fee/intent.py)
- Mở rộng/gia cố nhận diện freeship (wants_free):
  - Regex bao phủ: `miễn\s*ship|free\s*ship|freeship|miễn\s*phí\s*vận\s*chuyển|bớt\s*ship|giảm\s*ship|miễn\s*phí\s*ship|bạn\s*miễn\s*phí\s*ship\s*đi`.
  - Nếu bắt được các cụm trên → `intent_guess = "ask_freeship"` ngay cả khi có cụm “trong đơn”.
- Nhận diện cancel/rút lui do phí ship (cancel_threat): bổ sung mẫu:
  - `thôi.*(chào|bye).*shop`, `thế thôi.*(chào|bye)`, `khỏi.*mua`, `không.*mua.*nữa`, `để lần khác`, `thôi.*shop`.
- Nhận diện complaint phí ship (không yêu cầu freeship trực tiếp):
  - Ví dụ: `ôi.*phí.*cao`, `đắt.*phí`, `ship.*cao.*`, `mua.*\d+k.*mà ship.*\d+k`, `cao hơn.*đồ`.
  - Set cờ `about_fee_amount=True` và `is_complaint=True` để routing mềm mại.
- Ưu tiên logic:
  - Nếu `wants_free=True` hoặc `cancel_threat=True` → không hạ xuống `smalltalk`.
  - Nếu `cancel_threat=True` và có liên quan ship → map về `intent_for_service = "ship_fee"` để route đúng.
- LLM prompt: nhấn mạnh phân biệt `ask_freeship` vs `fee_question` với ví dụ “bạn miễn phí ship đi”. Parse `signals` gồm `wants_free`, `about_fee_amount`, `cancel_threat`, và (mới) `is_complaint` nếu có.

### Routing (ship_fee/service.py)
- `ask_freeship` → phản hồi nhóm `ask_freeship_reply`. Nếu khách lặp lại yêu cầu và `repeat_count >= 2` (theo counter) → action `tagAgent`.
- `fee_question`:
  - Nếu `is_complaint=True` (khách than phiền phí ship): trả lời câu ưu tiên (bên dưới) rồi `tagAgent`.
  - Nếu chỉ hỏi mức phí (`about_fee_amount=True`, không complaint): dùng nhóm `fee_question_general` (đa dạng, có `{fee}`).
- `cancel_threat=True` (xu hướng rời đi do phí ship): dùng nhóm `cancel_threat_save` (ưu đãi phí ship nhẹ nhàng), tránh smalltalk; nếu lặp lại rời đi → `tagAgent`.

### Ngưỡng lặp và cấu hình (ship_fee/counter.py, ship_fee/config.py)
- Thêm cấu hình `REPEAT_FREESHIP_TO_AGENT_THRESHOLD = 2`.
- Dùng ngưỡng này tại service khi quyết định đổi action sang `tagAgent`.

### Templates (ship_fee/templates.py)
- Nhóm templates và quy tắc chọn:
  - `fee_question_general` (trả lời mức phí):
    - Mục tiêu: vẫn có biến thể chuẩn “Dạ phí ship đơn hiện tại của mình là {fee} ạ”, nhưng thêm lựa chọn mềm mại, gợi mở hỗ trợ.
    - Ví dụ biến thể (random):
      1) "Dạ phí ship đơn hiện tại của mình là {fee} ạ."
      2) "Dạ em kiểm tra đơn này phí ship là {fee} ạ."
      3) "Em báo mình phí ship hiện tại: {fee} ạ."
      4) "Dạ hiện phí vận chuyển cho đơn của mình là {fee} ạ."
      5) "Em vừa tra phí ship là {fee} ạ, mình yên tâm giúp em nha."
      6) "Dạ phí ship đơn hiện tại là {fee} ạ, nếu cần em hỗ trợ tối ưu ạ."
      7) "Em xác nhận phí ship đơn này là {fee} ạ, chị/anh cần em kiểm tra thêm tuyến giao không ạ?"
      8) "Dạ phí vận chuyển hiện tại là {fee} ạ, em sẵn sàng hỗ trợ thêm cho mình." 
  - `fee_question_complaint` (than phiền phí ship):
    - Ưu tiên nói câu sau (luôn chọn đầu tiên):
      - "Dạ mong c thông cảm giúp e nha, phí ship của nhãn hàng khá cao, bên e đã hỗ trợ mình 1 phần ship và ưu đãi về giá sản phẩm thấp nhất có thể cho mình rùi ạ. Nhờ mình hỗ trợ phần ship này giúp e nha, e cảm ơn c nhiều."
    - Sau khi gửi, thực hiện `tagAgent` để nhân viên tiếp tục hỗ trợ.
    - Có thể bổ sung vài biến thể dự phòng (không ưu tiên, chỉ dùng nếu cần giảm lặp):
      - "Em rất hiểu cảm giác của mình ạ. Phí ship do hãng áp chung, bên em đã cố gắng hỗ trợ tối đa phần giá rồi ạ. Nhờ mình thông cảm giúp em ạ."
      - "Dạ em thông cảm với mình ạ. Bên em luôn chọn phương án ship tối ưu, mong mình hỗ trợ phần ship giúp em nha."
  - `ask_freeship_reply` (khách xin freeship):
    - Ví dụ: "Dạ em hỗ trợ mình ưu đãi phí ship cho đơn này ạ. Mình cho em địa chỉ để em áp mức tốt nhất nha?"
    - Ví dụ: "Em note hỗ trợ phí ship cho mình ạ, mình xác nhận mẫu và địa chỉ giúp em nhé?"
  - `cancel_threat_save` (có xu hướng rời đi vì ship):
    - Ví dụ: "Dạ em xin lỗi vì bất tiện ạ. Em hỗ trợ ưu đãi phí ship để mình yên tâm nha, mình cho em địa chỉ để em áp mức tốt nhất ạ?"
    - Ví dụ: "Đừng vội ạ, em hỗ trợ giảm phí ship cho đơn này để mình trải nghiệm trước nha?"
  - `smalltalk_reply` (nếu thật sự là smalltalk): câu ngắn, lịch sự (tối đa 1-2 câu).

- Tone chung:
  - Thêm tiền tố "Dạ", giữ kính ngữ, đồng cảm: "em hiểu ạ", "mình yên tâm ạ".
  - Ngắn gọn 1-2 câu; tránh mệnh lệnh cứng.

### Tests đề xuất
- Intent:
  - "bạn miễn phí ship đi" → `intent=ship_fee`, `wants_free=True`, không `smalltalk`.
  - "Thế thôi chào shop nhé." → `cancel_threat=True`, route sang `cancel_threat_save` (không smalltalk).
  - "Mua có 150k mà ship 35k á" → `fee_question_complaint`.
  - "Phí ship bao nhiêu vậy" → `fee_question_general`.
- Routing:
  - Lặp xin freeship 2 lần → action `tagAgent`.
  - Complaint → gửi câu ưu tiên rồi `tagAgent`.
- Templates:
  - `fee_question_general` random đa dạng; có biến thể nêu rõ `{fee}`.

### Logging & rollout
- Log thêm: `intent_final`, `rule_score`, `is_complaint`, `repeat_count`, `route_selected`, `action`.
- Theo dõi tỉ lệ rơi vào smalltalk ở các câu rút lui sau triển khai (kỳ vọng giảm mạnh).

### Ví dụ hành vi mong muốn
- Input: "Bạn miễn phí ship đi"
  - Detect: `wants_free=True` → route `ask_freeship_reply`.
- Input: "Thế thôi chào shop nhé."
  - Detect: `cancel_threat=True` → route `cancel_threat_save`.
- Input: "Mua có 150k mà ship 35k á"
  - Detect: `is_complaint=True` → trả câu ưu tiên rồi `tagAgent`.
- Input: "Phí ship đơn này bao nhiêu vậy"
  - Detect: `about_fee_amount=True` → trả `fee_question_general` (ví dụ: "Dạ phí ship đơn hiện tại của mình là {fee} ạ").
