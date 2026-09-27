"""Release metadata must be derived from the package version and changelog."""

import pytest
from scripts import release


def _changelog() -> str:
    return (
        "# Changelog\n\n"
        "## [Unreleased]\n\n### Added\n\n- A useful change.\n\n"
        "## [1.2.2] - 2026-01-01\n\n### Fixed\n\n- Earlier fix.\n\n"
        f"[Unreleased]: {release.REPO}/compare/v1.2.2...HEAD\n"
    )


def test_prepare_is_idempotent_and_notes_match_changelog() -> None:
    prepared = release.prepare(_changelog(), "1.2.3")

    assert release.prepare(prepared, "1.2.3") == prepared
    release.check(prepared, "1.2.3", "v1.2.3")
    assert release.notes(prepared, "1.2.3") == "### Added\n\n- A useful change.\n"
    assert f"[Unreleased]: {release.REPO}/compare/v1.2.3...HEAD" in prepared
    assert f"[1.2.3]: {release.REPO}/compare/v1.2.2...v1.2.3" in prepared


def test_prepare_rejects_empty_unreleased() -> None:
    with pytest.raises(ValueError, match="no changes"):
        release.prepare(_changelog().replace("- A useful change.", ""), "1.2.3")


def test_check_rejects_mismatched_tag() -> None:
    prepared = release.prepare(_changelog(), "1.2.3")
    with pytest.raises(ValueError, match="does not match"):
        release.check(prepared, "1.2.3", "v1.2.4")


def test_prepare_rejects_changes_added_after_preparation() -> None:
    prepared = release.prepare(_changelog(), "1.2.3")
    changed = prepared.replace("## [Unreleased]\n", "## [Unreleased]\n\n- Missed change.\n")
    with pytest.raises(ValueError, match="after this version was prepared"):
        release.prepare(changed, "1.2.3")
