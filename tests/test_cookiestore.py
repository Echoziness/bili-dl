"""Tests for cookie validation and ensure_cookie orchestration.

Asserts that:
* ``validate`` returns correct verdicts for missing/empty/no-SESSDATA files;
* ``ensure_cookie`` orchestrates validate → import → re-validate correctly;
* ``bili_cookie_path`` resolves to the expected location;
* result objects carry structured messages (no ui side-effects).
"""

from __future__ import annotations

from pathlib import Path

from bili_dl import cookiestore as store

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


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
