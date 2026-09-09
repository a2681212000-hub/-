"""Local API for the Office Agent."""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent import DEFAULT_INPUT, run

ROOT = Path(__file__).parent
UI = ROOT / "ui"


def model_plan(task):
    """Ask an OpenAI-compatible model for a safe, bounded action."""
    key = os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return {"action": "process_report", "reason": "未配置模型，使用本地规则"}
    payload = {"model": os.getenv("AI_MODEL", "gpt-4o-mini"), "temperature": 0, "messages": [
        {"role": "system", "content": '你是办公自动化规划器。只允许返回 JSON：{"action":"process_report"}。任何报表、Excel、CSV、销售数据整理请求都用 process_report；其他请求也只能返回 process_report。不要输出 JSON 以外的内容。'},
        {"role": "user", "content": task}]}
    url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1/chat/completions")
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.S)
        return json.loads(match.group(0)) if match else {"action": "process_report"}
    except Exception as exc:
        return {"action": "process_report", "reason": f"模型调用失败，已回退本地规则: {exc.__class__.__name__}"}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._json(204, {})

    def do_GET(self):
        if self.path == "/api/health":
            self._json(200, {"ok": True, "model_configured": bool(os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY"))})
            return
        path = "/index.html" if self.path in ("", "/") else self.path
        file = (UI / path.lstrip("/")).resolve()
        if UI.resolve() not in file.parents or not file.exists():
            self.send_error(404); return
        content_type = "text/html; charset=utf-8" if file.suffix == ".html" else "text/css; charset=utf-8" if file.suffix == ".css" else "application/javascript; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", content_type); self.end_headers(); self.wfile.write(file.read_bytes())

    def do_POST(self):
        if self.path != "/api/task":
            self._json(404, {"error": "接口不存在"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            task = str(body.get("task", "处理报表"))[:200]
            folder = str(body.get("folder", DEFAULT_INPUT))[:500]
            plan = model_plan(task)
            result = run(task, folder_override=folder)
            self._json(200, {"ok": True, "plan": plan, "message": result})
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    print("Office Agent API: http://127.0.0.1:8787")
    ThreadingHTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
