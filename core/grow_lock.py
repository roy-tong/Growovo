"""Cross-process run locks; stale files are harmless because the OS owns the lock."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import threading
from functools import wraps

_LOCAL = threading.local()

class BusyRun(RuntimeError):
    pass

@contextmanager
def run_lock(path):
    key = str(Path(path).resolve())
    held = getattr(_LOCAL, "held", set())
    if key in held:
        yield
        return
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyRun("该任务已有一轮正在运行，跳过并发调用") from exc
        _LOCAL.held = held | {key}
        try:
            yield
        finally:
            _LOCAL.held = held
            fcntl.flock(fh, fcntl.LOCK_UN)


def lock_status(path):
    """Non-destructive probe: is another process holding this run's lock?

    The lock file itself is harmless after a crash (the OS releases flock), so
    only an actually held lock counts as a duplicate instance.
    """
    lock = Path(str(path) + ".lock")
    if not lock.exists():
        return {"held": False, "path": str(lock), "note": "没有锁文件"}
    with lock.open("a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"held": True, "path": str(lock),
                    "note": "另一实例正在执行（单实例锁被占用）"}
        except OSError as exc:  # pragma: no cover - 平台异常按未知处理
            return {"held": None, "path": str(lock), "note": f"锁状态未知：{exc}"}
        fcntl.flock(fh, fcntl.LOCK_UN)
        return {"held": False, "path": str(lock), "note": "锁空闲"}


def locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with run_lock(self.path):
            return method(self, *args, **kwargs)
    return wrapped
