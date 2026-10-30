from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from metateam.core.netguard import blocked_http_reason


def test_public_literal_allowed() -> None:
    assert blocked_http_reason("https://8.8.8.8/dns") is None


def test_loopback_blocked_by_default() -> None:
    assert blocked_http_reason("http://127.0.0.1:5173/")
    assert blocked_http_reason("http://localhost:3000/")
    assert blocked_http_reason("http://[::1]/")


def test_loopback_allowed_when_opted_in() -> None:
    assert blocked_http_reason("http://127.0.0.1:5173/", allow_loopback=True) is None
    assert blocked_http_reason("http://localhost:5173/", allow_loopback=True) is None


def test_private_and_link_local_blocked() -> None:
    assert blocked_http_reason("http://10.0.0.8/admin")
    assert blocked_http_reason("http://192.168.1.1/")
    assert blocked_http_reason("http://172.16.0.1/")
    assert blocked_http_reason("http://169.254.169.254/latest/meta-data/")


def test_metadata_host_blocked() -> None:
    assert blocked_http_reason("http://metadata.google.internal/")
    assert blocked_http_reason("http://metadata/")


def test_non_http_blocked() -> None:
    assert blocked_http_reason("file:///etc/passwd")
    assert blocked_http_reason("ftp://example.com/")


def test_hostname_resolving_to_private_blocked() -> None:
    fake = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0)),
    ]
    with patch("socket.getaddrinfo", return_value=fake):
        assert blocked_http_reason("https://internal.example/")


def test_hostname_resolving_to_public_allowed() -> None:
    fake = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    ]
    with patch("socket.getaddrinfo", return_value=fake):
        assert blocked_http_reason("https://example.com/path") is None


def test_mcp_remote_rejects_private_url() -> None:
    from metateam.services.mcp_config import McpServerConfig, _reject_private_remote

    srv = McpServerConfig(
        id="local",
        name="local",
        transport="http",
        url="http://127.0.0.1:3333/mcp",
    )
    with pytest.raises(ValueError, match="blocked"):
        _reject_private_remote(srv)
