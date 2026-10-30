from __future__ import annotations

from metateam.core.hostinfo import (
    _is_kylin_fields,
    host_prompt_block,
    network_available,
    parse_os_release,
    reset_hostinfo_cache,
)
from metateam.runtime.prompts import _host_environment_block
from metateam.runtime.tools.web import web_search


def test_parse_os_release_kylin() -> None:
    fields = parse_os_release(
        'NAME="Kylin Linux Desktop"\nID=kylin\nPRETTY_NAME="Kylin Linux Desktop V10"\n'
    )
    assert _is_kylin_fields(fields)
    fields2 = parse_os_release('NAME="银河麒麟"\nID=neokylin\n')
    assert _is_kylin_fields(fields2)
    fields3 = parse_os_release('NAME="Ubuntu"\nID=ubuntu\n')
    assert not _is_kylin_fields(fields3)


def test_offline_env_blocks_web_search(monkeypatch) -> None:
    monkeypatch.setenv("META_OFFLINE", "1")
    reset_hostinfo_cache()
    assert network_available(force_refresh=True) is False
    block = host_prompt_block()
    assert "OFFLINE" in block
    # Tool-usage rules for OFFLINE live once in CORE; the host block is status.
    out = web_search("anything")
    assert out.startswith("ERROR:")
    assert "offline" in out.lower()
    assert "Do not retry" in out


def test_host_environment_block_has_time_and_os() -> None:
    reset_hostinfo_cache()
    text = _host_environment_block()
    assert "Host environment" in text
    assert "Local time:" in text
    assert "Network:" in text
    assert "OS:" in text
