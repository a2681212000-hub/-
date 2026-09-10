"""Persistent reusable workflow templates."""
import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


class WorkflowStore:
    def __init__(self, directory, runner, validator):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.validator = validator
        self.lock = threading.RLock()
        self.items = {}
        for file in self.directory.glob("*.json"):
            item = json.loads(file.read_text(encoding="utf-8"))
            self.items[item["id"]] = item

    def _save(self, item):
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
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

    def get(self, workflow_id):
        with self.lock:
            if workflow_id not in self.items:
                raise KeyError("工作流模板不存在")
            return self._copy(self.items[workflow_id])

    def create(self, name, task, folder, plan):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("模板名称需为 1 至 80 个字符")
        if not isinstance(task, str) or not task.strip() or len(task) > 2000:
            raise ValueError("任务描述需为 1 至 2000 个字符")
        if not isinstance(folder, str) or not folder.strip() or len(folder) > 1000:
            raise ValueError("输入目录无效")
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"输入文件夹不存在：{path}")
        safe_plan = self.validator(plan, task)
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "task": task.strip(),
            "folder": str(path),
            "plan": safe_plan,
            "created_at": now,
            "last_job_id": None,
            "last_status": None,
            "last_message": None,
        }
        with self.lock:
            self.items[item["id"]] = item
            self._save(item)
            return self._copy(item)

    def run(self, workflow_id):
        with self.lock:
            item = self.items.get(workflow_id)
            if item is None:
                raise KeyError("工作流模板不存在")
            snapshot = self._copy(item)
        job = self.runner(snapshot["task"], snapshot["folder"], snapshot["plan"])
        with self.lock:
            current = self.items[workflow_id]
            current["last_job_id"] = job["id"]
            current["last_status"] = job["status"]
            current["last_message"] = job["message"]
            self._save(current)
            return {"workflow": self._copy(current), "job": job}

    def remove(self, workflow_id):
        with self.lock:
            item = self.items.get(workflow_id)
            if item is None:
                raise KeyError("工作流模板不存在")
            del self.items[workflow_id]
            (self.directory / f"{workflow_id}.json").unlink(missing_ok=True)
            return self._copy(item)
