# Ship Fee Q&A Feature

Standalone FastAPI service to answer shipping-fee questions, with Redis-based ask counter, LLM-powered intent detection (hybrid), conversation-based order fetch, and a minimal web UI for testing.

---

## Quick start

1) Activate venv and install deps
```bash
cd /Users/baonq/workspace/nhanbeo
source .venv/bin/activate
pip install -r requirements.txt
```

2) Configure env (.env)
- GOOGLE_API_KEY=...
- GOOGLE_MODEL=gemini-1.5-flash  # or your default
- REDIS_URL=redis://localhost:6379/0
- ORDERS_JSON=examples/orders_priority.json
- CONVERSATION_ID_DEFAULT=792129147307154_24089184430742730
- POSCAKE_BASE=http://160.250.216.28:13886

3) Run service
```bash
uvicorn ship_fee.api:app --reload --port 8000
# or
python run_ship_fee.py
```

4) Open web UI
- http://localhost:8000/web
- Enter conversation id, click “Lấy đơn theo Conversation ID” to load orders JSON.
- You may edit the JSON in the textarea before sending a message.
- Click “Gửi” to ask.
- “Reset đếm” clears the Redis counter and also clears chat history.

---

## API

Base URL: `/`

Health
- GET `/healthz` → `{ "ok": true }`

Fetch orders by conversation (proxy to POSCAKE)
- GET `/api/v1/orders/by-conversation?conversation_id=...`
- Uses `POSCAKE_BASE/api/v1/poscake/orders/near-by-conversation` under the hood.

Answer ship-fee question
- POST `/api/v1/ship-fee/answer`
```json
{
  "user_text": "...",
  "conversation_id": "792129147307154_24089184430742730",
  "orders_json": { "success": true, "orders": [ { "order_info": {"status": 0, "shipping_fee": 30000}, "items": [{"name": "..."}] } ] }
}
```
- If `orders_json` is provided, it is used. Otherwise, server loads from `ORDERS_JSON`.
- Response
```json
{
  "case": "no_order|freeship|has_ship_first_time|ask_free_ship|ask_free_ship_many_times|smalltalk",
  "reply_text": "...",
  "action": "freeship|null",
  "actions": { "apply_free_shipping": true/false },
  "diagnostic": {
    "asked_count": 2,
    "shipping_fee": 30000,
    "order_id": 309,
    "status": 0,
    "picked_reason": "..."
  }
}
```

Reset counter
- POST `/api/v1/ship-fee/reset`
```json
{ "conversation_id": "792129147307154_24089184430742730" }
```
- Clears Redis key `shipfee:{conversation_id}`. Web UI also clears chat on success.

---

## Business logic

Order source and selection
- Orders come from `orders_json` (textarea) or fallback file (`ORDERS_JSON`).
- Latest active order = first entry with `status ∈ {0,1}` and non-empty `items`.
- Shipping fee = `order_info.shipping_fee`.
- Freeship if `shipping_fee == 0`.

Intent detection (hybrid)
- Regex heuristics detect: fee questions, requests for freeship, cancel threats, smalltalk.
- LLM is only called when rule_score < 0.8 or to generate smalltalk replies.
- Smalltalk intent responds immediately with a short LLM reply and does not increment the counter.

Counter policy (15 minutes per conversation)
- Redis key: `shipfee:{conversation_id}` with TTL 900s.
- Only requests related to “ask freeship” or cancel threats increment the counter.
- Explicit fee-amount questions do NOT increment; the bot replies with numeric fee ("Dạ phí ship đơn hiện tại của mình là {fee}đ ạ.").

Cases and replies
- no_order: no active order found → polite check message.
- freeship: fee == 0 → inform freeship.
- has_ship_first_time: first time asking about freeship → informative, no change.
- ask_free_ship: second time or explicit request for free → polite refusal template.
- ask_free_ship_many_times: third time OR cancel threat → escalate with `action: "freeship"` and `actions.apply_free_shipping=true`.
- smalltalk: greeting/ack/thanks → short friendly reply, no counting.

Escalation templates (loyal vs new)
- Loyal customer: has any historical successful order (status 3).
  - `TEMPLATE_ESCALATE_FREESHIP_LOYAL`
- New customer: no past successful order.
  - `TEMPLATE_ESCALATE_FREESHIP_NEW`

---

## Web UI behavior
- Conversation ID input persists in `localStorage`.
- “Lấy đơn theo Conversation ID” fetches orders via backend proxy and fills the textarea.
- The textarea content is sent as `orders_json` on “Gửi”.
- “Reset đếm” removes the counter key and clears the chat log.

---

## Examples

Curl: ask with inline orders JSON
```bash
curl -X POST http://localhost:8000/api/v1/ship-fee/answer \
  -H 'Content-Type: application/json' \
  -d '{
    "user_text": "phí ship là bao nhiêu?",
    "conversation_id": "792129147307154_24089184430742730",
    "orders_json": {"success":true,"orders":[{"order_info":{"status":0,"shipping_fee»:30000},"items":[{"name":"abc"}]}]}
  }'
```

Curl: reset counter
```bash
curl -X POST http://localhost:8000/api/v1/ship-fee/reset \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id":"792129147307154_24089184430742730"}'
```

---

## Troubleshooting
- 404 on `/web`: ensure server is running and you’re visiting `/web` (root `/` redirects there).
- CORS: backend enables permissive CORS for the web UI.
- Redis not available: the app falls back to in-memory counter (dev only).
- POSCAKE errors: verify `POSCAKE_BASE` and that the conversation_id is correct.

---

## Code map
- `ship_fee/api.py`: FastAPI app, routes, static web.
- `ship_fee/service.py`: core logic and case selection.
- `ship_fee/intent.py`: hybrid intent classifier, smalltalk reply.
- `ship_fee/orders.py`: parse orders JSON, pick latest, detect loyal customer.
- `ship_fee/counter.py`: Redis and in-memory counter with TTL.
- `ship_fee/templates.py`: reply templates and fee formatter.
- `ship_fee/web/index.html`: minimal test UI.
- `run_ship_fee.py`: dev runner.
