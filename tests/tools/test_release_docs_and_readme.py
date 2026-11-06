"""Unit tests for English documentation, release runbooks, and issue templates.

Workorder: G10-ENGLISH-README-AND-GITHUB-RELEASE-DOCS-R1
Scope: Verify README.md, CHANGELOG.md, CONTRIBUTING.md, Issue Templates,
docs/release/github-launch-v1.0.0.md, and ROADMAP.md alignment for v1.0.0 launch.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

try:
    import yaml  # type: ignore
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def test_readme_file_exists_and_content_requirements() -> None:
    """README.md must exist, be in English, reference v1.0.0, B2-M1, artifacts, and have no stale G07 status."""
    readme_path = Path("README.md")
    assert readme_path.is_file(), "README.md does not exist!"

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert len(content) > 1000, "README.md is unexpectedly short!"

    # Header and Versioning
    assert "ChunDVC (ResolveFlow Assistant) — v1.0.0" in content, "Missing title or v1.0.0 in README.md"
    assert "Release v1.0.0" in content, "Missing Release v1.0.0 badge in README.md"
    assert "Python 3.12+" in content, "Missing Python 3.12+ badge in README.md"
    assert "PySide6 (Qt6)" in content, "Missing PySide6 (Qt6) badge in README.md"
    assert "DaVinci Resolve 18 / 19+" in content, "Missing DaVinci Resolve badge in README.md"

    # Core Architecture Pillars
    assert "Three-Pillar NLE Obsidian Studio Architecture" in content
    assert "Luxury EdgeDock Overlay" in content
    assert "ChunDVC Studio Asset Hub" in content
    assert "4-Step AI Director" in content

    # Key Technology & Governance Terms
    assert "B2-M1" in content, "B2-M1 governance not referenced in README.md"
    assert "resolveflow-assistant_1.0.0_amd64.deb" in content, "Debian package name missing from README.md"
    assert "SHA256SUMS" in content, "SHA256SUMS missing from README.md"
    assert "--verify-imports" in content, "--verify-imports CLI missing from README.md"
    assert "run_gate6.py" in content, "run_gate6.py test canary missing from README.md"

    # Stale status check: G07 must NOT be marked 'In progress'
    for line in content.splitlines():
        if "G07" in line:
            assert "In progress" not in line, f"Stale 'In progress' found on G07 line: {line}"


def test_changelog_file_and_sections() -> None:
    """CHANGELOG.md must exist and document [1.0.0] - 2026-11-06 with required sections."""
    changelog_path = Path("CHANGELOG.md")
    assert changelog_path.is_file(), "CHANGELOG.md does not exist!"

    with open(changelog_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "[1.0.0] - 2026-11-06" in content, "Missing version header [1.0.0] - 2026-11-06"
    assert "### Added" in content, "Missing ### Added section in CHANGELOG.md"
    assert "### Changed" in content, "Missing ### Changed section in CHANGELOG.md"
    assert "### Fixed" in content, "Missing ### Fixed section in CHANGELOG.md"
    assert "### Security & Governance (B2-M1)" in content, (
        "Missing ### Security & Governance (B2-M1) section in CHANGELOG.md"
    )
    assert "B2-M1" in content


def test_contributing_file_and_sections() -> None:
    """CONTRIBUTING.md must provide comprehensive onboarding, test instructions, and PR checklist."""
    contrib_path = Path("CONTRIBUTING.md")
    assert contrib_path.is_file(), "CONTRIBUTING.md does not exist!"

    with open(contrib_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Python:" in content or "Python 3.12" in content
    assert "3.12" in content
    assert "QT_QPA_PLATFORM=offscreen" in content, "Missing headless Qt testing guide in CONTRIBUTING.md"
    assert "build_standalone.py --verify-imports" in content, "Missing --verify-imports command in CONTRIBUTING.md"
    assert "Pull Request Checklist" in content, "Missing PR checklist in CONTRIBUTING.md"


def test_github_issue_templates() -> None:
    """GitHub Issue Templates for bug_report and feature_request must exist and be valid YAML forms."""
    bug_path = Path(".github/ISSUE_TEMPLATE/bug_report.yml")
    feat_path = Path(".github/ISSUE_TEMPLATE/feature_request.yml")

    assert bug_path.is_file(), "bug_report.yml template missing!"
    assert feat_path.is_file(), "feature_request.yml template missing!"

    for p in (bug_path, feat_path):
        with open(p, "r", encoding="utf-8") as f:
            raw_text = f.read()
        assert "name:" in raw_text
        assert "description:" in raw_text
        assert "body:" in raw_text

        if HAS_YAML:
            data = yaml.safe_load(raw_text)
            assert isinstance(data, dict)
            assert "name" in data
            assert "description" in data
            assert "body" in data
            assert len(data["body"]) > 0

    # Check DaVinci Resolve specific field in bug report
    with open(bug_path, "r", encoding="utf-8") as f:
        bug_text = f.read()
    assert "DaVinci Resolve" in bug_text


def test_github_launch_v1_0_0_guide() -> None:
    """docs/release/github-launch-v1.0.0.md must exist and outline the release runbook."""
    guide_path = Path("docs/release/github-launch-v1.0.0.md")
    assert guide_path.is_file(), "docs/release/github-launch-v1.0.0.md does not exist!"

    with open(guide_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "v1.0.0" in content
    assert "SHA256SUMS" in content
    assert "package_deb.sh" in content
    assert "git tag -a v1.0.0" in content


def test_roadmap_gate_statuses() -> None:
    """docs/architecture/ROADMAP.md must reflect G07 (B2-M1), G08, and G10 completion."""
    roadmap_path = Path("docs/architecture/ROADMAP.md")
    assert roadmap_path.is_file(), "docs/architecture/ROADMAP.md does not exist!"

    with open(roadmap_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "B2-M1" in content, "B2-M1 missing from ROADMAP.md"
    assert "Gate G07 ĐÃ ĐÓNG" in content or "G07 |" in content
    assert "G08" in content
    assert "G10" in content
    assert "NLE Obsidian Studio UI Polish" in content
    assert "v1.0.0 Public Release Ready" in content
