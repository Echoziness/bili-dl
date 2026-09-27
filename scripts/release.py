"""Prepare and check the changelog used by tag-triggered releases."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "src/bili_dl/__init__.py"
REPO = "https://github.com/Echoziness/bili-dl"


def version() -> str:
    source = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)"$', source, re.MULTILINE)
    if match is None:
        raise ValueError("Cannot read the package version from __init__.py")
    return match.group(1)


def sections(changelog: str) -> list[re.Match[str]]:
    return list(re.finditer(r"(?m)^## \[([^]]+)\](?: - (\d{4}-\d{2}-\d{2}))?[ \t]*$", changelog))


def notes(changelog: str, release_version: str) -> str:
    headings = sections(changelog)
    matches = [i for i, heading in enumerate(headings) if heading.group(1) == release_version]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one CHANGELOG section for {release_version}")
    index = matches[0]
    end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
    body = changelog[headings[index].end() : end].strip()
    if not body or not re.search(r"(?m)^- ", body):
        raise ValueError(f"CHANGELOG section for {release_version} has no changes")
    return body + "\n"


def check(changelog: str, release_version: str, tag: str | None = None) -> None:
    if tag is not None and tag != f"v{release_version}":
        raise ValueError(f"Tag {tag} does not match package version {release_version}")
    headings = sections(changelog)
    if len(headings) < 3 or headings[0].group(1) != "Unreleased":
        raise ValueError("CHANGELOG must start with Unreleased and contain two releases")
    if headings[1].group(1) != release_version:
        raise ValueError(f"Latest CHANGELOG section must be {release_version}")
    previous = headings[2].group(1)
    notes(changelog, release_version)
    expected = (
        f"[Unreleased]: {REPO}/compare/v{release_version}...HEAD",
        f"[{release_version}]: {REPO}/compare/v{previous}...v{release_version}",
    )
    for line in expected:
        if changelog.splitlines().count(line) != 1:
            raise ValueError(f"Missing or duplicate CHANGELOG link: {line}")


def prepare(changelog: str, release_version: str) -> str:
    headings = sections(changelog)
    if len(headings) < 2 or headings[0].group(1) != "Unreleased":
        raise ValueError("CHANGELOG must start with Unreleased and an earlier release")
    if headings[1].group(1) == release_version:
        if changelog[headings[0].end() : headings[1].start()].strip():
            raise ValueError("Unreleased has changes after this version was prepared")
        check(changelog, release_version)
        return changelog
    if any(heading.group(1) == release_version for heading in headings):
        raise ValueError(f"Version {release_version} already appears in CHANGELOG")
    previous = headings[1].group(1)
    pending = changelog[headings[0].end() : headings[1].start()].strip()
    if not pending or not re.search(r"(?m)^- ", pending):
        raise ValueError("Unreleased section has no changes")
    old_link = f"[Unreleased]: {REPO}/compare/v{previous}...HEAD"
    if changelog.splitlines().count(old_link) != 1:
        raise ValueError(f"Expected one current Unreleased link: {old_link}")
    new_section = f"\n\n## [{release_version}] - {date.today().isoformat()}\n\n{pending}\n\n"
    result = changelog[: headings[0].end()] + new_section + changelog[headings[1].start() :]
    new_links = (
        f"[Unreleased]: {REPO}/compare/v{release_version}...HEAD\n"
        f"[{release_version}]: {REPO}/compare/v{previous}...v{release_version}"
    )
    result = result.replace(old_link, new_links, 1)
    check(result, release_version)
    return result


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"prepare", "check", "notes"}:
        raise SystemExit("Usage: python scripts/release.py prepare|check [tag]|notes tag FILE")
    command = sys.argv[1]
    release_version = version()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    try:
        if command == "prepare" and len(sys.argv) == 2:
            result = prepare(changelog, release_version)
            if result != changelog:
                CHANGELOG.write_text(result, encoding="utf-8", newline="\n")
            print(f"CHANGELOG ready for v{release_version}")
        elif command == "check" and len(sys.argv) in {2, 3}:
            check(changelog, release_version, sys.argv[2] if len(sys.argv) == 3 else None)
            print(f"Release metadata matches v{release_version}")
        elif command == "notes" and len(sys.argv) == 4:
            check(changelog, release_version, sys.argv[2])
            Path(sys.argv[3]).write_text(notes(changelog, release_version), encoding="utf-8")
        else:
            raise SystemExit("Usage: python scripts/release.py prepare|check [tag]|notes tag FILE")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
