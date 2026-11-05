"""User-wide exclusive lock (flock is released automatically if the holder dies)."""

from __future__ import annotations

import fcntl
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockTimeout(RuntimeError):
    pass


def private_dir(path: Path) -> None:
    """Create `path` (0700) or accept an existing real directory that only we can modify.

    mkdir's mode never tightens an existing directory, so check it: if another user could write
    here they could replace the lock (or socket) pathname while we keep flocking the old inode.
    Never chmod silently; refuse instead. lstat, so a symlink is rejected, not followed."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError(f"refusing to use {path}: not a directory owned by this user")
    if info.st_mode & 0o022:
        raise PermissionError(f"refusing to use {path}: writable by group or others "
                              f"(mode {stat.S_IMODE(info.st_mode):04o}); run chmod go-w on it")


@contextmanager
def global_lock(path: Path, timeout: float = 15.0) -> Iterator[None]:
    private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise LockTimeout(f"timed out waiting for {path}") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)
