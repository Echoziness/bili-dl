"""Contract tests for the shared Bilibili HTTP transport boundary."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

import pytest

from bili_dl import transport
from bili_dl.config import REFERER, USER_AGENT


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class _Opener:
    def __init__(self, body: bytes = b"{}", error: Optional[Exception] = None) -> None:
        self.body = body
        self.error = error

    def open(self, request: urllib.request.Request, timeout: float) -> _Response:
        if self.error is not None:
            raise self.error
        return _Response(self.body)


def test_cookie_opener_none_leaves_environment_proxy_to_urllib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[urllib.request.BaseHandler, ...]] = []
    sentinel = urllib.request.build_opener()
    monkeypatch.setattr(
        transport.urllib.request,
        "build_opener",
        lambda *handlers: captured.append(handlers) or sentinel,
    )

    assert transport.cookie_opener(proxy=None) is sentinel
    assert captured == [()]


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        ("", {}),
        (
            "http://127.0.0.1:7890",
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        ),
    ],
)
def test_cookie_opener_explicit_proxy_semantics(
    proxy: str,
    expected: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[urllib.request.BaseHandler, ...]] = []
    sentinel = urllib.request.build_opener()
    monkeypatch.setattr(
        transport.urllib.request,
        "build_opener",
        lambda *handlers: captured.append(handlers) or sentinel,
    )

    assert transport.cookie_opener(proxy=proxy) is sentinel
    assert len(captured[0]) == 1
    handler = captured[0][0]
    assert isinstance(handler, urllib.request.ProxyHandler)
    assert handler.proxies == expected


def test_request_merges_browser_headers_without_overwriting_cookie() -> None:
    req = transport.request(
        "https://api.bilibili.com/example",
        b"x=1",
        {"Cookie": "SESSDATA=session", "X-Test": "yes"},
    )

    assert req.get_header("Cookie") == "SESSDATA=session"
    assert req.get_header("User-agent") == USER_AGENT
    assert req.get_header("Referer") == REFERER
    assert req.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert req.get_header("X-test") == "yes"


def test_fetch_json_returns_a_mapping() -> None:
    payload = {"code": 0, "data": {"isLogin": True}}
    opener = _Opener(json.dumps(payload).encode())

    result, failure = transport.fetch_json(opener, transport.request("https://example.com"))

    assert result == payload
    assert failure is None


@pytest.mark.parametrize(
    ("opener", "kind", "status"),
    [
        (
            _Opener(
                error=urllib.error.HTTPError(
                    "https://example.com/?token=secret", 412, "secret body", {}, None
                )
            ),
            "http",
            412,
        ),
        (_Opener(error=urllib.error.URLError("SESSDATA=secret")), "network", None),
        (_Opener(b"<html>secret</html>"), "bad_json", None),
        (_Opener(b'["secret"]'), "bad_data", None),
    ],
)
def test_fetch_json_returns_only_safe_structured_failures(
    opener: _Opener, kind: str, status: Optional[int]
) -> None:
    result, failure = transport.fetch_json(opener, transport.request("https://example.com"))

    assert result is None
    assert failure == transport.HttpFailure(kind, status)
    rendered = f"{failure!r} {failure.describe()}"
    assert "secret" not in rendered
    assert "SESSDATA" not in rendered


def test_fetch_text_uses_the_same_network_failure_vocabulary() -> None:
    opener = _Opener(error=OSError("credential=secret"))

    result, failure = transport.fetch_text(opener, transport.request("https://example.com"))

    assert result is None
    assert failure == transport.HttpFailure("network")
