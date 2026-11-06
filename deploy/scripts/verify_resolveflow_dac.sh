#!/usr/bin/env bash
# ==============================================================================
# ResolveFlow DAC & Principal Verification Script (Revision R9)
# Workorder: G07-07B7-GATE6-PHASE2-BLOCKER-D-ADMISSION-LEDGER-PACKAGING-ALIGN-R1
# Prior Workorder: G07-07B7-GATE6-PHASE2-BLOCKER-D-PACKAGING-REALIGN-R1 (Revision R8)
# Reference: docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md
# Admission Ledger Reference: docs/architecture/07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-SERVER-DESIGN-R1.md (§4.2)
# Review Reference: docs/architecture/07B7-GATE6-DEP02A-R6-code-review.md
#
# SAFETY GOVERNANCE:
# - STRICTLY READ-ONLY: Never creates, mutates, re-owns, or deletes any file or state.
# - STRUCTURED OUTCOMES:
#     STATUS=VERIFIED_OK       (Exit 0: All principals, DAC modes, and boundaries valid)
#     STATUS=NOT_PROVISIONED   (Exit 2: Host has not been provisioned yet)
#     STATUS=FAILED_MISMATCH   (Exit 1: Boundary integrity violation detected)
#
# REMEDIATION DETAILS (Revision R9):
# - BLOCKER-D-ADMISSION-LEDGER-PACKAGING-ALIGN-R1:
#   * Artifact-only metadata allowlist alignment: adds 'consumed_admissions.jsonl'
#     (accepted unified compound admission ledger basename from Admission Server Design R5 §4.2
#     and DurableAdmissionLedger R3) to PERMITTED_DISPATCHER_FILES under /var/lib/resolveflow/dispatcher.
#   * Routes 'consumed_admissions.jsonl' through existing generic descriptor-relative regular-file
#     metadata checks (regular file, not symlink, link count 1, UID:GID 902:902, mode 0600).
#   * File remains optional for metadata inventory purposes (empty initial Dispatcher directory
#     remains valid). Does not create the ledger, inspect its contents, or imply host presence or
#     operational listener/admission readiness.
#   * Retains legacy basenames ('consumed_preflight_runs.jsonl', 'consumed_execution_workorders.jsonl')
#     solely for metadata inventory compatibility without authorizing dual-ledger fallback, merge, or migration.
# - BLOCKER-D-PACKAGING-REALIGN-R1 (Revision R8):
#   * Aligns /var/lib/resolveflow exact direct-child policy to {'signer', 'dispatcher'}.
#   * Verifies Dispatcher durable state directory (/var/lib/resolveflow/dispatcher) descriptor-relatively:
#     owner 902:902, mode 0700. Permitted direct regular files drawn strictly from DurableDispatchJournal
#     and DurableAdmissionLedger specifications ({'execution_journal.wal', 'execution.log',
#     'target_ledger.v2.json', 'receipt.json', 'consumed_preflight_runs.jsonl',
#     'consumed_execution_workorders.jsonl', 'consumed_admissions.jsonl'}, mode 0600, link count 1, UID:GID 902:902).
#     Permitted direct subdirectory strictly .locks/ (mode 0700, UID:GID 902:902), containing .lock and .in_doubt
#     nodes (mode 0600, link count 1, UID:GID 902:902). Empty directory accepted as valid initial setup state.
#   * Adds descriptor-anchored verification for Class A trust-root directory /etc/resolveflow (owner root:root, mode 0755).
#     Verifies known public keys (oracle_public_key.pem, preflight_verifier_public_key.pem, tech_lead_public_key.pem)
#     as mode 0644 root:root (nlink 1, regular file, not symlink) without reading content. Missing keys reported as
#     absent/unprovisioned. Fails closed on revoked_keys.jsonl (unresolved packaging prerequisite) or unreviewed entries.
#   * Reconciles legacy signer anchors directory: removes verification of /var/lib/resolveflow/signer/anchors and updates
#     signer direct children to {'keystore', 'nonces'}.
# - DEP02A-R6-01: Uses descriptor-relative os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
#   for present socket node inspection, ensuring standard Python library compatibility.
# - DEP02A-R5-01: Validates socket directories allow either empty set (pre-daemon
#   provisioning state) or exact single expected socket basename. When present,
#   validates socket node type (ISSOCK), UID, GID, and mode (0660) descriptor-relatively
#   on the pinned parent directory descriptor using O_NOFOLLOW.
# - DEP02A-R4-01: Enforces exact permitted direct-child set for parent directories
#   (/var/lib/resolveflow -> {'signer', 'dispatcher'}; signer -> {'keystore', 'nonces'};
#   /run/resolveflow -> {'oracle', 'signer_client', 'dispatcher_client'}) and emptiness
#   for leaf state directories directly on pinned descriptors.
# - DEP02A-R3-01: Verifies directory metadata and contents strictly through the
#   same pinned directory descriptor (O_NOFOLLOW | O_DIRECTORY + fstat + os.scandir(fd)).
# - DEP02A-R3-02: Verifies daemon accounts possess zero supplementary group memberships.
# ==============================================================================
set -euo pipefail

SIGNER_UID=901
SIGNER_GID=901
SIGNER_USER="_resolveflow_signer"
SIGNER_GROUP="_resolveflow_signer"

DISPATCHER_UID=902
DISPATCHER_GID=902
DISPATCHER_USER="_resolveflow_dispatcher"
DISPATCHER_GROUP="_resolveflow_dispatcher"

CLIENTS_GID=903
CLIENTS_GROUP="resolveflow_clients"

RUNNER_USER="${RESOLVEFLOW_RUNNER_USER:-${SUDO_USER:-$(id -un)}}"

MISSING_ITEMS=()
MISMATCH_ITEMS=()

record_missing() {
    MISSING_ITEMS+=("$1")
}

record_mismatch() {
    MISMATCH_ITEMS+=("$1")
}

# ------------------------------------------------------------------------------
# 1. Verify Numeric Accounts, Groups & Least-Privilege Memberships
# ------------------------------------------------------------------------------
verify_group() {
    local target_name="$1"
    local target_gid="$2"

    if ! getent group "$target_name" > /dev/null 2>&1; then
        record_missing "Group '$target_name' does not exist"
        return
    fi

    local actual_gid
    actual_gid="$(getent group "$target_name" | cut -d: -f3)"
    if [ "$actual_gid" -ne "$target_gid" ]; then
        record_mismatch "Group '$target_name' has GID $actual_gid (expected $target_gid)"
    fi
}

verify_user() {
    local target_name="$1"
    local target_uid="$2"
    local target_gid="$3"

    if ! getent passwd "$target_name" > /dev/null 2>&1; then
        record_missing "User '$target_name' does not exist"
        return
    fi

    local actual_uid actual_user_gid
    actual_uid="$(getent passwd "$target_name" | cut -d: -f3)"
    actual_user_gid="$(getent passwd "$target_name" | cut -d: -f4)"

    if [ "$actual_uid" -ne "$target_uid" ]; then
        record_mismatch "User '$target_name' has UID $actual_uid (expected $target_uid)"
    fi
    if [ "$actual_user_gid" -ne "$target_gid" ]; then
        record_mismatch "User '$target_name' has primary GID $actual_user_gid (expected $target_gid)"
    fi

    # DEP02A-R3-02: Verify daemon account possesses zero supplementary groups
    local actual_gids
    actual_gids="$(id -G "$target_name" 2>/dev/null || echo "")"
    if [ "$actual_gids" != "$target_gid" ]; then
        record_mismatch "User '$target_name' has unauthorized supplementary groups '$actual_gids' (expected only primary GID $target_gid)"
    fi
}

verify_group "$SIGNER_GROUP" "$SIGNER_GID"
verify_group "$DISPATCHER_GROUP" "$DISPATCHER_GID"
verify_group "$CLIENTS_GROUP" "$CLIENTS_GID"

verify_user "$SIGNER_USER" "$SIGNER_UID" "$SIGNER_GID"
verify_user "$DISPATCHER_USER" "$DISPATCHER_UID" "$DISPATCHER_GID"

# Verify Runner supplementary group membership
if getent passwd "$RUNNER_USER" > /dev/null 2>&1; then
    if getent group "$CLIENTS_GROUP" > /dev/null 2>&1; then
        if ! id -nG "$RUNNER_USER" | grep -qw "$CLIENTS_GROUP"; then
            record_mismatch "Runner user '$RUNNER_USER' is not in supplementary group '$CLIENTS_GROUP'"
        fi
    fi
    if id -nG "$RUNNER_USER" | grep -qw "$SIGNER_GROUP"; then
        record_mismatch "Security violation: Runner user '$RUNNER_USER' belongs to '$SIGNER_GROUP'"
    fi
    if id -nG "$RUNNER_USER" | grep -qw "$DISPATCHER_GROUP"; then
        record_mismatch "Security violation: Runner user '$RUNNER_USER' belongs to '$DISPATCHER_GROUP'"
    fi
else
    record_missing "Designated runner user '$RUNNER_USER' does not exist"
fi

# ------------------------------------------------------------------------------
# 2. Descriptor-Anchored Ancestor, Directory DAC & Empty Content Verification
# Remediating DEP02A-R3-01: Metadata and empty contents are evaluated strictly
# through the open directory descriptor without path re-opening.
# ------------------------------------------------------------------------------
VERIFY_JSON="$(/usr/bin/python3 - << 'PY_VERIFY_EOF'
import os
import stat
import json
import sys

SIGNER_UID = 901
SIGNER_GID = 901
DISPATCHER_UID = 902
DISPATCHER_GID = 902
CLIENTS_GID = 903

missing = []
mismatch = []

def open_dir_nofollow(parent_fd: int, name: str):
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        mismatch.append(f"Component '{name}' is not a directory.")
        return None
    except OSError as e:
        mismatch.append(f"Failed to open '{name}': {e}")
        return None

def verify_ancestor(fd: int, path_name: str) -> None:
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        mismatch.append(f"Ancestor {path_name} is not a directory.")
    if st.st_uid != 0:
        mismatch.append(f"Ancestor {path_name} owned by UID {st.st_uid} (expected 0).")
    if (st.st_mode & 0o002) and not (st.st_mode & stat.S_ISVTX):
        mismatch.append(f"Ancestor {path_name} is world-writable without sticky bit (mode {oct(st.st_mode)}).")
    if (st.st_mode & 0o020) and st.st_gid != 0:
        mismatch.append(f"Ancestor {path_name} is group-writable by non-root group {st.st_gid}.")

def verify_node(parent_fd: int, name: str, expected_uid: int, expected_gid: int, expected_mode: int, full_path: str, expected_children: set):
    fd = open_dir_nofollow(parent_fd, name)
    if fd is None:
        missing.append(f"Path '{full_path}' does not exist")
        return None

    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            mismatch.append(f"Security violation: Path '{full_path}' is a symbolic link")
            return None
        if not stat.S_ISDIR(st.st_mode):
            mismatch.append(f"Path '{full_path}' exists but is not a directory")
            return None

        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != expected_uid:
            mismatch.append(f"Directory '{full_path}' owned by UID {st.st_uid} (expected {expected_uid})")
        if st.st_gid != expected_gid:
            mismatch.append(f"Directory '{full_path}' owned by GID {st.st_gid} (expected {expected_gid})")
        if actual_mode != expected_mode:
            mismatch.append(f"Directory '{full_path}' mode is {oct(actual_mode)} (expected strictly {oct(expected_mode)})")

        # DEP02A-R4-01: Enforce exact permitted direct-child set descriptor-relatively on open fd
        with os.scandir(fd) as it:
            entries = set(e.name for e in it)
        extra = entries - expected_children
        missing_children = expected_children - entries
        if extra:
            mismatch.append(f"Directory '{full_path}' contains unreviewed direct entries: {sorted(extra)}")
        if missing_children:
            mismatch.append(f"Directory '{full_path}' is missing expected direct entries: {sorted(missing_children)}")
        return fd
    except Exception as e:
        mismatch.append(f"Error inspecting '{full_path}': {e}")
        os.close(fd)
        return None

def verify_lock_file(dir_fd: int, full_dir_path: str, lock_name: str, expected_uid: int, expected_gid: int) -> None:
    full_path = f"{full_dir_path}/{lock_name}"
    try:
        st = os.stat(lock_name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            mismatch.append(f"Security violation: Lock file '{full_path}' is a symbolic link")
            return
        if not stat.S_ISREG(st.st_mode):
            mismatch.append(f"Path '{full_path}' exists but is not a regular file (mode {oct(st.st_mode)})")
            return
        if st.st_uid != expected_uid:
            mismatch.append(f"Lock file '{full_path}' owned by UID {st.st_uid} (expected {expected_uid})")
        if st.st_gid != expected_gid:
            mismatch.append(f"Lock file '{full_path}' owned by GID {st.st_gid} (expected {expected_gid})")
        actual_mode = stat.S_IMODE(st.st_mode)
        if actual_mode != 0o600:
            mismatch.append(f"Lock file '{full_path}' mode is {oct(actual_mode)} (expected strictly 0600)")
    except OSError as e:
        mismatch.append(f"Failed to inspect lock file '{full_path}': {e}")

def verify_socket_node(dir_fd: int, full_dir_path: str, sock_name: str, expected_uid: int, expected_gid: int) -> None:
    full_path = f"{full_dir_path}/{sock_name}"
    try:
        st = os.stat(sock_name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            mismatch.append(f"Security violation: Socket '{full_path}' is a symbolic link")
            return
        if not stat.S_ISSOCK(st.st_mode):
            mismatch.append(f"Path '{full_path}' exists but is not a UNIX domain socket (mode {oct(st.st_mode)})")
            return
        if st.st_uid != expected_uid:
            mismatch.append(f"Socket '{full_path}' owned by UID {st.st_uid} (expected {expected_uid})")
        if st.st_gid != expected_gid:
            mismatch.append(f"Socket '{full_path}' owned by GID {st.st_gid} (expected {expected_gid})")
        actual_mode = stat.S_IMODE(st.st_mode)
        if actual_mode != 0o660:
            mismatch.append(f"Socket '{full_path}' mode is {oct(actual_mode)} (expected strictly 0660)")
    except OSError as e:
        mismatch.append(f"Failed to inspect socket '{full_path}': {e}")

def verify_socket_dir(
    parent_fd: int,
    dir_name: str,
    expected_dir_uid: int,
    expected_dir_gid: int,
    expected_dir_mode: int,
    full_dir_path: str,
    expected_lock_name: str,
    expected_socket_name: str,
    expected_node_uid: int,
    expected_node_gid: int,
):
    dir_fd = open_dir_nofollow(parent_fd, dir_name)
    if dir_fd is None:
        missing.append(f"Path '{full_dir_path}' does not exist")
        return None

    try:
        st = os.fstat(dir_fd)
        if stat.S_ISLNK(st.st_mode):
            mismatch.append(f"Security violation: Path '{full_dir_path}' is a symbolic link")
            return None
        if not stat.S_ISDIR(st.st_mode):
            mismatch.append(f"Path '{full_dir_path}' exists but is not a directory")
            return None

        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != expected_dir_uid:
            mismatch.append(f"Directory '{full_dir_path}' owned by UID {st.st_uid} (expected {expected_dir_uid})")
        if st.st_gid != expected_dir_gid:
            mismatch.append(f"Directory '{full_dir_path}' owned by GID {st.st_gid} (expected {expected_dir_gid})")
        if actual_mode != expected_dir_mode:
            mismatch.append(f"Directory '{full_dir_path}' mode is {oct(actual_mode)} (expected strictly {oct(expected_dir_mode)})")

        with os.scandir(dir_fd) as it:
            entries = set(e.name for e in it)

        # Realigned exact-child policy (DEP02A-DAC-REALIGN):
        # State A (Idle / pre-daemon): set()
        # State B (Stopped / initialized): {expected_lock_name}
        # State C (Active running): {expected_lock_name, expected_socket_name}
        if entries == set():
            pass  # Valid idle state
        elif entries == {expected_lock_name}:
            verify_lock_file(dir_fd, full_dir_path, expected_lock_name, expected_node_uid, expected_node_gid)
        elif entries == {expected_lock_name, expected_socket_name}:
            verify_lock_file(dir_fd, full_dir_path, expected_lock_name, expected_node_uid, expected_node_gid)
            verify_socket_node(dir_fd, full_dir_path, expected_socket_name, expected_node_uid, expected_node_gid)
        else:
            extra = entries - {expected_lock_name, expected_socket_name}
            if extra:
                mismatch.append(f"Directory '{full_dir_path}' contains unreviewed direct entries: {sorted(extra)}")
            else:
                mismatch.append(
                    f"Directory '{full_dir_path}' contains invalid child state: {sorted(entries)} "
                    f"(permitted: empty, {{{expected_lock_name}}}, or {{{expected_lock_name}, {expected_socket_name}}})"
                )
        return dir_fd
    except Exception as e:
        mismatch.append(f"Error inspecting '{full_dir_path}': {e}")
        os.close(dir_fd)
        return None

# Permitted direct regular files in /var/lib/resolveflow/dispatcher (metadata-only allowlist;
# all entries optional; empty initial directory valid; legacy replay basenames retained for
# metadata compatibility only without authorizing dual-ledger fallback or state migration):
PERMITTED_DISPATCHER_FILES = {
    "execution_journal.wal",
    "execution.log",
    "target_ledger.v2.json",
    "receipt.json",
    "consumed_preflight_runs.jsonl",
    "consumed_execution_workorders.jsonl",
    "consumed_admissions.jsonl",
}

def verify_dispatcher_state_dir(parent_fd: int, dir_name: str, full_path: str):
    fd = open_dir_nofollow(parent_fd, dir_name)
    if fd is None:
        missing.append(f"Path '{full_path}' does not exist")
        return None

    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            mismatch.append(f"Security violation: Path '{full_path}' is a symbolic link")
            return None
        if not stat.S_ISDIR(st.st_mode):
            mismatch.append(f"Path '{full_path}' exists but is not a directory")
            return None

        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != DISPATCHER_UID:
            mismatch.append(f"Directory '{full_path}' owned by UID {st.st_uid} (expected {DISPATCHER_UID})")
        if st.st_gid != DISPATCHER_GID:
            mismatch.append(f"Directory '{full_path}' owned by GID {st.st_gid} (expected {DISPATCHER_GID})")
        if actual_mode != 0o700:
            mismatch.append(f"Directory '{full_path}' mode is {oct(actual_mode)} (expected strictly 0700)")

        with os.scandir(fd) as it:
            entries = set(e.name for e in it)

        # Permitted direct entries: drawn strictly from PERMITTED_DISPATCHER_FILES and optional .locks directory
        unreviewed = entries - (PERMITTED_DISPATCHER_FILES | {".locks"})
        if unreviewed:
            mismatch.append(f"Directory '{full_path}' contains unreviewed direct entries: {sorted(unreviewed)}")

        # Verify regular files
        for fname in entries & PERMITTED_DISPATCHER_FILES:
            fpath = f"{full_path}/{fname}"
            try:
                fst = os.stat(fname, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(fst.st_mode):
                    mismatch.append(f"Security violation: File '{fpath}' is a symbolic link")
                    continue
                if not stat.S_ISREG(fst.st_mode):
                    mismatch.append(f"Path '{fpath}' exists but is not a regular file (mode {oct(fst.st_mode)})")
                    continue
                if fst.st_nlink != 1:
                    mismatch.append(f"File '{fpath}' link count anomaly: {fst.st_nlink} (expected 1)")
                if fst.st_uid != DISPATCHER_UID:
                    mismatch.append(f"File '{fpath}' owned by UID {fst.st_uid} (expected {DISPATCHER_UID})")
                if fst.st_gid != DISPATCHER_GID:
                    mismatch.append(f"File '{fpath}' owned by GID {fst.st_gid} (expected {DISPATCHER_GID})")
                fmode = stat.S_IMODE(fst.st_mode)
                if fmode != 0o600:
                    mismatch.append(f"File '{fpath}' mode is {oct(fmode)} (expected strictly 0600)")
            except OSError as e:
                mismatch.append(f"Failed to inspect file '{fpath}': {e}")

        # Verify .locks directory if present
        if ".locks" in entries:
            locks_path = f"{full_path}/.locks"
            locks_fd = open_dir_nofollow(fd, ".locks")
            if locks_fd is not None:
                try:
                    lst = os.fstat(locks_fd)
                    if stat.S_ISLNK(lst.st_mode):
                        mismatch.append(f"Security violation: Path '{locks_path}' is a symbolic link")
                    elif not stat.S_ISDIR(lst.st_mode):
                        mismatch.append(f"Path '{locks_path}' exists but is not a directory")
                    else:
                        lmode = stat.S_IMODE(lst.st_mode)
                        if lst.st_uid != DISPATCHER_UID:
                            mismatch.append(f"Directory '{locks_path}' owned by UID {lst.st_uid} (expected {DISPATCHER_UID})")
                        if lst.st_gid != DISPATCHER_GID:
                            mismatch.append(f"Directory '{locks_path}' owned by GID {lst.st_gid} (expected {DISPATCHER_GID})")
                        if lmode != 0o700:
                            mismatch.append(f"Directory '{locks_path}' mode is {oct(lmode)} (expected strictly 0700)")

                        with os.scandir(locks_fd) as lit:
                            for lentry in lit:
                                lnode_path = f"{locks_path}/{lentry.name}"
                                if not (lentry.name.endswith(".lock") or lentry.name.endswith(".in_doubt")):
                                    mismatch.append(f"Directory '{locks_path}' contains unreviewed entry: {lentry.name}")
                                    continue
                                try:
                                    nst = os.stat(lentry.name, dir_fd=locks_fd, follow_symlinks=False)
                                    if stat.S_ISLNK(nst.st_mode):
                                        mismatch.append(f"Security violation: Lock/marker node '{lnode_path}' is a symbolic link")
                                        continue
                                    if not stat.S_ISREG(nst.st_mode):
                                        mismatch.append(f"Lock/marker node '{lnode_path}' is not a regular file (mode {oct(nst.st_mode)})")
                                        continue
                                    if nst.st_nlink != 1:
                                        mismatch.append(f"Lock/marker node '{lnode_path}' link count anomaly: {nst.st_nlink} (expected 1)")
                                    if nst.st_uid != DISPATCHER_UID:
                                        mismatch.append(f"Lock/marker node '{lnode_path}' owned by UID {nst.st_uid} (expected {DISPATCHER_UID})")
                                    if nst.st_gid != DISPATCHER_GID:
                                        mismatch.append(f"Lock/marker node '{lnode_path}' owned by GID {nst.st_gid} (expected {DISPATCHER_GID})")
                                    nmode = stat.S_IMODE(nst.st_mode)
                                    if nmode != 0o600:
                                        mismatch.append(f"Lock/marker node '{lnode_path}' mode is {oct(nmode)} (expected strictly 0600)")
                                except OSError as e:
                                    mismatch.append(f"Failed to inspect lock/marker node '{lnode_path}': {e}")
                finally:
                    os.close(locks_fd)

        return fd
    except Exception as e:
        mismatch.append(f"Error inspecting '{full_path}': {e}")
        os.close(fd)
        return None

KNOWN_TRUST_ANCHORS = {
    "oracle_public_key.pem",
    "preflight_verifier_public_key.pem",
    "tech_lead_public_key.pem",
}

def verify_trust_root_dir(parent_fd: int, dir_name: str, full_path: str):
    fd = open_dir_nofollow(parent_fd, dir_name)
    if fd is None:
        missing.append(f"Path '{full_path}' does not exist")
        return None

    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            mismatch.append(f"Security violation: Path '{full_path}' is a symbolic link")
            return None
        if not stat.S_ISDIR(st.st_mode):
            mismatch.append(f"Path '{full_path}' exists but is not a directory")
            return None

        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != 0:
            mismatch.append(f"Directory '{full_path}' owned by UID {st.st_uid} (expected 0)")
        if st.st_gid != 0:
            mismatch.append(f"Directory '{full_path}' owned by GID {st.st_gid} (expected 0)")
        if actual_mode != 0o755:
            mismatch.append(f"Directory '{full_path}' mode is {oct(actual_mode)} (expected strictly 0755)")

        with os.scandir(fd) as it:
            entries = set(e.name for e in it)

        # Check for unreviewed entries and Gate 5 revocation ledger
        for name in entries:
            if name == "revoked_keys.jsonl":
                mismatch.append(
                    f"Path '{full_path}/revoked_keys.jsonl' encountered "
                    "without established DAC policy (unresolved packaging prerequisite)"
                )
            elif name not in KNOWN_TRUST_ANCHORS:
                mismatch.append(f"Directory '{full_path}' contains unreviewed direct entry: {name}")

        # Verify metadata of present known public key trust anchors without reading content
        for key_name in entries & KNOWN_TRUST_ANCHORS:
            key_path = f"{full_path}/{key_name}"
            try:
                kst = os.stat(key_name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(kst.st_mode):
                    mismatch.append(f"Security violation: Trust anchor '{key_path}' is a symbolic link")
                    continue
                if not stat.S_ISREG(kst.st_mode):
                    mismatch.append(f"Trust anchor '{key_path}' exists but is not a regular file (mode {oct(kst.st_mode)})")
                    continue
                if kst.st_nlink != 1:
                    mismatch.append(f"Trust anchor '{key_path}' link count anomaly: {kst.st_nlink} (expected 1)")
                if kst.st_uid != 0:
                    mismatch.append(f"Trust anchor '{key_path}' owned by UID {kst.st_uid} (expected 0)")
                if kst.st_gid != 0:
                    mismatch.append(f"Trust anchor '{key_path}' owned by GID {kst.st_gid} (expected 0)")
                kmode = stat.S_IMODE(kst.st_mode)
                if kmode != 0o644:
                    mismatch.append(f"Trust anchor '{key_path}' mode is {oct(kmode)} (expected strictly 0644)")
            except OSError as e:
                mismatch.append(f"Failed to inspect trust anchor '{key_path}': {e}")

        # Report missing known trust anchors as absent/unprovisioned
        for key_name in sorted(KNOWN_TRUST_ANCHORS):
            if key_name not in entries:
                missing.append(f"Trust anchor '{full_path}/{key_name}' is absent (unprovisioned)")

        return fd
    except Exception as e:
        mismatch.append(f"Error inspecting '{full_path}': {e}")
        os.close(fd)
        return None

# Check root and ancestors
try:
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    verify_ancestor(root_fd, "/")

    var_fd = open_dir_nofollow(root_fd, "var")
    if var_fd is not None:
        verify_ancestor(var_fd, "/var")
        lib_fd = open_dir_nofollow(var_fd, "lib")
        if lib_fd is not None:
            verify_ancestor(lib_fd, "/var/lib")

            # Check /var/lib/resolveflow hierarchy
            rf_lib_fd = verify_node(lib_fd, "resolveflow", 0, 0, 0o755, "/var/lib/resolveflow", {"signer", "dispatcher"})
            if rf_lib_fd is not None:
                signer_lib_fd = verify_node(rf_lib_fd, "signer", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer", {"keystore", "nonces"})
                if signer_lib_fd is not None:
                    ks_fd = verify_node(signer_lib_fd, "keystore", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer/keystore", set())
                    if ks_fd is not None: os.close(ks_fd)

                    nonces_fd = verify_node(signer_lib_fd, "nonces", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer/nonces", set())
                    if nonces_fd is not None: os.close(nonces_fd)

                    os.close(signer_lib_fd)

                dispatcher_lib_fd = verify_dispatcher_state_dir(rf_lib_fd, "dispatcher", "/var/lib/resolveflow/dispatcher")
                if dispatcher_lib_fd is not None:
                    os.close(dispatcher_lib_fd)

                os.close(rf_lib_fd)
            os.close(lib_fd)
        os.close(var_fd)

    etc_fd = open_dir_nofollow(root_fd, "etc")
    if etc_fd is not None:
        verify_ancestor(etc_fd, "/etc")
        rf_etc_fd = verify_trust_root_dir(etc_fd, "resolveflow", "/etc/resolveflow")
        if rf_etc_fd is not None:
            os.close(rf_etc_fd)
        os.close(etc_fd)

    run_fd = open_dir_nofollow(root_fd, "run")
    if run_fd is not None:
        verify_ancestor(run_fd, "/run")

        # Check /run/resolveflow hierarchy (mode strictly 02750 setgid per DEP02A-DAC-REALIGN)
        rf_run_fd = verify_node(run_fd, "resolveflow", 0, 0, 0o755, "/run/resolveflow", {"oracle", "signer_client", "dispatcher_client"})
        if rf_run_fd is not None:
            oracle_fd = verify_socket_dir(
                rf_run_fd, "oracle",
                SIGNER_UID, DISPATCHER_GID, 0o2750, "/run/resolveflow/oracle",
                "signer_oracle.lock", "signer_oracle.sock", SIGNER_UID, DISPATCHER_GID
            )
            if oracle_fd is not None: os.close(oracle_fd)

            signer_client_fd = verify_socket_dir(
                rf_run_fd, "signer_client",
                SIGNER_UID, CLIENTS_GID, 0o2750, "/run/resolveflow/signer_client",
                "signer_public.lock", "signer_public.sock", SIGNER_UID, CLIENTS_GID
            )
            if signer_client_fd is not None: os.close(signer_client_fd)

            dispatcher_client_fd = verify_socket_dir(
                rf_run_fd, "dispatcher_client",
                DISPATCHER_UID, CLIENTS_GID, 0o2750, "/run/resolveflow/dispatcher_client",
                "dispatcher.lock", "dispatcher.sock", DISPATCHER_UID, CLIENTS_GID
            )
            if dispatcher_client_fd is not None: os.close(dispatcher_client_fd)

            os.close(rf_run_fd)
        os.close(run_fd)

    os.close(root_fd)
except Exception as e:
    mismatch.append(f"Filesystem traversal error: {e}")

print(json.dumps({"missing": missing, "mismatch": mismatch}))
PY_VERIFY_EOF
)"

# Parse python output
PY_MISSING_COUNT="$(python3 -c "import json; d=json.loads('''$VERIFY_JSON'''); print(len(d['missing']))")"
PY_MISMATCH_COUNT="$(python3 -c "import json; d=json.loads('''$VERIFY_JSON'''); print(len(d['mismatch']))")"

if [ "$PY_MISSING_COUNT" -gt 0 ]; then
    while IFS= read -r item; do
        if [ -n "$item" ]; then
            record_missing "$item"
        fi
    done < <(python3 -c "import json; d=json.loads('''$VERIFY_JSON'''); print('\n'.join(d['missing']))")
fi

if [ "$PY_MISMATCH_COUNT" -gt 0 ]; then
    while IFS= read -r item; do
        if [ -n "$item" ]; then
            record_mismatch "$item"
        fi
    done < <(python3 -c "import json; d=json.loads('''$VERIFY_JSON'''); print('\n'.join(d['mismatch']))")
fi

# ------------------------------------------------------------------------------
# 3. Synthesize Verification Report & Exit Code
# ------------------------------------------------------------------------------
echo "========================================================================"
echo "ResolveFlow DAC & Principal Verification Result (Revision R8)"
echo "========================================================================"

if [ "${#MISSING_ITEMS[@]}" -gt 0 ]; then
    echo "STATUS=NOT_PROVISIONED"
    echo ""
    echo "Missing components (${#MISSING_ITEMS[@]} items):"
    for item in "${MISSING_ITEMS[@]}"; do
        echo "  - $item"
    done
    if [ "${#MISMATCH_ITEMS[@]}" -gt 0 ]; then
        echo ""
        echo "Additional mismatches detected (${#MISMATCH_ITEMS[@]} items):"
        for item in "${MISMATCH_ITEMS[@]}"; do
            echo "  - $item"
        done
    fi
    echo "========================================================================"
    exit 2
fi

if [ "${#MISMATCH_ITEMS[@]}" -gt 0 ]; then
    echo "STATUS=FAILED_MISMATCH"
    echo ""
    echo "Integrity violations detected (${#MISMATCH_ITEMS[@]} items):"
    for item in "${MISMATCH_ITEMS[@]}"; do
        echo "  - $item"
    done
    echo "========================================================================"
    exit 1
fi

echo "STATUS=VERIFIED_OK"
echo "All Principals, GIDs, UIDs, empty content policies, and Filesystem DAC boundaries conform strictly to DEP-01 R3 / DEP-02A R4."
echo "========================================================================"
exit 0
