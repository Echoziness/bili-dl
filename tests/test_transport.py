"""Contract tests for the shared Bilibili HTTP transport boundary."""

from __future__ import annotations

import gzip
import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

from bili_dl import transport
from bili_dl.config import REFERER, USER_AGENT


def test_truncated_response_is_a_safe_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def truncated(self: object) -> bytes:
        raise http.client.IncompleteRead(b"secret")

    monkeypatch.setattr(_Response, "read", truncated)
    payload, failure = transport.fetch_json(_Opener(), transport.request("https://example.com"))
    assert payload is None and failure == transport.HttpFailure("network")
    assert "secret" not in repr(failure)


def test_invalid_utf8_is_not_silently_replaced() -> None:
    payload, failure = transport.fetch_json(
        _Opener(b'{"message":"\xff"}'), transport.request("https://example.com")
    )
    assert payload is None and failure == transport.HttpFailure("bad_data")


class _Response:
    def __init__(self, body: bytes, content_encoding: Optional[str] = None) -> None:
        self.body = body
        self.headers = {"Content-Encoding": content_encoding} if content_encoding else {}

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return "https://www.bilibili.com/video/BV1Got26ZE5K/?p=2"

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class _Opener:
    def __init__(
        self,
        body: bytes = b"{}",
        error: Optional[Exception] = None,
        content_encoding: Optional[str] = None,
    ) -> None:
        self.body = body
        self.error = error
        self.content_encoding = content_encoding

    def open(self, request: urllib.request.Request, timeout: float) -> _Response:
        if self.error is not None:
            raise self.error
        return _Response(self.body, self.content_encoding)


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


def test_fetch_json_decompresses_gzip_before_decoding() -> None:
    payload = {"code": 0, "data": {"refresh": True}}
    opener = _Opener(gzip.compress(json.dumps(payload).encode()), content_encoding="gzip")

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


def test_fetch_text_decompresses_gzip_correspond_page() -> None:
    page = '<div id="1-name">refresh-csrf</div>'
    opener = _Opener(gzip.compress(page.encode()), content_encoding="gzip")

    result, failure = transport.fetch_text(opener, transport.request("https://example.com"))

    assert result == page
    assert failure is None


def test_fetch_text_rejects_invalid_gzip_as_bad_data() -> None:
    opener = _Opener(b"not-a-gzip-stream", content_encoding="gzip")

    result, failure = transport.fetch_text(opener, transport.request("https://example.com"))

    assert result is None
    assert failure == transport.HttpFailure("bad_data")


def test_load_cookie_jar_failure_is_safe_and_does_not_create_a_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    jar, error = transport.load_cookie_jar(path)
    assert jar is None and error == "无法读取现有 Cookie"
    assert not path.exists()


def test_resolve_share_url_keeps_query_without_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_Response, "read", lambda self: pytest.fail("Must not read video HTML"))
    url, failure = transport.resolve_url(_Opener(), "https://b23.tv/example", 15)
    assert url == "https://www.bilibili.com/video/BV1Got26ZE5K/?p=2"
    assert failure is None


@pytest.mark.parametrize(
    "exception,expected",
    [
        (urllib.error.URLError("secret"), transport.HttpFailure("network")),
        (
            urllib.error.HTTPError("https://b23.tv/example", 412, "secret", {}, None),
            transport.HttpFailure("http", 412),
        ),
    ],
)
def test_resolve_share_url_returns_safe_failures(
    exception: Exception, expected: transport.HttpFailure
) -> None:
    url, failure = transport.resolve_url(_Opener(error=exception), "https://b23.tv/example", 15)
    assert url is None
    assert failure == expected
    assert "secret" not in failure.describe()
