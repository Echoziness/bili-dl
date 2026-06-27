"""Tests for cookie extraction — the privacy-sensitive core logic.

Asserts that:
* only ``bilibili`` lines are kept (other-site secrets dropped);
* dot-prefixed domains get their Netscape column-2 fixed to TRUE;
* the written file has SESSDATA extractable by ``_extract_sessdata``;
* auto-detection works with any .txt filename.
"""

from __future__ import annotations

from pathlib import Path

from bili_dl import cookies

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


def test_extract_drops_other_sites(tmp_path: Path) -> None:
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    assert cookies.import_bili_cookie(tmp_path) is True

    out = cookies.bili_cookie_path(tmp_path)
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

    cookies.import_bili_cookie(tmp_path)
    content = cookies.bili_cookie_path(tmp_path).read_text(encoding="utf-8")

    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0].startswith("."):
            assert fields[1] == "TRUE", f"dot-domain not fixed: {line!r}"


def test_extract_sessdata(tmp_path: Path) -> None:
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cookies.import_bili_cookie(tmp_path)

    lines = cookies._read_lines(cookies.bili_cookie_path(tmp_path))
    sess = cookies._extract_sessdata(lines)
    assert sess is not None
    assert "abc" in sess


def test_import_empty_dir(tmp_path: Path) -> None:
    assert cookies.import_bili_cookie(tmp_path) is False


def test_import_no_bilibili_lines(tmp_path: Path) -> None:
    src = tmp_path / "other_cookies.txt"
    src.write_text(
        "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n",
        encoding="utf-8",
    )
    assert cookies.import_bili_cookie(tmp_path) is False


def test_any_txt_filename_detected(tmp_path: Path) -> None:
    """Auto-detection works regardless of source filename."""
    src = tmp_path / "random_name.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    assert cookies.import_bili_cookie(tmp_path) is True
    assert cookies.bili_cookie_path(tmp_path).exists()


def test_backup_created_on_overwrite(tmp_path: Path) -> None:
    dst = cookies.bili_cookie_path(tmp_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("# old\n.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tOLD\n", encoding="utf-8")

    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cookies.import_bili_cookie(tmp_path)

    backups = list(tmp_path.glob("cookies_bilibili.txt.bak_*"))
    assert len(backups) == 1
    assert "OLD" in backups[0].read_text(encoding="utf-8")


def test_httponly_prefix_kept_and_stripped(tmp_path: Path) -> None:
    """#HttpOnly_ lines must be treated as cookie data, not comments."""
    src = tmp_path / "cookies_export.txt"
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cookies.import_bili_cookie(tmp_path)

    content = cookies.bili_cookie_path(tmp_path).read_text(encoding="utf-8")
    assert "Cookie_HttpOnly" in content
    assert "this_should_be_kept" in content
    # prefix must be stripped from output
    assert "#HttpOnly_" not in content


def test_bili_output_ignored(tmp_path: Path) -> None:
    """cookies_bilibili.txt itself is never used as a source."""
    # Only the output file exists — no source to import from.
    out = cookies.bili_cookie_path(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tOLD\n", encoding="utf-8")
    assert cookies.import_bili_cookie(tmp_path) is False
