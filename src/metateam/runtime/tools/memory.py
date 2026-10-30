"""Memory-library append / read / write / remove / list."""

from __future__ import annotations

from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext


def register_memory_tools(reg: ToolRegistry, ctx: ToolContext) -> None:
    settings = ctx.settings

    def memory_append(
        note: str,
        category: str = "",
        title: str = "",
        tags: str = "",
    ) -> str:
        from ...services.memory import append_memory

        return append_memory(
            settings.memory_file,
            note,
            category=category,
            title=title,
            tags=tags,
        )

    def memory_read(
        category: str = "",
        tags: str = "",
        memory_id: str = "",
    ) -> str:
        from ...services.memory import read_memory_detail

        return read_memory_detail(
            settings.memory_file,
            category=category,
            tags=tags,
            memory_id=memory_id,
            include_disabled=True,
            max_chars=8000,
        )

    def memory_search(
        query: str,
        category: str = "",
        tags: str = "",
        top_k: int = 5,
    ) -> str:
        from ...services.memory import search_library

        return search_library(
            settings.memory_file,
            query,
            category=category,
            tags=tags,
            top_k=top_k,
            include_disabled=False,
        )

    def memory_list() -> str:
        from ...services.memory import list_library_text

        return list_library_text(settings.memory_file)

    def memory_remove(match: str = "", memory_id: str = "") -> str:
        from ...services.memory import remove_memory

        return remove_memory(settings.memory_file, match=match, memory_id=memory_id)

    def memory_write(
        content: str,
        memory_id: str = "",
        category: str = "",
        title: str = "",
        tags: str = "",
    ) -> str:
        from ...services.memory import replace_memory

        return replace_memory(
            settings.memory_file,
            content,
            memory_id=memory_id,
            category=category,
            title=title,
            tags=tags,
        )

    reg.register(
        Tool(
            "memory_append",
            "Save a durable note into the memory library (not the workspace). "
            "Pass category (creates it if missing), optional title and comma-separated tags. "
            "Enabled notes appear in the catalog; pin short prefs for auto-inject. "
            "Requires user approval.",
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "category": {
                        "type": "string",
                        "description": "Library category, e.g. General / a project name",
                    },
                    "title": {"type": "string"},
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags",
                    },
                },
                "required": ["note"],
            },
            memory_append,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "memory_remove",
            "Delete one memory-library entry by id or by matching title/content/tags. "
            "Requires user approval.",
            {
                "type": "object",
                "properties": {
                    "match": {
                        "type": "string",
                        "description": "Substring to find in title/content/tags",
                    },
                    "memory_id": {
                        "type": "string",
                        "description": "Exact id from memory_list (preferred)",
                    },
                },
                "required": [],
            },
            memory_remove,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "memory_write",
            "Update one memory-library note. Prefer memory_id; otherwise creates a new note. "
            "Requires user approval.",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "memory_id": {"type": "string"},
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "tags": {"type": "string"},
                },
                "required": ["content"],
            },
            memory_write,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "memory_search",
            "Search the memory library by keywords (title/tags/content). "
            "Returns ranked ids and summaries — then call memory_read(memory_id=…) "
            "for full bodies. Prefer this over dumping the whole library.",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or phrase to match",
                    },
                    "category": {"type": "string"},
                    "tags": {"type": "string"},
                    "top_k": {
                        "type": "integer",
                        "description": "Max hits (default 5, max 20)",
                    },
                },
                "required": ["query"],
            },
            memory_search,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "memory_read",
            "Read memory-library note bodies. Prefer memory_id from memory_search "
            "or memory_list. Optional filters: category, tags. Includes disabled "
            "notes; only enabled notes appear in the catalog / search by default.",
            {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "tags": {"type": "string"},
                    "memory_id": {"type": "string"},
                },
                "required": [],
            },
            memory_read,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "memory_list",
            "List memory-library categories and notes (id, title, tags, ON/off/pin). "
            "Use before memory_read/write when you need an id; prefer memory_search "
            "when looking for a topic.",
            {"type": "object", "properties": {}, "required": []},
            memory_list,
            parallel_safe=True,
        )
    )
