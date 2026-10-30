"""Skill dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str = ""
    _body_loaded: bool = field(default=False, repr=False)

    def read_body(self) -> str:
        """Lazy-load SKILL.md body (keeps session create fast)."""
        if self._body_loaded:
            return self.body
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            self._body_loaded = True
            return self.body
        from .load import _parse_frontmatter

        _, body = _parse_frontmatter(raw)
        self.body = body
        self._body_loaded = True
        return self.body
