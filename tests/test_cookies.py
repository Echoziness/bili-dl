"""Tests for cookie extraction — the privacy-sensitive core logic.

Asserts that:
* only ``bilibili`` lines are kept (other-site secrets dropped);
* dot-prefixed domains get their Netscape column-2 fixed to TRUE;
* the written file has SESSDATA extractable by ``_extract_sessdata``.
"""

from __future__ import annotations

from pathlib import Path

from bili_dl import cookies

DATA = Path(__file__).parent / "data" / "sample_cookies_all.txt"


def test_extract_drops_other_sites(tmp_path: Path) -> None:
    src = tmp_path / cookies.ALL_COOKIE_FILENAME
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    assert cookies.import_bili_cookie_from_all(tmp_path) is True

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
    src = tmp_path / cookies.ALL_COOKIE_FILENAME
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")

    cookies.import_bili_cookie_from_all(tmp_path)
    content = cookies.bili_cookie_path(tmp_path).read_text(encoding="utf-8")

    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0].startswith("."):
            assert fields[1] == "TRUE", f"dot-domain not fixed: {line!r}"


def test_extract_sessdata(tmp_path: Path) -> None:
    src = tmp_path / cookies.ALL_COOKIE_FILENAME
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cookies.import_bili_cookie_from_all(tmp_path)

    lines = cookies._read_lines(cookies.bili_cookie_path(tmp_path))
    sess = cookies._extract_sessdata(lines)
    assert sess is not None
    assert "abc" in sess


def test_import_missing_file(tmp_path: Path) -> None:
    assert cookies.import_bili_cookie_from_all(tmp_path) is False


def test_import_no_bilibili_lines(tmp_path: Path) -> None:
    src = tmp_path / cookies.ALL_COOKIE_FILENAME
    src.write_text(
        "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n",
        encoding="utf-8",
    )
    assert cookies.import_bili_cookie_from_all(tmp_path) is False


def test_backup_created_on_overwrite(tmp_path: Path) -> None:
    dst = cookies.bili_cookie_path(tmp_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("# old\n.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tOLD\n", encoding="utf-8")

    src = tmp_path / cookies.ALL_COOKIE_FILENAME
    src.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    cookies.import_bili_cookie_from_all(tmp_path)

    backups = list(tmp_path.glob("cookies_bilibili.txt.bak_*"))
    assert len(backups) == 1
    assert "OLD" in backups[0].read_text(encoding="utf-8")
