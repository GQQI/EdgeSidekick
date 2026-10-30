from __future__ import annotations

import json
from pathlib import Path

from metateam.services.verify_detect import detect_verify_command, grounding_verify_hint


def test_pytest_ini(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == "python -m pytest"
    assert "python -m pytest" in grounding_verify_hint(tmp_path)


def test_package_json_test(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run", "lint": "eslint ."}}),
        encoding="utf-8",
    )
    assert detect_verify_command(tmp_path) == "npm run test"


def test_package_json_placeholder_falls_to_tsc(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert detect_verify_command(tmp_path) == "npx tsc --noEmit"


def test_empty(tmp_path: Path) -> None:
    assert detect_verify_command(tmp_path) == ""
    assert grounding_verify_hint(tmp_path) == ""
