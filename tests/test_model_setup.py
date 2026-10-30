from __future__ import annotations

from metateam.services.model_config import (
    ModelEntry,
    ModelProvider,
    ModelRef,
    ModelSetup,
    _pick_model_setup,
)


def _setup(main_id: str, key: str, provider_id: str = "p1") -> ModelSetup:
    return ModelSetup(
        providers=[
            ModelProvider(
                id=provider_id,
                name="demo",
                models=[ModelEntry(id=main_id, name="m", api_key=key, base_url="http://x")],
            )
        ],
        main=ModelRef(provider_id=provider_id, model_id=main_id),
    )


def test_pick_model_setup_prefers_workspace_when_main_has_key() -> None:
    ws = _setup("ws", "sk-ws")
    acct = _setup("acct", "sk-acct")
    picked = _pick_model_setup(ws, acct)
    assert picked.resolve(picked.main)[3] == "sk-ws"


def test_pick_model_setup_falls_back_to_account_when_workspace_is_demo() -> None:
    ws = _setup("ws", "")
    acct = _setup("acct", "sk-acct")
    picked = _pick_model_setup(ws, acct)
    assert picked.resolve(picked.main)[3] == "sk-acct"


def test_find_entry_matches_without_provider_id() -> None:
    setup = _setup("mdl_1", "sk-1", provider_id="prov_a")
    setup.main = ModelRef(provider_id="", model_id="mdl_1")
    prov, entry = setup.find_entry(setup.main)
    assert entry is not None
    assert entry.api_key == "sk-1"
    assert prov is not None


def test_parse_entry_max_tokens() -> None:
    from metateam.services.model_config import _parse_entry

    entry = _parse_entry(
        {
            "id": "m1",
            "name": "qwen",
            "base_url": "http://x/v1",
            "api_key": "sk-1",
            "max_tokens": 4096,
        }
    )
    assert entry.max_tokens == 4096
    stored = entry.masked_dict()
    assert stored["max_tokens"] == 4096


def test_llm_omits_auto_tool_choice() -> None:
    from metateam.core.config import Settings
    from metateam.runtime.llm import LLM, _is_tool_choice_error, _retry_kwargs_for_exc

    settings = Settings(
        demo_mode=True,
        api_key="",
        thinking_enabled=False,
        reasoning_effort="",
    )
    llm = LLM(settings, max_tokens=2048)
    tools = [{"type": "function", "function": {"name": "list_dir"}}]
    kwargs = llm._call_kwargs([{"role": "user", "content": "hi"}], tools, 0.2)
    assert "tool_choice" not in kwargs
    assert kwargs["tools"] == tools
    assert kwargs["max_tokens"] == 2048
    assert kwargs["parallel_tool_calls"] is True

    err = Exception(
        "Error code: 400 - {'error': {'message': '\"auto\" tool choice requires "
        "--enable-auto-tool-choice and --tool-call-parser to be set'}}"
    )
    assert _is_tool_choice_error(err)
    retry = _retry_kwargs_for_exc(kwargs, err)
    assert retry is not None
    assert "tools" not in retry
    assert "tool_choice" not in retry
    assert "parallel_tool_calls" not in retry


def test_llm_parallel_tool_calls_retries_without_flag() -> None:
    from metateam.core.config import Settings
    from metateam.runtime.llm import (
        LLM,
        _is_parallel_tool_calls_error,
        _retry_kwargs_for_exc,
    )

    settings = Settings(
        demo_mode=True,
        api_key="",
        thinking_enabled=False,
        reasoning_effort="",
    )
    llm = LLM(settings)
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    kwargs = llm._call_kwargs([{"role": "user", "content": "hi"}], tools, 0.2)
    assert kwargs["parallel_tool_calls"] is True

    err = Exception("Error code: 400 - unknown parameter: parallel_tool_calls")
    assert _is_parallel_tool_calls_error(err)
    retry = _retry_kwargs_for_exc(kwargs, err)
    assert retry is not None
    assert retry["tools"] == tools
    assert "parallel_tool_calls" not in retry

    llm._disable_parallel_tool_calls = True
    kwargs2 = llm._call_kwargs([{"role": "user", "content": "hi"}], tools, 0.2)
    assert "parallel_tool_calls" not in kwargs2
    assert kwargs2["tools"] == tools

    type_err = TypeError(
        "Completions.create() got an unexpected keyword argument 'parallel_tool_calls'"
    )
    assert _is_parallel_tool_calls_error(type_err)
