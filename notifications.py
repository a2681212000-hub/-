"""Persistent local notification drafts produced by Office Agent tasks."""
import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class NotificationStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.items = {}
        for file in self.directory.glob("*.json"):
            item = json.loads(file.read_text(encoding="utf-8"))
            self.items[item["id"]] = item

    def _save(self, item):
        item["updated_at"] = timestamp()
        path = self.directory / f"{item['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def list(self):
        with self.lock:
            return [deepcopy(item) for item in sorted(
                self.items.values(), key=lambda value: value["created_at"], reverse=True
            )]

    def capture_job(self, job):
        captured = []
        with self.lock:
            existing = {item.get("source") for item in self.items.values()}
            for index, result in enumerate(job.get("results", [])):
                if result.get("tool") != "notify":
                    continue
                source = f"{job['id']}:{index}"
                if source in existing:
                    continue
                item = {
                    "id": uuid.uuid4().hex,
                    "source": source,
                    "task_id": job["id"],
                    "task": job["task"],
                    "title": result.get("title", "Office Agent 任务通知"),
                    "body": result.get("body", ""),
                    "output": result.get("output"),
                    "status": "draft",
                    "created_at": timestamp(),
                }
                self.items[item["id"]] = item
                self._save(item)
                captured.append(deepcopy(item))
        return captured

    def mark(self, notification_id, status):
        if status not in {"draft", "read"}:
            raise ValueError("通知状态无效")
        with self.lock:
            item = self.items.get(notification_id)
            if item is None:
                raise KeyError("通知草稿不存在")
            item["status"] = status
            self._save(item)
            return deepcopy(item)
