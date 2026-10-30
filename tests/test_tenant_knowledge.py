from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from metateam.services.tenant_context import (
    apply_knowledge_to_settings,
    ensure_tenant_knowledge,
    reset_user,
    set_user,
    tenant_memory_file,
    tenant_skills_dir,
)


def test_two_users_get_isolated_memory_and_skills() -> None:
    try:
        set_user("u_alice")
        sa, ma = ensure_tenant_knowledge("u_alice")
        set_user("u_bob")
        sb, mb = ensure_tenant_knowledge("u_bob")
        assert sa != sb
        assert ma != mb
        assert "u_alice" in str(sa)
        assert "u_bob" in str(mb)
        ma.write_text("# MEMORY\nalice-secret\n", encoding="utf-8")
        mb.write_text("# MEMORY\nbob-secret\n", encoding="utf-8")
        assert "alice-secret" in tenant_memory_file("u_alice").read_text(encoding="utf-8")
        assert "bob-secret" in tenant_memory_file("u_bob").read_text(encoding="utf-8")
        assert "alice-secret" not in tenant_memory_file("u_bob").read_text(encoding="utf-8")
        assert tenant_skills_dir("u_alice") != tenant_skills_dir("u_bob")
    finally:
        reset_user()


def test_apply_knowledge_sets_settings_paths() -> None:
    try:
        set_user("u_carol")
        s = SimpleNamespace(skills_dir=Path("."), memory_file=Path("MEMORY.md"))
        apply_knowledge_to_settings(s, "u_carol")
        assert "u_carol" in str(s.skills_dir)
        assert s.memory_file.name == "MEMORY.md"
        assert s.memory_file.exists()
    finally:
        reset_user()
