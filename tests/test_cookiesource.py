"""Tests for cookie source detection and import — the privacy-sensitive core.

Asserts that:
* only ``bilibili`` lines are kept (other-site secrets dropped);
* dot-prefixed domains get their Netscape column-2 fixed to TRUE;
* the written file has SESSDATA extractable;
* auto-detection works with any .txt filename;
* ``#HttpOnly_`` lines are treated as cookie data, not comments;
* the output file is never used as a source;
* existing output is backed up before overwrite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bili_dl import cookiesource as cs
from bili_dl.cookiestore import bili_cookie_path

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


def test_extract_drops_other_sites(tmp_path: Path) -> None:
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    result = cs.import_cookie(tmp_path)
    assert result.success is True

    out = bili_cookie_path(tmp_path)
    assert out.exists()
    content = out.read_text(encoding="utf-8")

    assert "SESSDATA" in content
    assert "bili_jct" in content
    assert "DedeUserID" in content
    # other-site secrets must not leak
    assert "other_secret" not in content
    assert "example.org" not in content
    assert "SHOULD_BE_DROPPED" not in content


def test_dot_domain_fixed_to_true(tmp_path: Path) -> None:
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    cs.import_cookie(tmp_path)
    content = bili_cookie_path(tmp_path).read_text(encoding="utf-8")

    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0].startswith("."):
            assert fields[1] == "TRUE", f"dot-domain not fixed: {line!r}"


def test_extract_sessdata_after_import(tmp_path: Path) -> None:
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cs.import_cookie(tmp_path)

    from bili_dl.cookiestore import _extract_sessdata

    lines = cs.read_lines(bili_cookie_path(tmp_path))
    sess = _extract_sessdata(lines)
    assert sess is not None
    assert "abc" in sess


def test_import_empty_dir(tmp_path: Path) -> None:
    result = cs.import_cookie(tmp_path)
    assert result.success is False


def test_import_no_bilibili_lines(tmp_path: Path) -> None:
    src = tmp_path / "other_cookies.txt"
    src.write_text(
        "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n",
        encoding="utf-8",
    )
    result = cs.import_cookie(tmp_path)
    assert result.success is False


def test_any_txt_filename_detected(tmp_path: Path) -> None:
    """Auto-detection works regardless of source filename."""
    src = tmp_path / "random_name.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    result = cs.import_cookie(tmp_path)
    assert result.success is True
    assert bili_cookie_path(tmp_path).exists()


def test_backup_created_on_overwrite(tmp_path: Path) -> None:
    dst = bili_cookie_path(tmp_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("# old\n.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tOLD\n", encoding="utf-8")

    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cs.import_cookie(tmp_path)

    backups = list(tmp_path.glob("cookies_bilibili.txt.bak_*"))
    assert len(backups) == 1
    assert "OLD" in backups[0].read_text(encoding="utf-8")


def test_httponly_prefix_kept_and_stripped(tmp_path: Path) -> None:
    """#HttpOnly_ lines must be treated as cookie data, not comments."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cs.import_cookie(tmp_path)

    content = bili_cookie_path(tmp_path).read_text(encoding="utf-8")
    assert "Cookie_HttpOnly" in content
    assert "this_should_be_kept" in content
    # prefix must be stripped from output
    assert "#HttpOnly_" not in content


def test_bili_output_ignored(tmp_path: Path) -> None:
    """cookies_bilibili.txt itself is never used as a source."""
    out = bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tOLD\n", encoding="utf-8")
    result = cs.import_cookie(tmp_path)
    assert result.success is False


def test_www_bilibili_kept(tmp_path: Path) -> None:
    """www.bilibili.com entries are bilibili-domain and should be kept."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cs.import_cookie(tmp_path)

    content = bili_cookie_path(tmp_path).read_text(encoding="utf-8")
    assert "SessionOnly" in content
    assert "www.bilibili.com" in content


# ─── find_source / read_lines / _candidates edge cases ───────────────────────


def test_find_source_returns_path(tmp_path: Path) -> None:
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    assert cs.find_source(tmp_path) == src


def test_find_source_dir_missing(tmp_path: Path) -> None:
    """A non-existent cookie directory yields None, not an exception."""
    assert cs.find_source(tmp_path / "nope") is None


def test_find_source_no_bili(tmp_path: Path) -> None:
    """A directory with only other-site cookies yields None."""
    src = tmp_path / "other.txt"
    src.write_text(".example.com\tTRUE\t/\tFALSE\t0\tfoo\tbar\n", encoding="utf-8")
    assert cs.find_source(tmp_path) is None


def test_read_lines_oserror_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I/O errors on read degrade to [] rather than crashing (§2.20)."""
    f = tmp_path / "x.txt"
    f.write_text("ok\n", encoding="utf-8")

    def bad_read(self: Path, *a: object, **kw: object) -> str:
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", bad_read)
    assert cs.read_lines(f) == []


# ─── import_cookie: multi-candidate / write-failure branches ─────────────────


def test_import_multiple_candidates(tmp_path: Path) -> None:
    """When several .txt files exist, the count is reported in the info message."""
    src1 = tmp_path / "a.txt"
    src1.write_text(".example.com\tTRUE\t/\tFALSE\t0\tfoo\tbar\n", encoding="utf-8")
    src2 = tmp_path / "b.txt"
    src2.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    result = cs.import_cookie(tmp_path)
    assert result.success is True
    assert any("2 个" in t for _, t in result.messages)


def test_import_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError writing the output file → failure with reason (§2.20)."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    def bad_write(self: Path, *a: object, **kw: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", bad_write)
    result = cs.import_cookie(tmp_path)
    assert result.success is False
    assert any("无法写入" in t for _, t in result.messages)


# ─── is_bili_line / _strip_httponly unit tests ───────────────────────────────


def test_is_bili_line_true() -> None:
    assert cs.is_bili_line(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tx") is True


def test_is_bili_line_comment_false() -> None:
    assert cs.is_bili_line("# Netscape HTTP Cookie File") is False


def test_is_bili_line_other_site_false() -> None:
    assert cs.is_bili_line(".example.com\tTRUE\t/\tFALSE\t0\tfoo\tbar") is False


def test_strip_httponly_prefix() -> None:
    assert cs._strip_httponly("#HttpOnly_.bilibili.com") == ".bilibili.com"
    assert cs._strip_httponly(".bilibili.com") == ".bilibili.com"
