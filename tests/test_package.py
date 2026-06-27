"""Smoke test that importing the package and resolving the CLI entry succeed.

Doesn't invoke any network. Guards against package-layout / metadata regressions.
"""

from __future__ import annotations

import importlib

import bili_dl


def test_version() -> None:
    assert bili_dl.__version__ == "0.1.0"


def test_cli_module_importable() -> None:
    cli = importlib.import_module("bili_dl.cli")
    assert hasattr(cli, "main")
    assert callable(cli.main)


def test_main_module_present() -> None:
    importlib.import_module("bili_dl.__main__")
