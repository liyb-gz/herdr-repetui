import os

import pytest

from herdr_repetui.locking import global_lock, private_dir


def test_creates_missing_dir_private(short_dir) -> None:
    target = short_dir / "new"
    private_dir(target)
    assert target.is_dir() and (target.stat().st_mode & 0o777) == 0o700


def test_existing_0755_dir_is_accepted_and_left_alone(short_dir) -> None:
    target = short_dir / "ok"
    target.mkdir()
    target.chmod(0o755)
    private_dir(target)
    assert (target.stat().st_mode & 0o777) == 0o755


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757, 0o770, 0o702])
def test_existing_group_or_other_writable_dir_is_refused_not_chmodded(short_dir, mode) -> None:
    target = short_dir / "open"
    target.mkdir()
    target.chmod(mode)
    with pytest.raises(PermissionError):
        private_dir(target)
    assert (target.stat().st_mode & 0o777) == mode          # never silently fixed up


def test_symlinked_dir_is_refused(short_dir) -> None:
    real = short_dir / "real"
    real.mkdir(mode=0o700)
    link = short_dir / "link"
    os.symlink(real, link)
    with pytest.raises(PermissionError):
        private_dir(link)


def test_non_directory_is_refused(short_dir) -> None:
    target = short_dir / "file"
    target.write_text("")
    with pytest.raises(OSError):
        private_dir(target)


def test_global_lock_refuses_world_writable_parent(short_dir) -> None:
    parent = short_dir / "shared"
    parent.mkdir()
    parent.chmod(0o777)
    with pytest.raises(PermissionError):
        with global_lock(parent / "l"):
            pytest.fail("lock must not be taken")
    assert not (parent / "l").exists()
