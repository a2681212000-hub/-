"""Local API for the Office Agent."""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent import ALLOWED_TOOLS, DEFAULT_INPUT, execute_tool, plan_task

ROOT = Path(__file__).parent
UI = ROOT / "ui"


def model_plan(task, settings=None):
    """Ask an OpenAI-compatible model for a safe, bounded action."""
    settings = settings or {}
    key = settings.get("api_key") or os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        plan = plan_task(task)
        plan["reason"] = "未配置模型，使用本地规划器"
        return plan
    payload = {"model": settings.get("model") or os.getenv("AI_MODEL", "gpt-4o-mini"), "temperature": 0, "messages": [
        {"role": "system", "content": '你是办公自动化规划器。只能返回 JSON，格式为 {"steps":[{"tool":"list_files|process_report|list_reports","reason":"简短原因"}]}。只能使用这三个工具。报表处理依次选择 list_files、process_report；查看已有报告选择 list_reports。'},
        {"role": "user", "content": task}]}
    url = settings.get("base_url") or os.getenv("AI_BASE_URL", "https://api.openai.com/v1/chat/completions")
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.S)
        plan = json.loads(match.group(0)) if match else plan_task(task)
        return validate_plan(plan, task)
    except Exception as exc:
        plan = plan_task(task)
        plan["reason"] = f"模型调用失败，已回退本地规划器: {exc.__class__.__name__}"
        return plan


def validate_plan(plan, task):
    """Keep model output inside the explicit tool allow-list."""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps:
        return plan_task(task)
    safe_steps = []
    for item in steps[:5]:
        tool = item.get("tool") if isinstance(item, dict) else None
        if tool in ALLOWED_TOOLS:
            safe_steps.append({"tool": tool, "reason": str(item.get("reason", ""))[:80]})
    return {"steps": safe_steps or plan_task(task)["steps"]}


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
            plan = model_plan(task, body.get("ai"))
            results = [execute_tool(step["tool"], task, folder) for step in plan["steps"]]
            report_result = next((result for result in reversed(results) if result["tool"] == "process_report"), None)
            message = report_result["message"] if report_result else "任务完成"
            self._json(200, {"ok": True, "plan": plan, "results": results, "message": message})
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    print("Office Agent API: http://127.0.0.1:8787")
    ThreadingHTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
