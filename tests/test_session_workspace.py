from pathlib import Path

from metateam.services.session import workspace_matches


def test_workspace_matches_normalizes_slash_and_case(tmp_path: Path) -> None:
    folder = tmp_path / "Proj"
    folder.mkdir()
    assert workspace_matches(str(folder), folder)
    mixed = str(folder).replace("\\", "/")
    assert workspace_matches(mixed, folder)


def test_workspace_matches_rejects_other_project(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert not workspace_matches(str(a), b)
    assert not workspace_matches("", b)
    assert not workspace_matches(str(a), "")
