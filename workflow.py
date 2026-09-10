"""Persisted task execution and single-use approval for file moves."""
import hashlib
import json
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent import ALLOWED_TOOLS, ARCHIVE_ROOT, execute_tool, find_files


# Every registered tool must explicitly declare whether it needs approval.
TOOL_POLICY = {name: "automatic" for name in (
    "list_files", "process_report", "list_reports", "extract_pdf",
    "collect_web", "list_mail_attachments", "notify",
)}
TOOL_POLICY["archive_files"] = "approval"


class TaskConflict(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(path):
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError(f"文件路径已变化或不可读取：{path}")
    before = path.stat()
    sha256 = digest(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"文件正在变化，请重新提交：{path.name}")
    return {"size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": sha256}


def archive_preview(folder, archive_root, task_id, step_index, ttl):
    folder = Path(folder).resolve()
    files = find_files(folder)
    if not folder.is_dir() or not files:
        raise ValueError(f"没有可归档的 CSV/TSV/XLSX 文件：{folder}")
    target = (archive_root / datetime.now().strftime("%Y-%m-%d") /
              f"{task_id}_{step_index + 1}").resolve()
    entries = []
    for path in files:
        destination = target / path.name
        if destination.exists():
            raise ValueError(f"目标文件已存在：{destination}")
        entries.append({"source": str(path), "destination": str(destination), **fingerprint(path)})
    return {"id": uuid.uuid4().hex, "tool": "archive_files", "folder": str(folder),
            "target": str(target), "count": len(entries), "files": entries,
            "expires_at": time.time() + ttl}


def archive_approved(preview):
    """Use the reviewed manifest, never rescan the folder after approval."""
    moved = []
    created = []
    try:
        target = Path(preview["target"])
        if target.resolve() != target:
            raise ValueError("归档目录已变化，请重新提交")
        for entry in preview["files"]:
            source = Path(entry["source"])
            if fingerprint(source) != {k: entry[k] for k in ("size", "mtime_ns", "sha256")}:
                raise ValueError(f"文件已变化，请重新提交：{source.name}")
            destination = Path(entry["destination"])
            if destination.parent != target or destination.exists():
                raise ValueError(f"归档目标已变化：{destination}")

        target.mkdir(parents=True, exist_ok=True)
        for entry in preview["files"]:
            source, destination = Path(entry["source"]), Path(entry["destination"])
            # Exclusive creation prevents overwrites, including across volumes.
            with source.open("rb") as incoming, destination.open("xb") as outgoing:
                created.append(str(destination))
                shutil.copyfileobj(incoming, outgoing)
            expected = {k: entry[k] for k in ("size", "mtime_ns", "sha256")}
            if digest(destination) != entry["sha256"] or fingerprint(source) != expected:
                raise ValueError(f"复制期间文件变化，已保留原文件：{source.name}")
            shutil.copystat(source, destination)
            source.unlink()
            moved.append({"source": str(source), "destination": str(destination)})
    except Exception as exc:
        return {"tool": "archive_files", "error": str(exc), "archived": moved,
                "created": created, "count": len(moved), "target": preview["target"]}
    return {"tool": "archive_files", "archived": moved, "count": len(moved),
            "target": preview["target"], "message": f"已归档 {len(moved)} 个文件"}


class TaskStore:
    def __init__(self, directory, archive_root=ARCHIVE_ROOT, approval_ttl=1800):
        self.directory = Path(directory)
        self.archive_root = Path(archive_root).resolve()
        self.approval_ttl = approval_ttl
        self.lock = threading.RLock()
        self.execution_lock = threading.Lock()
        self.jobs = {}
        self.directory.mkdir(parents=True, exist_ok=True)
        for file in self.directory.glob("*.json"):
            job = json.loads(file.read_text(encoding="utf-8"))
            self.jobs[job["id"]] = job
            if job["status"] == "running":
                self._end(job, "interrupted", "服务重启中断了执行，请检查已记录的结果后重新提交")

    def _save(self, job):
        job["updated_at"] = timestamp()
        path = self.directory / f"{job['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _event(self, job, event, message):
        job["events"].append({"at": timestamp(), "event": event, "message": message})

    def _end(self, job, status, message):
        job["status"], job["message"] = status, message
        for step in job["steps"]:
            if step["status"] in {"pending", "approved", "running", "awaiting_confirmation"}:
                step["status"] = "skipped" if status != "interrupted" else "interrupted"
        self._event(job, status, message)
        self._save(job)

    def _expire(self, job):
        approval = job.get("confirmation")
        if job["status"] == "awaiting_confirmation" and approval["expires_at"] <= time.time():
            self._end(job, "expired", "确认已过期，请重新提交以获取最新文件清单")

    def get(self, task_id):
        with self.lock:
            if task_id not in self.jobs:
                raise KeyError("任务不存在")
            job = self.jobs[task_id]
            self._expire(job)
            return json.loads(json.dumps(job))

    def recent(self):
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda job: job["created_at"], reverse=True)
            for job in jobs:
                self._expire(job)
            # Pending approvals remain accessible even when newer tasks fill the history.
            selected = [job for job in jobs if job["status"] == "awaiting_confirmation"]
            selected += [job for job in jobs if job["status"] != "awaiting_confirmation"][:50]
            return [self.get(job["id"]) for job in selected]

    def create(self, task, folder, plan):
        steps = plan.get("steps", []) if isinstance(plan, dict) else []
        if not isinstance(steps, list) or not steps or len(steps) > 5 or any(
            not isinstance(step, dict) or step.get("tool") not in ALLOWED_TOOLS
            or step["tool"] not in TOOL_POLICY for step in steps
        ):
            raise ValueError("执行计划包含未注册工具或步骤数量无效")
        job = {"id": uuid.uuid4().hex, "task": task, "folder": str(Path(folder).resolve()),
               "plan": plan, "status": "running", "message": "正在执行", "created_at": timestamp(),
               "steps": [{"tool": step["tool"], "status": "pending"} for step in steps],
               "results": [], "events": []}
        with self.lock:
            self.jobs[job["id"]] = job
            self._event(job, "created", "任务已创建")
            self._save(job)
        return self._advance(job["id"])

    def decide(self, task_id, confirmation_id, decision):
        if decision not in {"approve", "cancel"}:
            raise ValueError("decision 必须是 approve 或 cancel")
        with self.lock:
            job = self.get(task_id)
            if job["status"] != "awaiting_confirmation" or job["confirmation"]["id"] != confirmation_id:
                raise TaskConflict("此确认已处理、已过期或与当前任务不匹配")
            job = self.jobs[task_id]
            if decision == "cancel":
                self._end(job, "cancelled", "已取消后续操作；已完成步骤的结果已保留")
                return self.get(task_id)
            job["status"] = "running"
            step = next(step for step in job["steps"] if step["status"] == "awaiting_confirmation")
            step["status"] = "approved"
            self._event(job, "approved", f"已确认归档 {job['confirmation']['count']} 个文件")
            self._save(job)
        return self._advance(task_id)

    def _advance(self, task_id):
        # Legacy tools share output locations, so tool execution is serialized.
        with self.execution_lock:
            while True:
                with self.lock:
                    job = self.jobs[task_id]
                    if job["status"] != "running":
                        return self.get(task_id)
                    index = next((i for i, s in enumerate(job["steps"])
                                  if s["status"] in {"pending", "approved"}), None)
                    if index is None:
                        warnings = sum(len(result.get("problems", [])) for result in job["results"])
                        self._end(job, "completed", f"任务已完成，发现 {warnings} 项异常，请复核" if warnings else "任务已完成")
                        return self.get(task_id)
                    step = job["steps"][index]
                    needs_preview = TOOL_POLICY[step["tool"]] == "approval" and step["status"] != "approved"
                    if not needs_preview:
                        step["status"] = "running"
                        self._event(job, "step_started", step["tool"])
                        self._save(job)
                try:
                    if needs_preview:
                        preview = archive_preview(job["folder"], self.archive_root, task_id,
                                                  index, self.approval_ttl)
                        with self.lock:
                            job["confirmation"] = preview
                            step["preview"] = preview
                            step["status"] = job["status"] = "awaiting_confirmation"
                            job["message"] = f"等待确认归档 {preview['count']} 个文件"
                            self._event(job, "awaiting_confirmation", job["message"])
                            self._save(job)
                        return self.get(task_id)
                    if step["tool"] == "archive_files":
                        result = archive_approved(step["preview"])
                    else:
                        result = execute_tool(step["tool"], job["task"], job["folder"])
                except Exception as exc:
                    result = {"tool": step["tool"], "error": str(exc)}
                with self.lock:
                    step["result"] = result
                    job["results"].append(result)
                    if result.get("error"):
                        step["status"] = "failed"
                        self._end(job, "failed", result["error"])
                        return self.get(task_id)
                    step["status"] = "completed"
                    self._event(job, "step_completed", step["tool"])
                    self._save(job)
