# Gate 3-I13 Revision R2 Static Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the current I13 R2 workorder, source, tests, completion report, and submitted focused test log. Submitted SHA-256 values match the workspace files:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `31c75bbe74b6842a392647c3c665c5051ee151e4bbb93596c370be535613e74f` | Match |
| `.bridge/report.md` | `82f7fa6153938f2dbc3d09d1525e4fd8de050022bb11c5aec643ca1ed1191bce` | Match |
| `src/services/trial_authorization_signer_service.py` | `64a6efe63ac316a065f810fa55fc034d77fe1a5e889da08047c7d615fb962199` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `4bc7b11fde9a7153ede708719231ad1597b65b61f2785a1dd3f25f8d3567ee06` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I13/pytest.log` | `21ce70dd16ac5e692bfca7146fd4510ae4831e00ed1eef34907cba16709eabc3` | Match |

The submitted log reports **246 passed, 2 warnings in 2.64s**, exit code 0. Tests were not rerun; this was a static review. No Resolve, IPC, network, or live operation was performed.

## Finding

### I13-R2-01 — BLOCKER: Exported module sentinel still permits caller-created key handles

`src/services/trial_authorization_signer_service.py:3698` defines `_KEY_HANDLE_CREATION_TOKEN` as a module attribute. `LocalTestSignerKeyHandle.__init__` at lines 3715–3718 accepts any caller-supplied key bytes whenever `_token` is that object. Any importer can read the supposedly private token and bypass the descriptor-anchored loader:

```python
import src.services.trial_authorization_signer_service as signer
handle = signer.LocalTestSignerKeyHandle(
    attacker_chosen_32_bytes,
    _token=signer._KEY_HANDLE_CREATION_TOKEN,
)
```

The resulting handle verifies against caller-chosen key material. This contradicts the workorder's explicit ban on caller-supplied key bytes and the R2 claim that direct construction is prohibited. The test at `tests/services/test_trial_authorization_signer_service.py:5835-5841` covers only omitted and invalid tokens, so it misses the valid-token path.

**Required correction:** remove the caller-selectable token path. Make handle creation originate only inside the validated loader through a mechanism that does not expose a reusable construction token in the module namespace, and add a regression test attempting direct construction with any discoverable module-level state. Keep the documented limitation that arbitrary same-process Python introspection is not an OS security boundary; the correction must still prevent this straightforward public-module API bypass.

## Closed R1 findings

- **I13-01 (direct construction / key slot):** The instance no longer stores `_key_bytes`, and attribute assignment is blocked. The attempted remediation is incomplete because the module token remains available to callers; see I13-R2-01.
- **I13-02 (exact mode):** `stat.S_IMODE(st_before.st_mode) == 0o600` now rejects special permission bits; the submitted tests cover the listed special-bit variants.
- **I13-03 (report wording):** The report now correctly distinguishes zero production keystore access from ephemeral fixture-key access.

## Disposition

Fix I13-R2-01 within the existing two-file allowlist and resubmit with the added regression test and updated evidence. Keep Gate 3 incomplete, `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. This review authorizes no challenge issuance, authorization issuance, production key access, OS service wiring, Gate 4–7, or live operations.
