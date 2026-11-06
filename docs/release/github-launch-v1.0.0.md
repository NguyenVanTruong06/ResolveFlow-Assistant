# ChunDVC v1.0.0 GitHub Launch Guide

This runbook guides repository maintainers through the step-by-step release process for **ChunDVC (ResolveFlow Assistant) v1.0.0**, including git tagging, local artifact verification, Debian package build, `SHA256SUMS` generation, and GitHub Release asset publication.

---

## 📋 Release Checklist

- [ ] All working directories are clean and untracked artifacts removed.
- [ ] 100% of internal `src.*` modules pass import resolution (`python build_standalone.py --verify-imports`).
- [ ] Headless UI acceptance test suite passes 100% (`QT_QPA_PLATFORM=offscreen pytest tests/ui/`).
- [ ] Packaging test suite passes 100% (`pytest tests/tools/`).
- [ ] Version numbers aligned across `build_standalone.py` (`1.0.0`), `package_deb.sh` (`1.0.0`), and documentation.

---

## 🚀 Step-by-Step Release Instructions

### Step 1: Pre-Release Verification

Run the automated import resolution and dry-run packaging checks:

```bash
# 1. Activate development environment
source .venv/bin/activate

# 2. Verify all 237 internal modules resolve cleanly
python build_standalone.py --verify-imports

# 3. Perform pre-build dry-run configuration check
python build_standalone.py --dry-run

# 4. Run test suites
QT_QPA_PLATFORM=offscreen pytest tests/ui/ tests/tools/ -v
```

Ensure all tests pass with zero failures.

---

### Step 2: Build Standalone Executables & Debian Package

On Linux (Ubuntu / Debian x86_64):

```bash
# 1. Package Debian (.deb) installer and standalone binary
./package_deb.sh 1.0.0

# 2. Package tarball for portable Linux distribution
tar -czf "dist/ResolveFlow-Assistant-v1.0.0-linux-x64.tar.gz" -C dist ResolveFlow-Assistant
```

The build artifacts will be located in the `dist/` directory:
- `dist/ResolveFlow-Assistant/` (Standalone directory)
- `dist/resolveflow-assistant_1.0.0_amd64.deb` (Debian package)
- `dist/ResolveFlow-Assistant-v1.0.0-linux-x64.tar.gz` (Portable Linux tarball)

On Windows x64 (via PowerShell):

```powershell
# Build standalone Windows bundle
python build_standalone.py

# Package into ZIP archive
Compress-Archive -Path dist/ResolveFlow-Assistant -DestinationPath "dist/ResolveFlow-Assistant-v1.0.0-windows-x64.zip" -Force
```

---

### Step 3: Generate & Verify SHA-256 Checksums

In the `dist/` directory, verify that all release assets have their cryptographic hashes recorded in `dist/SHA256SUMS`:

```bash
cd dist

# Generate unified SHA256SUMS for all distribution packages
sha256sum *.deb *.tar.gz *.zip 2>/dev/null > SHA256SUMS || sha256sum *.deb > SHA256SUMS

# Verify checksum integrity
sha256sum -c SHA256SUMS
cd ..
```

Example `dist/SHA256SUMS` output:
```text
<sha256_hash>  resolveflow-assistant_1.0.0_amd64.deb
<sha256_hash>  ResolveFlow-Assistant-v1.0.0-linux-x64.tar.gz
```

---

### Step 4: Create and Push Git Tag

Create an annotated git tag pointing to the release commit:

```bash
# Create annotated tag
git tag -a v1.0.0 -m "ChunDVC (ResolveFlow Assistant) v1.0.0 Public Release"

# Push tag to GitHub
git push origin v1.0.0
```

Pushing the `v1.0.0` tag triggers the automated GitHub Actions release workflow (`.github/workflows/release.yml`), which builds and publishes the release assets.

---

### Step 5: Publish GitHub Release

If publishing manually or updating release notes on GitHub:

1. Navigate to: `https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/new`
2. Select tag: `v1.0.0`
3. Release Title: `ChunDVC (ResolveFlow Assistant) — v1.0.0`
4. Description: Copy release notes from [CHANGELOG.md](../../CHANGELOG.md) under section `[1.0.0] - 2026-11-06`.
5. Attach the release binaries and checksum file:
   - `resolveflow-assistant_1.0.0_amd64.deb`
   - `ResolveFlow-Assistant-v1.0.0-linux-x64.tar.gz`
   - `ResolveFlow-Assistant-v1.0.0-windows-x64.zip`
   - `SHA256SUMS`
6. Click **Publish release**.

---

## 🔒 Post-Release Verification

On a clean Ubuntu / Debian test machine:

```bash
# 1. Download release assets
wget https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v1.0.0/resolveflow-assistant_1.0.0_amd64.deb
wget https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v1.0.0/SHA256SUMS

# 2. Check SHA-256 integrity
sha256sum -c SHA256SUMS

# 3. Install
sudo dpkg -i resolveflow-assistant_1.0.0_amd64.deb
sudo apt-get install -f

# 4. Launch
resolveflow-assistant
```
