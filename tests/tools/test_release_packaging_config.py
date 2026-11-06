"""Unit tests for standalone build, packaging, and CI release configuration.

Workorder: G10-PACKAGING-V1-RELEASE-PREP-R1
Scope: Verify build_standalone.py, resolveflow.spec, package_deb.sh, and release.yml
invariants for v1.0.0 release packaging readiness.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

import build_standalone


def test_build_standalone_shebang() -> None:
    """build_standalone.py line 1 must start with #!/usr/bin/env python3."""
    with open("build_standalone.py", "r", encoding="utf-8") as f:
        first_line = f.readline()
    assert first_line.startswith("#!/usr/bin/env python3"), (
        f"Expected shebang #!/usr/bin/env python3, got: {first_line!r}"
    )


def test_hidden_imports_resolvability_and_no_dead_modules() -> None:
    """Hidden imports must exclude dead modules, include third-party essentials, and resolve 100% of src.*."""
    hidden_imports = build_standalone.get_hidden_imports()

    # 1. Obsolete non-existent modules must be strictly absent
    assert "src.ui.app" not in hidden_imports, "Dead module 'src.ui.app' found in hidden imports!"
    assert "src.ui.components.mini_timeline" not in hidden_imports, (
        "Dead module 'src.ui.components.mini_timeline' found in hidden imports!"
    )

    # 2. Required third-party runtime packages must be present
    required_third_party = [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtSvg",
        "PySide6.QtMultimedia",
        "PySide6.QtNetwork",
        "faster_whisper",
        "ctranslate2",
        "soundfile",
        "numpy",
        "cv2",
        "PIL",
        "pydantic",
        "cryptography",
    ]
    for pkg in required_third_party:
        assert pkg in hidden_imports, f"Required third-party runtime package '{pkg}' missing from hidden imports!"

    # 3. Every src.* module in get_hidden_imports() must be resolvable via importlib.util.find_spec
    src_modules = [m for m in hidden_imports if m.startswith("src.")]
    assert len(src_modules) > 100, f"Expected >100 internal src modules, got {len(src_modules)}"

    missing_modules: List[str] = []
    for mod in src_modules:
        spec = importlib.util.find_spec(mod)
        if spec is None:
            missing_modules.append(mod)

    assert not missing_modules, (
        f"The following {len(missing_modules)} modules failed importlib.util.find_spec: {missing_modules}"
    )


def test_build_standalone_cli_dry_run() -> None:
    """Running python build_standalone.py --dry-run via subprocess must exit 0."""
    res = subprocess.run(
        [sys.executable, "build_standalone.py", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"build_standalone.py --dry-run failed with code {res.returncode}:\nStdout: {res.stdout}\nStderr: {res.stderr}"
    )
    assert "[DRY-RUN SUCCESS]" in res.stdout


def test_build_standalone_cli_verify_imports() -> None:
    """Running python build_standalone.py --verify-imports via subprocess must exit 0."""
    res = subprocess.run(
        [sys.executable, "build_standalone.py", "--verify-imports"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"build_standalone.py --verify-imports failed with code {res.returncode}:\nStdout: {res.stdout}\nStderr: {res.stderr}"
    )
    assert "100%" in res.stdout
    assert "verified in repository" in res.stdout


def test_resolveflow_spec_syntax_and_no_dead_modules() -> None:
    """resolveflow.spec must compile as valid Python syntax, guard data dirs, and contain no dead modules."""
    with open("resolveflow.spec", "r", encoding="utf-8") as f:
        spec_text = f.read()

    # Valid Python compilation check
    compiled = compile(spec_text, "resolveflow.spec", "exec")
    assert compiled is not None

    # No dead modules
    assert "src.ui.app" not in spec_text, "Dead module 'src.ui.app' found in resolveflow.spec!"
    assert "src.ui.components.mini_timeline" not in spec_text, (
        "Dead module 'src.ui.components.mini_timeline' found in resolveflow.spec!"
    )

    # Guard with os.path.exists
    assert "os.path.exists" in spec_text, "Data directory os.path.exists guard missing in resolveflow.spec!"


def test_package_deb_syntax_and_checksum_reference() -> None:
    """package_deb.sh must pass bash -n, preserve version default, and generate SHA256SUMS."""
    # Syntax check
    res = subprocess.run(["bash", "-n", "package_deb.sh"], capture_output=True, text=True)
    assert res.returncode == 0, f"bash -n package_deb.sh failed:\n{res.stderr}"

    with open("package_deb.sh", "r", encoding="utf-8") as f:
        script_text = f.read()

    assert 'VERSION=${1:-"1.0.0"}' in script_text or 'VERSION="${1:-1.0.0}"' in script_text, (
        "VERSION default '1.0.0' not preserved in package_deb.sh"
    )
    assert "SHA256SUMS" in script_text, "SHA256SUMS reference missing from package_deb.sh!"
    assert "dist/" in script_text, "dist/ target directory missing from package_deb.sh!"


def test_github_workflow_release_config() -> None:
    """release.yml must use Python 3.12, run pre-build verifications, and publish SHA256SUMS with action-gh-release@v1."""
    workflow_path = Path(".github/workflows/release.yml")
    assert workflow_path.is_file(), "release.yml workflow file missing!"

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow_text = f.read()

    # Python 3.12 enforcement
    assert 'python-version: "3.12"' in workflow_text, "Expected python-version: '3.12' in release.yml"
    assert 'python-version: "3.11"' not in workflow_text, "Found legacy python-version: '3.11' in release.yml!"

    # Job IDs
    assert "build-windows:" in workflow_text, "Job 'build-windows' missing in release.yml"
    assert "build-linux:" in workflow_text, "Job 'build-linux' missing in release.yml"

    # Pre-build verification steps
    assert "build_standalone.py --dry-run" in workflow_text, "Missing --dry-run in release.yml"
    assert "build_standalone.py --verify-imports" in workflow_text, "Missing --verify-imports in release.yml"

    # SHA256SUMS and action-gh-release@v1
    assert "SHA256SUMS" in workflow_text, "SHA256SUMS missing from release.yml"
    assert "softprops/action-gh-release@v1" in workflow_text, "Expected softprops/action-gh-release@v1 in release.yml"


def test_get_pyinstaller_args_and_resource_specs_guard() -> None:
    """get_resource_specs and get_pyinstaller_args must only include existing directories."""
    resources = build_standalone.get_resource_specs()
    for src, dst in resources:
        assert os.path.exists(src), f"Resource '{src}' returned by get_resource_specs() does not exist!"

    # Verify that non-existent 'resources' folder is not included
    assert not any(src == "resources" for src, _ in resources), (
        "Non-existent 'resources' directory must not be in active resource specs!"
    )

    args = build_standalone.get_pyinstaller_args()
    assert "--name" in args
    assert "ResolveFlow-Assistant" in args
    assert "--hidden-import" in args
    assert "--exclude-module" in args
