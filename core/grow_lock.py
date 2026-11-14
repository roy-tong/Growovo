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


def locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with run_lock(self.path):
            return method(self, *args, **kwargs)
    return wrapped
