from __future__ import annotations

import subprocess
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from alan import served_sha as served_sha_mod


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    served_sha_mod.served_sha.cache_clear()
    yield
    served_sha_mod.served_sha.cache_clear()


def test_served_sha_returns_stripped_stdout_on_success() -> None:
    mock_result = MagicMock(returncode=0, stdout="deadbeef\n")
    with patch("alan.served_sha.subprocess.run", return_value=mock_result):
        assert served_sha_mod.served_sha() == "deadbeef"


def test_served_sha_returns_none_on_nonzero_returncode() -> None:
    mock_result = MagicMock(returncode=128, stdout="")
    with patch("alan.served_sha.subprocess.run", return_value=mock_result):
        assert served_sha_mod.served_sha() is None


def test_served_sha_returns_none_on_file_not_found() -> None:
    with patch("alan.served_sha.subprocess.run", side_effect=FileNotFoundError):
        assert served_sha_mod.served_sha() is None


def test_served_sha_returns_none_on_timeout() -> None:
    with patch(
        "alan.served_sha.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
    ):
        assert served_sha_mod.served_sha() is None


def test_served_sha_is_cached() -> None:
    mock_result = MagicMock(returncode=0, stdout="cafef00d\n")
    with patch("alan.served_sha.subprocess.run", return_value=mock_result) as mock_run:
        assert served_sha_mod.served_sha() == "cafef00d"
        assert served_sha_mod.served_sha() == "cafef00d"
    assert mock_run.call_count == 1
