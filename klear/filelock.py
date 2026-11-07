"""Exclusive lock on an open file that blocks other processes and threads, on POSIX and Windows."""

import errno
import os
import sys
import time
from contextlib import contextmanager

# Lock files are opened in binary mode so Windows does not translate newlines in what is read or written.
BINARY = getattr(os, "O_BINARY", 0)

if sys.platform == "win32":
    import msvcrt

    # Windows locks a byte range and enforces it on reads and writes through other handles, so the
    # lock sits far past the end of any lock file, where it guards nothing that is ever read or written.
    _OFFSET = 1 << 40
    _BUSY = (errno.EACCES, errno.EDEADLK)

    def _lock_byte(fd: int, mode: int) -> None:
        """msvcrt locks from the current position, so move there and put the position back after."""
        here = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            os.lseek(fd, _OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, mode, 1)
        finally:
            os.lseek(fd, here, os.SEEK_SET)

    @contextmanager
    def exclusive(fd: int):
        """Hold an exclusive lock on the open file descriptor, waiting until it is free."""
        while True:
            try:
                # LK_LOCK gives up after ten seconds, so poll the non-blocking form instead.
                _lock_byte(fd, msvcrt.LK_NBLCK)
                break
            except OSError as e:
                if e.errno not in _BUSY:
                    raise
                time.sleep(0.02)
        try:
            yield
        finally:
            _lock_byte(fd, msvcrt.LK_UNLCK)

else:
    import fcntl

    @contextmanager
    def exclusive(fd: int):
        """Hold an exclusive lock on the open file descriptor, waiting until it is free."""
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
