"""Tests for TOML config file loading — settings.py.

Covers:
* missing file → empty Settings
* complete config → all fields populated
* partial config → only set fields populated, rest None
* empty proxy string → treated as None
* malformed TOML → TOMLDecodeError propagated to caller
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from bili_dl import settings
from bili_dl.settings import Settings


def test_load_missing_file(tmp_path: Path) -> None:
    result = settings.load(tmp_path / "nope.toml")
    assert result == Settings()


def test_load_complete(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_text(
        'mode = "a"\nproxy = "http://127.0.0.1:7890"\ninsecure = true\n'
        'video_dir = "/tmp/v"\naudio_dir = "/tmp/a"\ncookie_dir = "/tmp/c"\n',
        encoding="utf-8",
    )
    result = settings.load(f)
    assert result.mode == "a"
    assert result.proxy == "http://127.0.0.1:7890"
    assert result.insecure is True
    assert result.video_dir == Path("/tmp/v")
    assert result.audio_dir == Path("/tmp/a")
    assert result.cookie_dir == Path("/tmp/c")


def test_load_partial(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_text('mode = "v"\n', encoding="utf-8")
    result = settings.load(f)
    assert result.mode == "v"
    assert result.proxy is None
    assert result.insecure is None
    assert result.video_dir is None


def test_load_empty_proxy_preserved(tmp_path: Path) -> None:
    """proxy = "" in config means 'explicitly disable' — must not be
    coerced to None, which would fall through to env vars (§audit)."""
    f = tmp_path / "config.toml"
    f.write_text('proxy = ""\n', encoding="utf-8")
    result = settings.load(f)
    assert result.proxy == ""  # stays "" — caller can distinguish from unset


def test_load_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_text("", encoding="utf-8")
    result = settings.load(f)
    assert result == Settings()


def test_load_malformed_raises(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_text("mode = \n", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        settings.load(f)


def test_load_insecure_non_bool_coerced_to_none(tmp_path: Path) -> None:
    """A non-bool insecure value (e.g. "yes") is coerced to None so downstream
    ``cfg.insecure or False`` can't pick up a truthy string (§audit)."""
    f = tmp_path / "config.toml"
    f.write_text('insecure = "yes"\n', encoding="utf-8")
    result = settings.load(f)
    assert result.insecure is None


def test_load_insecure_int_coerced_to_none(tmp_path: Path) -> None:
    """An integer 1 is also not a bool → None (strict isinstance check)."""
    f = tmp_path / "config.toml"
    f.write_text("insecure = 1\n", encoding="utf-8")
    result = settings.load(f)
    assert result.insecure is None
