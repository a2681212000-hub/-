"""Local API for the Office Agent."""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent import ALLOWED_TOOLS, DEFAULT_INPUT, plan_task
from workflow import TaskConflict, TaskStore

ROOT = Path(__file__).parent
UI = ROOT / "ui"
STORE = TaskStore(ROOT / "runtime" / "tasks")


def model_plan(task, settings=None):
    """Ask an OpenAI-compatible model for a safe, bounded action."""
    settings = settings or {}
    key = settings.get("api_key") or os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        plan = plan_task(task)
        plan["reason"] = "未配置模型，使用本地规划器"
        return plan
    payload = {"model": settings.get("model") or os.getenv("AI_MODEL", "gpt-4o-mini"), "temperature": 0, "messages": [
        {"role": "system", "content": '你是办公自动化规划器。只能返回 JSON，格式为 {"steps":[{"tool":"工具名","reason":"简短原因"}]}。工具名只能从 list_files、process_report、list_reports、extract_pdf、collect_web、list_mail_attachments、archive_files、notify 中选择。涉及发送通知时只选择 notify，它只能生成草稿。'},
        {"role": "user", "content": task}]}
    url = settings.get("base_url") or os.getenv("AI_BASE_URL", "https://api.openai.com/v1/chat/completions")
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```") and content.endswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        plan = json.loads(content)
        return validate_plan(plan, task)
    except Exception as exc:
        plan = plan_task(task)
        plan["reason"] = f"模型调用失败，已回退本地规划器: {exc.__class__.__name__}"
        return plan


def validate_plan(plan, task):
    """Keep model output inside the explicit tool allow-list."""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not 1 <= len(steps) <= 5:
        raise ValueError("模型执行计划格式无效")
    safe_steps = []
    for item in steps:
        tool = item.get("tool") if isinstance(item, dict) else None
        if tool not in ALLOWED_TOOLS:
            raise ValueError("模型执行计划包含未知工具")
        safe_steps.append({"tool": tool, "reason": str(item.get("reason", ""))[:80]})
    return {"steps": safe_steps}


def open_local_path(raw_path, mode="folder"):
    """Open a local folder through the Windows shell."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("路径不能为空")
    if not isinstance(mode, str) or mode not in {"folder", "reveal"}:
        raise ValueError("打开方式无效")
    path = Path(raw_path).expanduser().resolve()
    if mode == "folder":
        if not path.is_dir():
            raise ValueError(f"文件夹不存在：{path}")
        target = path
    else:
        if not path.is_file():
            raise ValueError(f"文件不存在：{path}")
        target = path.parent
    try:
        os.startfile(str(target))
    except AttributeError as exc:
        raise OSError("当前系统不支持打开本地文件夹") from exc
    return {"path": str(path), "folder": str(target), "mode": mode}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._json(403, {"ok": False, "error": "只接受本地同源网页请求"})

    def _local_request(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in hosts and (origin is None or origin == f"http://{host}")

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请求格式必须是 application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 65536:
            raise ValueError("请求体为空或过大")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return body

    def do_GET(self):
        if not self._local_request():
            self._json(403, {"ok": False, "error": "不允许跨来源访问"}); return
        if self.path == "/api/health":
            self._json(200, {"ok": True, "model_configured": bool(os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY")), "pending_tasks": sum(1 for job in STORE.recent() if job["status"] == "awaiting_confirmation")})
            return
        if self.path == "/api/tasks":
            self._json(200, {"ok": True, "tasks": STORE.recent()})
            return
        match = re.fullmatch(r"/api/task/([a-f0-9]+)/?", self.path)
        if match:
            try:
                self._json(200, {"ok": True, "job": STORE.get(match.group(1))})
            except KeyError as exc:
                self._json(404, {"ok": False, "error": str(exc)})
            return
        path = "/index.html" if self.path in ("", "/") else self.path
        file = (UI / path.lstrip("/")).resolve()
        if UI.resolve() not in file.parents or not file.is_file():
            self.send_error(404); return
        content_type = "text/html; charset=utf-8" if file.suffix == ".html" else "text/css; charset=utf-8" if file.suffix == ".css" else "application/javascript; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", content_type); self.end_headers(); self.wfile.write(file.read_bytes())

    def do_POST(self):
        if not self._local_request():
            self._json(403, {"ok": False, "error": "不允许跨来源操作"}); return
        if self.path == "/api/open-path":
            try:
                body = self._body()
                opened = open_local_path(body.get("path"), body.get("mode", "folder"))
                self._json(200, {"ok": True, "opened": opened})
            except (OSError, ValueError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        decision_match = re.fullmatch(r"/api/task/([a-f0-9]+)/decision", self.path)
        if decision_match:
            try:
                body = self._body()
                job = STORE.decide(decision_match.group(1), str(body.get("confirmation_id", "")), str(body.get("decision", "")))
                self._json(200, {"ok": True, "job": job})
            except TaskConflict as exc:
                self._json(409, {"ok": False, "error": str(exc)})
            except (KeyError, ValueError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception:
                self._json(500, {"ok": False, "error": "任务记录写入失败，请查询任务状态"})
            return
        if self.path != "/api/task":
            self._json(404, {"error": "接口不存在"}); return
        try:
            body = self._body()
            task = body.get("task", "处理报表")
            folder = body.get("folder") or str(DEFAULT_INPUT)
            if not isinstance(task, str) or not task.strip() or len(task) > 2000:
                raise ValueError("任务描述需为 1 至 2000 个字符")
            if not isinstance(folder, str) or len(folder) > 1000:
                raise ValueError("输入目录无效")
            if body.get("ai") is not None and not isinstance(body["ai"], dict):
                raise ValueError("模型配置必须是 JSON 对象")
            plan = model_plan(task, body.get("ai"))
            job = STORE.create(task, folder, plan)
            self._json(202 if job["status"] == "awaiting_confirmation" else 200, {"ok": True, "job": job, "plan": job["plan"], "results": job["results"], "message": job["message"]})
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    print("Office Agent API: http://127.0.0.1:8787")
    ThreadingHTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
