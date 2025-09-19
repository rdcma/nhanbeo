from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import os
import requests

from .service import ShipFeeService
from .config import get_orders_json_path, get_default_conversation_id
from .counter import CounterStore


class AskRequest(BaseModel):
    user_text: str
    conversation_id: Optional[str] = None
    orders_json_path: Optional[str] = None
    orders_json: Optional[dict] = None


class ResetRequest(BaseModel):
    conversation_id: Optional[str] = None


def create_app() -> FastAPI:
    app = FastAPI(title="Ship Fee Q&A API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    service = ShipFeeService(CounterStore())

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/api/v1/ship-fee/answer")
    def answer(req: AskRequest):
        resp = service.answer(
            user_text=req.user_text,
            conversation_id=req.conversation_id or get_default_conversation_id(),
            orders_json_path=req.orders_json_path or get_orders_json_path(),
            orders_data=req.orders_json,
        )
        return {
            "case": resp.case,
            "reply_text": resp.reply_text,
            "action": resp.action,
            "actions": resp.actions,
            "diagnostic": resp.diagnostic,
        }

    @app.post("/api/v1/ship-fee/reset")
    def reset_counter(req: ResetRequest):
        conv_id = req.conversation_id or get_default_conversation_id()
        base_key = f"shipfee:{conv_id}"
        tagged_key = f"{base_key}:tagged"
        store = CounterStore()
        store.reset(base_key)
        # Clear the tagAgent flag as well
        try:
            store.set_flag(tagged_key, False)
        except Exception:
            pass
        return {"ok": True}

    # Proxy: get orders by conversation
    @app.get("/api/v1/orders/by-conversation")
    def get_orders_by_conversation(conversation_id: str):
        from .config import get_poscake_base

        base = get_poscake_base()
        url = f"{base}/api/v1/poscake/orders/near-by-conversation"
        try:
            r = requests.get(url, params={"conversation_id": conversation_id}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Static web chat at /web
    app.mount("/web", StaticFiles(directory=str(_ensure_web_dir()), html=True), name="static")

    @app.get("/")
    def root():
        return RedirectResponse(url="/web/")

    return app


def _ensure_web_dir():
    import os
    from pathlib import Path

    web_dir = Path(__file__).parent / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    index_file = web_dir / "index.html"
    if not index_file.exists():
        index_file.write_text(_DEFAULT_INDEX_HTML, encoding="utf-8")
    return web_dir


_DEFAULT_INDEX_HTML = """
<!doctype html>
<html lang=\"vi\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Ship Fee Chat</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; margin: 20px; }
    #log { border: 1px solid #ddd; padding: 12px; height: 300px; overflow: auto; }
    .msg { margin: 6px 0; }
    .me { color: #0a58ca; }
    .bot { color: #0a7d2a; }
    .info { color: #666; font-size: 12px; }
    input[type=text] { width: 80%; padding: 8px; }
    button { padding: 8px 12px; }
  </style>
  <script>
    function convId() {
      let id = localStorage.getItem('conv_id');
      if (!id) {
        id = '792129147307154_24089184430742730';
        localStorage.setItem('conv_id', id);
      }
      return id;
    }
    async function sendMsg() {
      const text = document.getElementById('text').value.trim();
      if (!text) return;
      log(`<div class=\"msg me\">Bạn: ${escapeHtml(text)}</div>`);
      document.getElementById('text').value = '';
      const res = await fetch('/api/v1/ship-fee/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_text: text, conversation_id: convId() })
      });
      const data = await res.json();
      log(`<div class=\"msg bot\">Bot: ${escapeHtml(data.reply_text)} <span class=\"info\">[case=${data.case}, asked=${data.diagnostic.asked_count}, action=${data.action||'none'}]</span></div>`);
    }
    function log(html) { const el = document.getElementById('log'); el.innerHTML += html; el.scrollTop = el.scrollHeight; }
    function escapeHtml(s){return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
    window.addEventListener('DOMContentLoaded', () => {
      document.getElementById('text').addEventListener('keydown', (e)=>{ if(e.key==='Enter'){ sendMsg(); }});
    });
  </script>
</head>
<body>
  <h2>Ship Fee Q&A</h2>
  <div id=\"log\"></div>
  <div style=\"margin-top:10px;\">
    <input id=\"text\" type=\"text\" placeholder=\"Nhập tin nhắn...\" />
    <button onclick=\"sendMsg()\">Gửi</button>
  </div>
</body>
</html>
"""


app = create_app()


