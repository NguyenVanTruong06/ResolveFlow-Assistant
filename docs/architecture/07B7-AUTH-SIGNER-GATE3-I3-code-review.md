# Static Code Review — G07-AUTH-SIGNER-GATE3-I3 Revision R1

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the active workorder, completion report, signer implementation, focused tests, dependency manifest, submitted pytest log, and bridge handoff. Submitted SHA-256 values match the workspace:

- `.bridge/task.md`: `33d9b45ab1892e5625508f0b4797e00ad4d11a98811d9f32d6be51f2e4fad73c`
- `.bridge/report.md`: `86840b7dcf5dc6df7a4e5d64aa80b35714a5838f53e8dcab8e9bad79e372b5e7`
- `src/services/director_approval_signer.py`: `02f375adbed9f700291f99520bfadcf8b5c6b77ca4340db3b0807e10d71c84f3`
- `tests/services/test_director_approval_signer.py`: `aed06efddcac0f9f6ca670e6970feb2b76fec1b37172c0768f7e57508fda74bd`
- `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I3/pytest.log`: `b0e2ce65e6f4dedcce4da49f12dcd5d45c510de210a7ccf07ee17addb274d784`

The submitted log records **93 passed, 0 failed in 0.55s, exit code 0**. This was a static review: tests were not rerun.

The implementation follows the accepted envelope contract for the claims it signs: it uses the shared canonical JSON helper, excludes `director_signature` from the signing payload, checks that the resulting envelope exposes the same signing bytes, and returns the frozen DTO. The private key is not included in the envelope or the custom string representation. This review does not treat the module as an isolated authorization boundary or as proof that an approval was legitimately obtained.

## Finding

### I3-01 — Ed25519 provider is present only in the virtual environment, not declared by the repository (HIGH)

`src/services/director_approval_signer.py:24-27` imports `cryptography.hazmat.primitives.asymmetric.ed25519` at module import time, and the tests import the same package. The workorder §3 requires an Ed25519 provider already present in the repository's **declared dependency set**, and expressly forbids adding a dependency in this slice. The completion report establishes only that `cryptography==50.0.2` happens to be installed in the current `.venv`. The repository's dependency manifest, `requirements.txt`, does not declare `cryptography`; no other project dependency manifest declaring it was found.

Consequently, a clean environment installed from the repository's declared dependencies cannot reliably import this module, and the submitted environment provenance does not satisfy the workorder's provider prerequisite. The passing local tests do not resolve that reproducibility gap.

**Required resolution:** stop this slice pending an approved dependency decision. Either use an Ed25519 provider already declared by the project, or obtain a separate workorder that explicitly allows the dependency manifest change and dependency approval. Do not silently add the package, vendor cryptographic code, or treat the local virtual environment as the project dependency contract. Refresh the relevant evidence after the approved resolution.

## Decision and safety boundary

Do not accept Gate 3-I3 Revision R1 until `I3-01` is resolved under an approved dependency scope. This review does not authorize Director key provisioning, persistent key access, subsequent Signer/Dispatcher slices, Gate 4/5, or live operations.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only. No tests were run and no Resolve, IPC, key, network, or AI operation was performed during review.
