"""Tests for the minimal Web QR login transport, without real network calls."""

from __future__ import annotations

import http.cookiejar
import urllib.request

import pytest

from bili_dl import authqr


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


def test_netscape_lines_keep_only_bilibili_cookies() -> None:
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(_cookie("SESSDATA", "session"))
    jar.set_cookie(_cookie("bili_jct", "csrf"))
    jar.set_cookie(_cookie("other_secret", "must-not-leak", ".example.com"))

    lines = authqr._netscape_lines(jar)

    text = "\n".join(lines)
    assert "SESSDATA\tsession" in text
    assert "bili_jct\tcsrf" in text
    assert "other_secret" not in text


def test_fallback_cookie_url_extracts_only_login_fields() -> None:
    lines = authqr._fallback_cookie_lines(
        {
            "url": "https://passport.biligame.com/crossDomain?SESSDATA=abc%252C123"
            "&bili_jct=csrf&unrelated=must-not-store"
        }
    )

    text = "\n".join(lines)
    assert "SESSDATA\tabc%2C123" in text
    assert "bili_jct\tcsrf" in text
    assert "unrelated" not in text


def test_has_sessdata_requires_bilibili_domain() -> None:
    assert authqr._has_sessdata([".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tok"])
    assert not authqr._has_sessdata([".example.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tevil"])


def test_poll_returns_refresh_token_from_a_successful_qr_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(_cookie("SESSDATA", "session"))
    session = authqr.QrSession(
        url="https://example.com/qr",
        key="key",
        jar=jar,
        opener=urllib.request.build_opener(),
    )
    monkeypatch.setattr(
        authqr.transport,
        "fetch_json",
        lambda opener, request, timeout: (
            {"code": 0, "data": {"code": 0, "refresh_token": "refresh-token"}},
            None,
        ),
    )

    result = authqr.poll(session, sleep=False)

    assert result.success is True
    assert result.refresh_token == "refresh-token"


def test_poll_keeps_waiting_while_qr_is_scanned_but_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bilibili repeats 86090 until App confirmation; it is not a failure."""
    replies = iter(
        [
            ({"code": 0, "data": {"code": 86090, "message": "二维码已扫码未确认"}}, None),
            ({"code": 0, "data": {"code": 86090, "message": "二维码已扫码未确认"}}, None),
            ({"code": 0, "data": {"code": 86038, "message": "二维码已失效"}}, None),
        ]
    )
    monkeypatch.setattr(
        authqr.transport, "fetch_json", lambda opener, request, timeout: next(replies)
    )
    session = authqr.QrSession(
        url="https://example.com/qr",
        key="key",
        jar=http.cookiejar.CookieJar(),
        opener=urllib.request.build_opener(),
    )

    result = authqr.poll(session, sleep=False)

    assert result.success is False
    assert result.messages == [("warn", "[登录] 二维码已过期，请重新执行 bili-dl login")]
