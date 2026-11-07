# Tests for the exclusive file lock used by the spend ledgers.
import os
import threading
import time

from klear.filelock import BINARY, exclusive


def test_lock_excludes_other_descriptors(tmp_path):
    """Each thread opens the file itself, so its descriptor locks like a separate process would."""
    path, inside, overlaps = tmp_path / "x.lock", [0], []

    def work():
        fd = os.open(path, os.O_RDWR | os.O_CREAT | BINARY)
        try:
            for _ in range(5):
                with exclusive(fd):
                    inside[0] += 1
                    overlaps.append(inside[0])
                    time.sleep(0.005)
                    inside[0] -= 1
        finally:
            os.close(fd)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == [1] * 20


def test_lock_is_released_after_an_error(tmp_path):
    path = tmp_path / "x.lock"
    first, second = (os.open(path, os.O_RDWR | os.O_CREAT | BINARY) for _ in range(2))
    try:
        try:
            with exclusive(first):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with exclusive(second):
            pass
    finally:
        os.close(first)
        os.close(second)


def test_lock_leaves_file_position_and_content_alone(tmp_path):
    path = tmp_path / "x.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | BINARY)
    try:
        os.write(fd, b"12345\n")
        os.lseek(fd, 2, os.SEEK_SET)
        with exclusive(fd):
            assert os.lseek(fd, 0, os.SEEK_CUR) == 2
            assert os.read(fd, 3) == b"345"
    finally:
        os.close(fd)
    assert path.read_bytes() == b"12345\n"
