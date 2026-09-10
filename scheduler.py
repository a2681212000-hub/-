"""Persistent local schedules for recurring Office Agent tasks."""
import json
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


class ScheduleConflict(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class ScheduleStore:
    def __init__(self, directory, runner, check_seconds=5):
        self.directory = Path(directory)
        self.runner = runner
        self.check_seconds = check_seconds
        self.lock = threading.RLock()
        self.items = {}
        self.directory.mkdir(parents=True, exist_ok=True)
        for file in self.directory.glob("*.json"):
            item = json.loads(file.read_text(encoding="utf-8"))
            if item.get("running"):
                item["running"] = False
                item["last_status"] = "interrupted"
                item["last_message"] = "服务重启中断了自动任务，可手动重新运行"
                item["next_run_at"] = time.time() + item["interval_minutes"] * 60
            self.items[item["id"]] = item
            if item.get("last_status") == "interrupted":
                self._save(item)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="office-agent-scheduler", daemon=True)
        self.thread.start()

    def _save(self, item):
        item["updated_at"] = timestamp()
        path = self.directory / f"{item['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _copy(self, item):
        return deepcopy(item)

    def list(self):
        with self.lock:
            return [self._copy(item) for item in sorted(
                self.items.values(), key=lambda value: value["created_at"], reverse=True
            )]

    def get(self, schedule_id):
        with self.lock:
            if schedule_id not in self.items:
                raise KeyError("自动任务不存在")
            return self._copy(self.items[schedule_id])

    def create(self, name, task, folder, interval_minutes):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("自动任务名称需为 1 至 80 个字符")
        if not isinstance(task, str) or not task.strip() or len(task) > 2000:
            raise ValueError("任务描述需为 1 至 2000 个字符")
        if not isinstance(folder, str) or not folder.strip() or len(folder) > 1000:
            raise ValueError("输入目录无效")
        try:
            interval_minutes = int(interval_minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("运行间隔必须是整数分钟") from exc
        if not 5 <= interval_minutes <= 10080:
            raise ValueError("运行间隔需在 5 分钟至 7 天之间")
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"输入文件夹不存在：{path}")
        now = time.time()
        item = {
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "task": task.strip(),
            "folder": str(path),
            "interval_minutes": interval_minutes,
            "enabled": True,
            "running": False,
            "created_at": timestamp(),
            "next_run_at": now + interval_minutes * 60,
            "last_run_at": None,
            "last_job_id": None,
            "last_status": None,
            "last_message": None,
        }
        with self.lock:
            self.items[item["id"]] = item
            self._save(item)
            return self._copy(item)

    def set_enabled(self, schedule_id, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须是布尔值")
        with self.lock:
            item = self.items.get(schedule_id)
            if item is None:
                raise KeyError("自动任务不存在")
            item["enabled"] = enabled
            if enabled and item["next_run_at"] <= time.time():
                item["next_run_at"] = time.time() + item["interval_minutes"] * 60
            self._save(item)
            return self._copy(item)

    def remove(self, schedule_id):
        with self.lock:
            item = self.items.get(schedule_id)
            if item is None:
                raise KeyError("自动任务不存在")
            if item["running"]:
                raise ScheduleConflict("自动任务正在运行，暂时不能删除")
            del self.items[schedule_id]
            (self.directory / f"{schedule_id}.json").unlink(missing_ok=True)
            return self._copy(item)

    def _claim(self, schedule_id, force=False):
        with self.lock:
            item = self.items.get(schedule_id)
            if item is None:
                raise KeyError("自动任务不存在")
            if item["running"]:
                raise ScheduleConflict("自动任务正在运行")
            if not force and (not item["enabled"] or item["next_run_at"] > time.time()):
                return None
            item["running"] = True
            item["last_run_at"] = timestamp()
            self._save(item)
            return self._copy(item)

    def _execute(self, item):
        try:
            job = self.runner(item["task"], item["folder"])
            status, message, job_id = job["status"], job["message"], job["id"]
        except Exception as exc:
            job = None
            status, message, job_id = "failed", str(exc), None
        with self.lock:
            current = self.items.get(item["id"])
            if current is None:
                return job
            current["running"] = False
            current["last_job_id"] = job_id
            current["last_status"] = status
            current["last_message"] = message
            current["next_run_at"] = time.time() + current["interval_minutes"] * 60
            self._save(current)
            return job

    def run_now(self, schedule_id):
        item = self._claim(schedule_id, force=True)
        return {"schedule": self.get(schedule_id), "job": self._execute(item)}

    def _loop(self):
        while not self.stop_event.wait(self.check_seconds):
            due = []
            with self.lock:
                ids = [item["id"] for item in self.items.values()]
            for schedule_id in ids:
                try:
                    item = self._claim(schedule_id)
                except (KeyError, ScheduleConflict):
                    continue
                if item is not None:
                    due.append(item)
            for item in due:
                self._execute(item)

    def close(self):
        self.stop_event.set()
