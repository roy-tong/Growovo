"""Small fail-loud storage ports; domain modules keep their own schemas."""
from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List


class StorageError(RuntimeError):
    pass


class FileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        except OSError as exc:
            handle.close()
            raise StorageError("锁不可用：%s" % exc)
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def __enter__(self):
        if not self.acquire():
            raise StorageError("锁已被占用：%s" % self.path)
        return self

    def __exit__(self, *args):
        self.release()


def atomic_write_text(path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


class AtomicJSONStore:
    def __init__(self, path, *, schema: str = "", default=None):
        self.path = Path(path)
        self.schema = schema
        self.default = default if default is not None else {}

    def load(self):
        if not self.path.is_file():
            return copy.deepcopy(self.default)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError("JSON 存储不可读 %s：%s" % (self.path, exc))
        if not isinstance(payload, dict):
            raise StorageError("JSON 存储必须是对象：%s" % self.path)
        if self.schema and payload.get("schema_version") != self.schema:
            raise StorageError("JSON schema 不匹配：%s" % self.path)
        return payload

    def save(self, payload: Dict[str, Any]):
        if not isinstance(payload, dict):
            raise StorageError("只能保存 JSON 对象")
        value = copy.deepcopy(payload)
        if self.schema:
            value["schema_version"] = self.schema
        return atomic_write_text(self.path, json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True))


class AppendOnlyJSONL:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, record: Dict[str, Any]):
        if not isinstance(record, dict):
            raise StorageError("JSONL 记录必须是对象")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.path.with_suffix(self.path.suffix + ".lock")):
            raw = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            fd = os.open(str(self.path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
            try:
                written = os.write(fd, raw)
                if written != len(raw):
                    raise StorageError("JSONL 短写：%s" % self.path)
            finally:
                os.close(fd)

    def all(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows = []
        with self.path.open(encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise StorageError("JSONL 第 %d 行损坏 %s：%s" % (number, self.path, exc))
                if not isinstance(row, dict):
                    raise StorageError("JSONL 第 %d 行不是对象：%s" % (number, self.path))
                rows.append(row)
        return rows
