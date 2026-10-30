"""Request bodies for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    mode: str = "agent"
    display: str | None = None
    # When creating a new session (no session_id), pin it to this folder so the
    # chat follows the UI's selected workspace instead of the tenant default.
    workspace: str | None = None
    # Social / group chat: text reply only — do not bind workbench tools
    # (prevents delegate_task when the user says "评价其他智能体").
    disable_tools: bool = False
    # Social (会话版) one-shot call: if a fresh session has to be created for
    # this turn, keep it out of the workbench history listing.
    hidden: bool = False
    # Group harness already assigned this member. Do not let them spawn another team.
    block_delegate: bool = False
    # One-shot classification (who the message is for). No tools, no plan, one reply.
    classify: bool = False
    # Session binding: optional subset of enabled MCP server ids.
    # Omit = use session-persisted binding; empty = auto all enabled.
    # Non-empty list = only those servers (composer filter).
    mcp_server_ids: list[str] | None = None
    # Session binding for local plugins (plugin_*). Same semantics as mcp_server_ids.
    plugin_ids: list[str] | None = None


class SessionCreate(BaseModel):
    workspace: str | None = None


class MemoryUpdate(BaseModel):
    content: str


class MemoryLibraryUpdate(BaseModel):
    version: int = 1
    categories: list[dict[str, Any]] = Field(default_factory=list)


class ModelUpdate(BaseModel):
    version: int | None = None
    providers: list[dict[str, Any]] | None = None
    main: dict[str, Any] | None = None
    subagent: dict[str, Any] | None = None
    compress: dict[str, Any] | None = None
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    subagent_model: str | None = None
    compress_model: str | None = None
    review_model: str | None = None
    reasoning_effort: str | None = None
    thinking_enabled: bool | None = None
    demo_mode: bool | None = None
    temperature: float | None = None


class ModelSelect(BaseModel):
    role: str
    provider_id: str
    model: str


class WorkspaceSet(BaseModel):
    path: str | None = None
    name: str | None = None
    create: bool = False


class WorkspaceCreate(BaseModel):
    path: str | None = None
    name: str | None = None


class FileWrite(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = ""
    workspace: str | None = None


class FileCreate(BaseModel):
    path: str = Field(..., min_length=1)
    kind: str = "file"
    workspace: str | None = None


class FileRename(BaseModel):
    path: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)
    workspace: str | None = None


class FileMove(BaseModel):
    path: str = Field(..., min_length=1)
    dest_dir: str = "."
    workspace: str | None = None


class FileReveal(BaseModel):
    path: str = "."
    workspace: str | None = None


class UndoBody(BaseModel):
    id: str | None = None
    session_id: str | None = None
    workspace: str | None = None


class GitPathsBody(BaseModel):
    paths: list[str] = Field(default_factory=list)
    workspace: str | None = None


class GitCommitBody(BaseModel):
    message: str = ""
    workspace: str | None = None


class GitCheckoutBody(BaseModel):
    branch: str = Field(..., min_length=1, max_length=200)
    create: bool = False
    workspace: str | None = None


class GitRemoteBody(BaseModel):
    url: str = Field(..., min_length=3, max_length=500)
    name: str = "origin"
    workspace: str | None = None


class AuthSetupBody(BaseModel):
    username: str = Field(..., min_length=2)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


class AuthLoginBody(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)
    username: str | None = None


class AuthCreateUserBody(BaseModel):
    username: str = Field(..., min_length=2)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


class AuthProfileBody(BaseModel):
    """Partial profile update; omitted fields stay untouched."""

    display_name: str | None = None
    email: str | None = None
    title: str | None = None
    bio: str | None = None
    avatar: str | None = None


class McpUpdateBody(BaseModel):
    version: int | None = 1
    servers: list[dict[str, Any]] = Field(default_factory=list)
    # Community Claude Desktop / Cursor shape (merged on save).
    mcpServers: dict[str, Any] | None = None


class McpTestBody(BaseModel):
    id: str = ""
    name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True



class McpCatalogInstallBody(BaseModel):
    id: str = Field(..., min_length=1)
    args: list[str] | None = None
    env: dict[str, str] = Field(default_factory=dict)


class McpCatalogUninstallBody(BaseModel):
    id: str = Field(..., min_length=1)



class SkillWriteBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    description: str = Field(..., min_length=1, max_length=800)
    content: str = Field(..., min_length=1)
    overwrite: bool = True
    previous: str | None = None


class SkillValidateBody(BaseModel):
    name: str = ""
    description: str = ""
    content: str = ""
    markdown: str = ""


class SkillFileWriteBody(BaseModel):
    content: str = Field(..., min_length=0)


class SkillFileCreateBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)
    type: Literal["file", "folder"] = "file"
    content: str = ""


class SkillImportDirBody(BaseModel):
    path: str = Field(..., min_length=2, max_length=1024)
    overwrite: bool = False


class TruncateBody(BaseModel):
    keep_user_turns: int = Field(0, ge=0)
    restore_files: bool = False


class ReplayBody(BaseModel):
    user_turn: int = Field(..., ge=0)
    restore_files: bool = True


class ApprovalDecision(BaseModel):
    approved: bool
    remember: bool = False
    patch_args: dict[str, Any] | None = None


class PrefsUpdate(BaseModel):
    auto_approve_tools: bool | None = None
    mcp_in_chat: bool | None = None
    plugins_in_chat: bool | None = None
    systemone_url: str | None = None
    systemone_model: str | None = None
    systemone_key: str | None = None


class AskAnswer(BaseModel):
    choice: str = ""
    text: str = ""
    option_label: str = ""


class PlanConfirm(BaseModel):
    approved: bool = True
    summary: str | None = None
    tasks: list[dict[str, Any]] | None = None


class BrowserStartBody(BaseModel):
    url: str = ""
    headless: bool = False


class BrowserNavigateBody(BaseModel):
    url: str


class BrowserActBody(BaseModel):
    lane: str = "social"
    op: str = "click"
    x: float = 0
    y: float = 0
    text: str = ""
    key: str = ""


class BrowserPickBody(BaseModel):
    timeout_ms: int = 60000
    with_screenshot: bool = True


class ScheduleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=8000)
    kind: Literal["once", "daily", "weekly", "interval", "monthly"]
    at: str | None = None  # "YYYY-MM-DD HH:MM" (once)
    time: str | None = None  # "HH:MM" (daily/weekly/monthly)
    weekdays: list[int] | None = None  # weekly: 0=Mon..6=Sun
    interval_minutes: int | None = Field(None, ge=5, le=10080)
    until: str | None = None  # inclusive end date YYYY-MM-DD
    month_day: int | None = Field(None, ge=1, le=31)
    session_id: str = Field(..., min_length=1)
    enabled: bool = True


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(None, min_length=1, max_length=120)
    prompt: str | None = Field(None, min_length=1, max_length=8000)
    at: str | None = None
    time: str | None = None
    weekdays: list[int] | None = None
    interval_minutes: int | None = Field(None, ge=5, le=10080)
    until: str | None = None
    month_day: int | None = Field(None, ge=1, le=31)
