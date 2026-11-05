import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def short_dir():
    """A short, user-owned real directory for sockets and lock sidecars.

    pytest's tmp_path is too long for AF_UNIX on macOS, and /tmp itself is a root-owned symlink
    that private_dir() rightly refuses, so sockets never sit directly in /tmp.
    """
    path = Path(tempfile.mkdtemp(prefix="hr-", dir="/private/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)
