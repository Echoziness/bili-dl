"""Smoke test that importing the package and resolving the CLI entry succeed.

Doesn't invoke any network. Guards against package-layout / metadata regressions.
"""

from __future__ import annotations

import importlib

import bili_dl


def test_version() -> None:
    assert isinstance(bili_dl.__version__, str)
    parts = bili_dl.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_cli_module_importable() -> None:
    cli = importlib.import_module("bili_dl.cli")
    assert hasattr(cli, "main")
    assert callable(cli.main)


def test_main_module_present() -> None:
    importlib.import_module("bili_dl.__main__")
