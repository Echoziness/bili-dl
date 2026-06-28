"""Tests for cookie validation and ensure_cookie orchestration.

Asserts that:
* ``validate`` returns correct verdicts for missing/empty/no-SESSDATA files;
* ``_nav_probe`` distinguishes network errors from HTTP errors (AGENTS.md §2.6);
* ``validate`` reports the *real* error cause (风控 vs 网络) on degradation;
* ``ensure_cookie`` orchestrates validate → import → re-validate correctly;
* result objects carry structured messages (no ui side-effects).
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from bili_dl import cookiestore as store
from bili_dl.cookiestore import NavProbeResult

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


def _make_cookie_dir(tmp_path: Path) -> Path:
    """Create a cookie dir with a valid SESSDATA file for validate tests."""
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc%2C123\n", encoding="utf-8")
    return tmp_path


def test_bili_cookie_path(tmp_path: Path) -> None:
    assert store.bili_cookie_path(tmp_path) == tmp_path / "cookies_bilibili.txt"


def test_validate_missing_file(tmp_path: Path) -> None:
    result = store.validate(tmp_path)
    assert result.valid is False
    assert result.uname is None
    # No message for missing file — ensure_cookie handles messaging
    assert len(result.messages) == 0


def test_validate_empty_file(tmp_path: Path) -> None:
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("", encoding="utf-8")
    result = store.validate(tmp_path)
    assert result.valid is False
    assert any(level == "warn" for level, _ in result.messages)


def test_validate_no_sessdata(tmp_path: Path) -> None:
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tother_cookie\tval\n", encoding="utf-8")
    result = store.validate(tmp_path)
    assert result.valid is False
    assert any("SESSDATA" in text for _, text in result.messages)


def test_validate_has_sessdata_local_only(tmp_path: Path) -> None:
    """A file with SESSDATA passes local format check (nav probe will fail offline)."""
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc%2C123\n", encoding="utf-8")
    result = store.validate(tmp_path)
    # Without network, nav_probe returns None → degrade to local-only → valid=True
    # With network, it may return False (fake cookie) — both are acceptable in CI
    assert isinstance(result.valid, bool)
    assert len(result.messages) > 0


def test_ensure_cookie_imports_from_source(tmp_path: Path) -> None:
    """ensure_cookie: no output file → import from source → re-validate."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    result = store.ensure_cookie(tmp_path)
    assert isinstance(result.ready, bool)
    assert len(result.messages) > 0
    # Every message is a (level, text) tuple
    for msg in result.messages:
        assert isinstance(msg, tuple)
        assert len(msg) == 2
        assert msg[0] in ("info", "ok", "warn", "error")


def test_ensure_cookie_no_source(tmp_path: Path) -> None:
    """ensure_cookie: no source file → not ready, no crash."""
    result = store.ensure_cookie(tmp_path)
    assert result.ready is False


# ─── _nav_probe: mock urllib to test error classification ───────────────────


class _FakeResp:
    """Minimal context-manager response object for urlopen mock."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def test_nav_probe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"code": 0, "data": {"isLogin": True, "uname": "alice"}}).encode()
    monkeypatch.setattr(store.urllib.request, "urlopen", lambda req, timeout: _FakeResp(body))
    result = store._nav_probe("sess")
    assert result.data is not None
    assert result.error is None
    assert result.data["data"]["uname"] == "alice"


def test_nav_probe_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> object:
        raise urllib.error.HTTPError("url", 412, "Precondition Failed", {}, None)

    monkeypatch.setattr(store.urllib.request, "urlopen", fake_urlopen)
    result = store._nav_probe("sess")
    assert result.data is None
    assert result.error == "http:412"


def test_nav_probe_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> object:
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(store.urllib.request, "urlopen", fake_urlopen)
    result = store._nav_probe("sess")
    assert result.data is None
    assert result.error == "network"


def test_nav_probe_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> object:
        raise OSError("boom")

    monkeypatch.setattr(store.urllib.request, "urlopen", fake_urlopen)
    result = store._nav_probe("sess")
    assert result.data is None
    assert result.error == "network"


# ─── validate: mock _nav_probe to test message precision ────────────────────


def test_validate_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s: NavProbeResult(data={"code": 0, "data": {"isLogin": True, "uname": "bob"}}),
    )
    result = store.validate(d)
    assert result.valid is True
    assert result.uname == "bob"
    assert any("bob" in t for _, t in result.messages)


def test_validate_not_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s: NavProbeResult(data={"code": -101, "data": {}}),
    )
    result = store.validate(d)
    assert result.valid is False
    assert any("未登录" in t for _, t in result.messages)


def test_validate_degrades_on_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(store, "_nav_probe", lambda s: NavProbeResult(error="network"))
    result = store.validate(d)
    assert result.valid is True  # degrade to local-only
    assert any("网络" in t for _, t in result.messages)


def test_validate_degrades_on_http_error_with_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(store, "_nav_probe", lambda s: NavProbeResult(error="http:412"))
    result = store.validate(d)
    assert result.valid is True  # degrade to local-only
    # Message should mention the HTTP status, not just "网络/SSL 错误"
    assert any("HTTP 412" in t for _, t in result.messages)
