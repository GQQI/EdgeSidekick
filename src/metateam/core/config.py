"""Runtime configuration."""

from __future__ import annotations

import copy
import os
import shutil
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name, "").strip().strip('"')
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


# src/metateam/core/config.py
# parents[0]=core, [1]=metateam, [2]=src, [3]=repo
_SRC_FROM_FILE = Path(__file__).resolve().parents[2]
_REPO_FROM_FILE = Path(__file__).resolve().parents[3]

# Packaged Windows install: Electron sets SIDEKICK_REPO_ROOT (read-only payload)
# and SIDEKICK_DATA_DIR (%APPDATA%\EdgeSidekick). Unset → source-tree layout.
_repo_override = _path_from_env("SIDEKICK_REPO_ROOT")
REPO_ROOT = _repo_override or _REPO_FROM_FILE
SRC_ROOT = _path_from_env("SIDEKICK_SRC_ROOT") or (
    REPO_ROOT / "src" if _repo_override else _SRC_FROM_FILE
)
# Runtime data lives under src/ in dev; AppData when installed.
ROOT = _path_from_env("SIDEKICK_DATA_DIR") or SRC_ROOT
BACKEND_ROOT = ROOT  # back-compat alias

_data_env = ROOT / ".env"
_repo_env = REPO_ROOT / ".env"
if _data_env.is_file():
    load_dotenv(_data_env)
load_dotenv(_repo_env, override=False)

if not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
    _bundled_pw = REPO_ROOT / "ms-playwright"
    if _bundled_pw.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_bundled_pw)
# Default China-friendly Chromium CDN (official azureedge often hangs with no logs).
if not os.getenv("PLAYWRIGHT_DOWNLOAD_HOST"):
    os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = "https://npmmirror.com/mirrors/playwright"
if not os.getenv("PIP_INDEX_URL"):
    os.environ["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
if not os.getenv("PIP_TRUSTED_HOST"):
    os.environ["PIP_TRUSTED_HOST"] = "pypi.tuna.tsinghua.edu.cn"


def infer_context_limit(model: str) -> int:
    """Best-effort context window from the model name when META_CONTEXT_LIMIT is unset."""
    m = (model or "").lower()
    rules: list[tuple[str, int]] = [
        ("gpt-4.1", 1_047_576),
        ("gpt-4o", 128_000),
        ("gpt-4-turbo", 128_000),
        ("o3", 200_000),
        ("o1", 200_000),
        ("claude", 200_000),
        ("gemini-2", 1_048_576),
        ("gemini-1.5", 1_048_576),
        ("minimax-m3", 1_000_000),
        ("minimax", 204_800),
        ("deepseek", 64_000),
        ("qwen-plus", 131_072),
        ("qwen-max", 32_768),
        ("qwen-turbo", 131_072),
        ("qwen3", 131_072),
        ("qwen2.5", 131_072),
        ("qwen", 32_768),
        ("glm-4", 128_000),
        ("kimi", 128_000),
        ("moonshot", 128_000),
        ("llama", 32_768),
    ]
    for key, n in rules:
        if key in m:
            return n
    return 48_000


def working_context_budget(window: int, explicit: int = 0) -> int:
    """Hot working set for compression and the UI.

    The provider window stays the ceiling. The working set is a fraction of it
    so a long turn can finish before compaction, without waiting until the
    whole window is full. An explicit META_CONTEXT_LIMIT is kept when it is
    smaller than the window; a value that equals the full window is ignored.
    """
    window = max(0, int(window or 0))
    explicit = max(0, int(explicit or 0))
    if explicit > 0 and (window <= 0 or explicit < window):
        return max(8_000, min(explicit, window or explicit))
    if window <= 48_000:
        return window or 48_000
    budget = int(window * 0.45)
    if budget < 48_000:
        budget = min(window - 8_000, 56_000)
    return max(48_000, min(budget, 96_000, window - 8_000))


def _bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    subagent_model: str = field(
        default_factory=lambda: os.getenv("META_SUBAGENT_MODEL")
        or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    compress_model: str = field(
        default_factory=lambda: os.getenv("META_COMPRESS_MODEL")
        or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    review_model: str = field(
        default_factory=lambda: os.getenv("META_REVIEW_MODEL")
        or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )

    # Optional per-role credentials when subagent/compress use another provider
    subagent_api_key: str = ""
    subagent_base_url: str = ""
    compress_api_key: str = ""
    compress_base_url: str = ""

    demo_mode: bool = field(
        default_factory=lambda: _bool("META_DEMO_MODE", False)
        or not os.getenv("OPENAI_API_KEY", "").strip()
    )

    context_limit: int = field(default_factory=lambda: int(os.getenv("META_CONTEXT_LIMIT", "0") or "0") or 48000)
    keep_recent_tokens: int = int(os.getenv("META_KEEP_RECENT", "16000"))
    compress_trigger_ratio: float = float(os.getenv("META_COMPRESS_RATIO", "0.72"))
    # LLM summarization is at most one pass; remaining shrink is cheap trim.
    max_compress_attempts: int = int(os.getenv("META_COMPRESS_ATTEMPTS", "1"))

    # 0 = 40% of main context_limit, capped. Each child uses this budget, not the parent's.
    subagent_context_limit: int = int(os.getenv("META_SUB_CONTEXT_LIMIT", "0") or "0")

    # 0 = no cap; the loop runs until the model stops or the user hits Stop.
    max_iterations: int = int(os.getenv("META_MAX_ITERS", "0") or "0")
    subagent_max_iterations: int = int(os.getenv("META_SUB_MAX_ITERS", "0") or "0")
    max_concurrent_children: int = int(os.getenv("META_MAX_CHILDREN", "3"))
    max_spawn_depth: int = int(os.getenv("META_MAX_SPAWN_DEPTH", "2"))
    # No stream token for this many seconds → interrupt this LLM call and continue.
    llm_idle_timeout: int = int(os.getenv("META_LLM_IDLE_TIMEOUT", "75"))
    # Hard cap for one streamed completion (endless thinking).
    llm_stream_timeout: int = int(os.getenv("META_LLM_STREAM_TIMEOUT", "180"))
    # 0 disables the wall-clock cut-off; a child should return its own result
    # instead of being discarded while it is still reasoning.
    subagent_timeout: int = int(os.getenv("META_SUBAGENT_TIMEOUT", "0"))

    same_call_fail_limit: int = int(os.getenv("META_SAME_CALL_FAIL", "2"))
    tool_result_cap: int = int(os.getenv("META_TOOL_RESULT_CAP", "18000"))

    review_every_n_turns: int = int(os.getenv("META_REVIEW_EVERY", "6"))
    # Off by default — silent review burns tokens and can mutate MEMORY/skills
    auto_skill_review: bool = field(default_factory=lambda: _bool("META_AUTO_REVIEW", False))

    root: Path = ROOT
    workspace: Path = field(default_factory=lambda: ROOT / "workspace")
    skills_dir: Path = field(default_factory=lambda: ROOT / "skills")
    memory_file: Path = field(default_factory=lambda: ROOT / "memory" / "MEMORY.md")
    sessions_dir: Path = field(default_factory=lambda: ROOT / "sessions")
    data_dir: Path = field(default_factory=lambda: ROOT / "data")

    # On by default for local desktop use; mutating shell still requires approval.
    allow_shell: bool = field(default_factory=lambda: _bool("META_ALLOW_SHELL", True))
    # Shell cwd stays in workspace; outside paths need approval (not a copy FS)
    shell_sandbox: bool = field(default_factory=lambda: _bool("META_SHELL_SANDBOX", True))
    shell_timeout: int = int(os.getenv("META_SHELL_TIMEOUT", "20"))
    # Enable MCP tool discovery when mcp package + mcp.json servers are present
    mcp_enabled: bool = field(default_factory=lambda: _bool("META_MCP_ENABLED", True))

    host: str = field(default_factory=lambda: os.getenv("META_HOST", "127.0.0.1"))
    port: int = int(os.getenv("META_PORT", "8787"))

    provider: str = ""
    reasoning_effort: str = "medium"
    thinking_enabled: bool = True
    temperature: float = 0.2
    max_tokens: int = 0
    # Larger budget so delegate_task/subagent tool args & final summaries don't
    # get cut mid-JSON (was 0 → provider default, too small for structured calls).
    subagent_max_tokens: int = int(os.getenv("META_SUB_MAX_TOKENS", "8192") or "8192")
    compress_max_tokens: int = int(os.getenv("META_COMPRESS_MAX_TOKENS", "2500") or "2500")
    main_endpoint: Any = None
    subagent_endpoint: Any = None
    compress_endpoint: Any = None

    def clone(self) -> "Settings":
        return copy.copy(self)

    def ensure_dirs(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            bundled = SRC_ROOT / "memory" / "MEMORY.md"
            if bundled.is_file() and bundled.resolve() != self.memory_file.resolve():
                shutil.copy2(bundled, self.memory_file)
            else:
                self.memory_file.write_text(
                    "# MEMORY\n\nDurable facts about the user and environment.\n",
                    encoding="utf-8",
                )
        self._seed_bundled_skills()

    def _seed_bundled_skills(self) -> None:
        """Copy packaged example skills into the writable data dir once."""
        bundled = SRC_ROOT / "skills"
        if not bundled.is_dir():
            return
        try:
            if bundled.resolve() == self.skills_dir.resolve():
                return
        except OSError:
            return
        for skill_md in bundled.rglob("SKILL.md"):
            rel = skill_md.parent.relative_to(bundled)
            dest = self.skills_dir / rel
            if dest.exists():
                continue
            shutil.copytree(skill_md.parent, dest, dirs_exist_ok=True)


_SETTINGS: Settings | None = None
_REQUEST_SETTINGS: ContextVar[Settings | None] = ContextVar(
    "metateam_request_settings", default=None
)


def get_process_settings(reload: bool = False) -> Settings:
    """Process-wide env defaults. Do not put per-user workspace/skills here."""
    global _SETTINGS
    if _SETTINGS is None or reload:
        _SETTINGS = Settings()
        _SETTINGS.ensure_dirs()
        try:
            from ..services.model_config import apply_to_settings, load_model_config

            apply_to_settings(_SETTINGS, load_model_config())
        except Exception as exc:
            from .logutil import get_logger, log_exception

            log_exception(get_logger("metateam.config"), "apply_to_settings failed", exc)
    return _SETTINGS


def bind_request_settings(settings: Settings) -> None:
    _REQUEST_SETTINGS.set(settings)


def reset_request_settings() -> None:
    _REQUEST_SETTINGS.set(None)


def _build_request_settings(process: Settings) -> Settings:
    overlay = process.clone()
    try:
        from ..services.tenant_context import apply_knowledge_to_settings

        apply_knowledge_to_settings(overlay)
    except Exception as exc:
        from .logutil import get_logger, log_exception

        log_exception(get_logger("metateam.config"), "apply_knowledge_to_settings failed", exc)
    try:
        from ..services.workspace_store import apply_saved_workspace

        apply_saved_workspace(overlay)
    except Exception as exc:
        from .logutil import get_logger, log_exception

        log_exception(get_logger("metateam.config"), "apply_saved_workspace failed", exc)
    try:
        from ..services.model_config import apply_to_settings, load_model_config

        apply_to_settings(overlay, load_model_config())
    except Exception as exc:
        from .logutil import get_logger, log_exception

        log_exception(get_logger("metateam.config"), "request apply_to_settings failed", exc)
    return overlay


def get_settings(reload: bool = False) -> Settings:
    if reload:
        reset_request_settings()
        get_process_settings(reload=True)
    bound = _REQUEST_SETTINGS.get()
    if bound is not None:
        return bound
    overlay = _build_request_settings(get_process_settings())
    bind_request_settings(overlay)
    return overlay


def reload_settings() -> Settings:
    return get_settings(reload=True)
