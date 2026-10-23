"""An operating-system lock prevents two servers from recovering the same database."""
from __future__ import annotations

import os
from pathlib import Path


class DatabaseInstanceLock:
    def __init__(self, db_path: Path):
        resolved = db_path.resolve()
        self.path = resolved.with_name(resolved.name + ".lock")
        self._stream = None

    def acquire(self) -> None:
        if self._stream is not None:
            raise RuntimeError("数据库实例锁已取得。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RuntimeError("该数据库已有工作台实例运行，请使用现有实例。") from None
        self._stream = stream

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        # Keep the lock inode: unlinking it lets a second process lock a new file.
