"""Minimal interactive CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runtime.agent import Agent
from .core.config import get_settings
from .runtime.context import messages_tokens
from .services.session import latest_session, load_session, save_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metateam",
        description="EdgeSidekick — multi-agent workspace runtime",
    )
    parser.add_argument("-q", "--query", help="Single-shot prompt (non-interactive)")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Override workspace directory",
    )
    parser.add_argument(
        "--dump-messages",
        type=Path,
        help="Write final message trace JSON to this path",
    )
    parser.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Resume the latest saved session transcript",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save transcript under sessions/ after the run",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.workspace:
        settings.workspace = args.workspace.resolve()
        settings.workspace.mkdir(parents=True, exist_ok=True)

    if not settings.api_key:
        print(
            "Missing OPENAI_API_KEY. Copy .env.example to .env and fill it in.",
            file=sys.stderr,
        )
        return 2

    def on_event(line: str) -> None:
        print(line, file=sys.stderr)

    agent = Agent(settings, on_event=on_event)
    if args.continue_session:
        path = latest_session(settings.root)
        if not path:
            print("No saved session found.", file=sys.stderr)
            return 1
        _meta, messages = load_session(path)
        agent.messages = messages
        print(f"(resumed {path.name})", file=sys.stderr)

    def _persist() -> None:
        if not args.save and not args.query:
            # interactive: always checkpoint on exit commands handled below
            return
        if args.save or args.dump_messages:
            p = save_session(
                settings.root,
                agent.messages,
                model=settings.model,
                workspace=settings.workspace,
            )
            print(f"[saved] {p}", file=sys.stderr)
        if args.dump_messages:
            args.dump_messages.write_text(
                json.dumps(agent.messages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    if args.query:
        result = agent.run(args.query)
        print(result.text)
        if args.save or args.dump_messages:
            _persist()
        print(
            f"\n[stats] iters={result.iterations} tokens≈{messages_tokens(result.messages)}",
            file=sys.stderr,
        )
        return 0

    print("EdgeSidekick  ·  /exit  /stats  /new  /save  /skills")
    print(f"workspace: {settings.workspace}")
    print(f"model: {settings.model}")
    while True:
        try:
            user = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/exit", "/quit", ":q"):
            p = save_session(
                settings.root,
                agent.messages,
                model=settings.model,
                workspace=settings.workspace,
            )
            print(f"saved {p.name}")
            break
        if user == "/stats":
            print(f"tokens≈{messages_tokens(agent.messages)} messages={len(agent.messages)}")
            continue
        if user == "/new":
            agent = Agent(settings, on_event=on_event)
            print("(new session)")
            continue
        if user == "/save":
            p = save_session(
                settings.root,
                agent.messages,
                model=settings.model,
                workspace=settings.workspace,
            )
            print(f"saved {p}")
            continue
        if user == "/skills":
            for s in agent.skills:
                print(f"- {s.name}: {s.description}")
            continue
        result = agent.run(user)
        print(f"\nagent> {result.text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
