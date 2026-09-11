"""Tests for the isolated, atomically written session-renewal state."""

from __future__ import annotations

from pathlib import Path

from bili_dl import authstate


def test_save_then_load_state(tmp_path: Path) -> None:
    state = authstate.AuthState("refresh-secret", "2026-09-11")

    assert authstate.save(state, tmp_path) is None
    loaded, error = authstate.load(tmp_path)

    assert error is None
    assert loaded == state


def test_load_rejects_malformed_state_without_exposing_contents(tmp_path: Path) -> None:
    authstate.path(tmp_path).write_text('{"refresh_token": 42}', encoding="utf-8")

    loaded, error = authstate.load(tmp_path)

    assert loaded is None
    assert error == "刷新凭证状态文件格式无效"


def test_load_missing_state_is_not_an_error(tmp_path: Path) -> None:
    assert authstate.load(tmp_path) == (None, None)
