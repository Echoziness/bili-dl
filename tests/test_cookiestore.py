"""Tests for cookie validation and ensure_cookie orchestration.

Asserts that:
* ``validate`` returns correct verdicts for missing/empty/no-SESSDATA files;
* ``_nav_probe`` distinguishes network errors from HTTP errors (AGENTS.md §2.6);
* ``validate`` reports the *real* error cause (风控 vs 网络) on degradation;
* ``ensure_cookie`` orchestrates validate → import → re-validate correctly.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from bili_dl import cookiestore as store

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


def _make_cookie_dir(tmp_path: Path) -> Path:
    """Create a cookie dir with a valid SESSDATA file for validate tests."""
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc%2C123\n", encoding="utf-8")
    return tmp_path


def test_validate_missing_file(tmp_path: Path) -> None:
    result = store.validate(tmp_path)
    assert result.valid is False
    assert len(result.messages) == 0


def test_validate_empty_file(tmp_path: Path) -> None:
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("", encoding="utf-8")
    result = store.validate(tmp_path)
    assert result.valid is False


def test_validate_no_sessdata(tmp_path: Path) -> None:
    out = store.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tother_cookie\tval\n", encoding="utf-8")
    result = store.validate(tmp_path)
    assert result.valid is False


def test_ensure_cookie_imports_from_source(tmp_path: Path) -> None:
    """ensure_cookie: no output file → import from source → re-validate."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    result = store.ensure_cookie(tmp_path)
    assert isinstance(result.ready, bool)
    assert len(result.messages) > 0


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
    data, error = store._nav_probe("sess")
    assert data is not None
    assert error is None
    assert data["data"]["uname"] == "alice"


def test_nav_probe_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> object:
        raise urllib.error.HTTPError("url", 412, "Precondition Failed", {}, None)

    monkeypatch.setattr(store.urllib.request, "urlopen", fake_urlopen)
    data, error = store._nav_probe("sess")
    assert data is None
    assert error == "http:412"


def test_nav_probe_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> object:
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(store.urllib.request, "urlopen", fake_urlopen)
    data, error = store._nav_probe("sess")
    assert data is None
    assert error == "network"


def test_nav_probe_badjson(monkeypatch: pytest.MonkeyPatch) -> None:
    """B站 returns HTML instead of JSON → 'badjson', not 'network' (§2.20)."""
    body = b"<html><body>Server Error</body></html>"

    monkeypatch.setattr(store.urllib.request, "urlopen", lambda req, timeout: _FakeResp(body))
    data, error = store._nav_probe("sess")
    assert data is None
    assert error == "badjson"


# ─── validate: mock _nav_probe to test message precision ────────────────────


def test_validate_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store,
        "_nav_probe",
        lambda s: ({"code": 0, "data": {"isLogin": True, "uname": "bob"}}, None),
    )
    result = store.validate(d)
    assert result.valid is True
    assert result.uname == "bob"


def test_validate_not_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(store, "_nav_probe", lambda s: ({"code": -101, "data": {}}, None))
    result = store.validate(d)
    assert result.valid is False


def test_validate_degrades_on_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(store, "_nav_probe", lambda s: (None, "network"))
    result = store.validate(d)
    assert result.valid is True
    assert any("网络" in t for _, t in result.messages)


def test_validate_degrades_on_http_error_with_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(store, "_nav_probe", lambda s: (None, "http:412"))
    result = store.validate(d)
    assert result.valid is True
    assert any("HTTP 412" in t for _, t in result.messages)


def test_validate_degrades_on_badjson(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B站 returns non-JSON → report 'non-JSON', not '网络/SSL' (§2.20)."""
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(store, "_nav_probe", lambda s: (None, "badjson"))
    result = store.validate(d)
    assert result.valid is True
    assert any("非 JSON" in t for _, t in result.messages)
    assert not any("网络" in t for _, t in result.messages)


# ─── _extract_sessdata: precise field matching (§2.6 hardening) ──────────────


def test_extract_sessdata_www_domain(tmp_path: Path) -> None:
    """www.bilibili.com domain contains 'bilibili.com' → SESSDATA extracted."""
    lines = ["www.bilibili.com\tFALSE\t/\tFALSE\t0\tSESSDATA\twww_sess"]
    assert store._extract_sessdata(lines) == "www_sess"


def test_extract_sessdata_other_domain_dropped() -> None:
    """A SESSDATA-named cookie on a non-bilibili domain must not be returned."""
    lines = [".example.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tevil"]
    assert store._extract_sessdata(lines) is None


def test_extract_sessdata_wrong_name() -> None:
    """A bilibili line whose name column is not 'SESSDATA' is skipped."""
    lines = [".bilibili.com\tTRUE\t/\tFALSE\t0\tother_cookie\tval"]
    assert store._extract_sessdata(lines) is None


def test_extract_sessdata_short_line() -> None:
    """Lines with fewer than 7 fields can't carry a value → None."""
    lines = [".bilibili.com\tTRUE\t/"]
    assert store._extract_sessdata(lines) is None


def test_validate_logged_in_no_uname(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """isLogin True but uname missing → falls back to '?' rather than KeyError."""
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store, "_nav_probe", lambda s: ({"code": 0, "data": {"isLogin": True}}, None)
    )
    result = store.validate(d)
    assert result.valid is True
    assert result.uname == "?"


# ─── ensure_cookie: orchestration branches ───────────────────────────────────


def test_ensure_cookie_already_valid_skips_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the existing cookie validates online, import is never called."""
    d = _make_cookie_dir(tmp_path)
    monkeypatch.setattr(
        store, "_nav_probe", lambda s: ({"code": 0, "data": {"isLogin": True, "uname": "u"}}, None)
    )
    from bili_dl.cookiesource import ImportResult

    import_calls: list[Path] = []

    def fake_import(cd: Path | None, dest: Path | None = None) -> ImportResult:
        import_calls.append(cd or Path())
        return ImportResult(success=True)

    monkeypatch.setattr(store, "import_cookie", fake_import)
    result = store.ensure_cookie(d)
    assert result.ready is True
    assert import_calls == []  # validate succeeded → no import


def test_ensure_cookie_import_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """validate fails → source found → import fails → ready False."""
    src = tmp_path / "s.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    from bili_dl.cookiesource import ImportResult

    monkeypatch.setattr(
        store,
        "import_cookie",
        lambda cd, dest=None: ImportResult(success=False, messages=[("error", "bad")]),
    )
    result = store.ensure_cookie(tmp_path)
    assert result.ready is False
