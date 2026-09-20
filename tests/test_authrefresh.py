"""Protocol tests for Web-session renewal without live Bilibili credentials."""

from __future__ import annotations

import http.cookiejar
import urllib.request
from pathlib import Path

import pytest

from bili_dl import authrefresh


def _cookie(name: str, value: str, domain: str = ".bilibili.com") -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _jar() -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(_cookie("SESSDATA", "session"))
    jar.set_cookie(_cookie("bili_jct", "csrf"))
    return jar


def test_correspond_parser_extracts_only_target_div() -> None:
    parser = authrefresh._RefreshCsrfParser()
    parser.feed('<div id="other">ignore</div><div id="1-name">csrf-token</div>')
    assert parser.value == "csrf-token"


def test_correspond_path_is_rsa_oaep_hex_with_runtime_dependency() -> None:
    pytest.importorskip("cryptography")
    path, error = authrefresh._correspond_path(1_700_000_000_000)
    assert error is None
    assert path is not None
    assert len(path) == 256
    assert all(char in "0123456789abcdef" for char in path)


def test_netscape_lines_never_serializes_other_domains() -> None:
    jar = _jar()
    jar.set_cookie(_cookie("other_secret", "must-not-leak", ".example.com"))

    rendered = "\n".join(authrefresh._netscape_lines(jar))

    assert "SESSDATA\tsession" in rendered
    assert "other_secret" not in rendered


def test_check_and_refresh_records_a_successful_no_refresh_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jar = _jar()
    proxies: list[str | None] = []
    monkeypatch.setattr(authrefresh.transport, "load_cookie_jar", lambda path: (jar, None))
    real_cookie_opener = authrefresh.transport.cookie_opener
    monkeypatch.setattr(
        authrefresh.transport,
        "cookie_opener",
        lambda current_jar, proxy: proxies.append(proxy) or real_cookie_opener(current_jar, proxy),
    )
    monkeypatch.setattr(
        authrefresh.transport,
        "fetch_json",
        lambda opener, request: ({"code": 0, "data": {"refresh": False}}, None),
    )

    result = authrefresh.check_and_refresh(
        Path("unused"), "refresh-token", proxy="http://refresh:7890"
    )

    assert result.checked is True
    assert result.refreshed is False
    assert result.messages == []
    assert proxies == ["http://refresh:7890"]


def test_check_requirement_is_read_only_and_reports_bilibili_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jar = _jar()
    monkeypatch.setattr(authrefresh.transport, "load_cookie_jar", lambda path: (jar, None))
    monkeypatch.setattr(
        authrefresh.transport,
        "fetch_json",
        lambda opener, request: ({"code": 0, "data": {"refresh": False}}, None),
    )

    assert authrefresh.check_requirement(Path("unused")) == (False, None)


def test_check_and_refresh_returns_new_candidate_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jar = _jar()
    replies = iter(
        [
            ({"code": 0, "data": {"refresh": True, "timestamp": 1_700_000_000_000}}, None),
            ({"code": 0, "data": {"refresh_token": "new-token"}}, None),
        ]
    )
    monkeypatch.setattr(authrefresh.transport, "load_cookie_jar", lambda path: (jar, None))
    monkeypatch.setattr(authrefresh.transport, "fetch_json", lambda opener, request: next(replies))
    monkeypatch.setattr(authrefresh, "_refresh_csrf", lambda opener, timestamp: ("csrf-2", None))

    result = authrefresh.check_and_refresh(Path("unused"), "old-token")

    assert result.checked is True
    assert result.refreshed is True
    assert result.refresh_token == "new-token"
    assert result.old_refresh_token == "old-token"
    assert result.opener is not None


def test_confirm_uses_the_new_cookie_jar(monkeypatch: pytest.MonkeyPatch) -> None:
    jar = _jar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    result = authrefresh.RenewalResult(
        checked=True, refreshed=True, old_refresh_token="old-token", opener=opener, jar=jar
    )
    monkeypatch.setattr(
        authrefresh.transport, "fetch_json", lambda opener, request: ({"code": 0}, None)
    )

    assert authrefresh.confirm(result) is None


def test_confirm_pending_loads_the_current_cookie_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path = tmp_path / "cookies.txt"
    jar = _jar()
    monkeypatch.setattr(authrefresh.transport, "load_cookie_jar", lambda path: (jar, None))
    captured: list[tuple[http.cookiejar.CookieJar, str]] = []
    monkeypatch.setattr(
        authrefresh,
        "_confirm",
        lambda opener, loaded_jar, token: captured.append((loaded_jar, token)) or None,
    )

    assert authrefresh.confirm_pending(cookie_path, "old-token") is None
    assert captured == [(jar, "old-token")]
