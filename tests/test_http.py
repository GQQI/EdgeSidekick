from __future__ import annotations

from fastapi import HTTPException
import pytest

from metateam.api.http import git_result_or_400, raise_fs_http


def test_git_result_ok() -> None:
    assert git_result_or_400("committed abc") == "committed abc"


def test_git_result_error() -> None:
    with pytest.raises(HTTPException) as ei:
        git_result_or_400("ERROR: nothing staged")
    assert ei.value.status_code == 400
    assert "nothing staged" in str(ei.value.detail)


@pytest.mark.parametrize(
    "exc, status",
    [
        (FileNotFoundError("gone"), 404),
        (FileExistsError("dup"), 409),
        (ValueError("bad path"), 400),
        (OSError("disk"), 500),
        (RuntimeError("boom"), 500),
    ],
)
def test_raise_fs_http(exc: BaseException, status: int) -> None:
    with pytest.raises(HTTPException) as ei:
        raise_fs_http(exc)
    assert ei.value.status_code == status
    if isinstance(exc, FileNotFoundError):
        assert str(ei.value.detail).startswith("not found:")
    if isinstance(exc, FileExistsError):
        assert "already exists" in str(ei.value.detail)
