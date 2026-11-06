#!/usr/bin/env bash
# ==============================================================================
# ResolveFlow Principal & DAC Provisioning Script (Revision R8)
# Workorder: G07-07B7-GATE6-PHASE2-BLOCKER-D-PACKAGING-REALIGN-R1
# Reference: docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md
# Review Reference: docs/architecture/07B7-GATE6-DEP02A-R6-code-review.md
#
# REMEDIATION DETAILS (Revision R8):
# - BLOCKER-D-PACKAGING-REALIGN-R1:
#   * Extends /var/lib/resolveflow exact direct-child policy from {'signer'} to {'signer', 'dispatcher'}.
#   * Provisions /var/lib/resolveflow/dispatcher as owner _resolveflow_dispatcher:902, mode 0700, empty (set()).
#     Pre-existing non-empty directory fails closed without mutation.
#   * Adds descriptor-anchored handling for Class A trust-root directory /etc/resolveflow (owner root:root, mode 0755).
#     Verifies known public keys (oracle_public_key.pem, preflight_verifier_public_key.pem, tech_lead_public_key.pem)
#     as 0644 root:root when present without reading, generating, or distributing keys. Fails closed on
#     revoked_keys.jsonl (unresolved packaging prerequisite) or unreviewed entries.
#   * Reconciles legacy signer anchors directory: removes creation of /var/lib/resolveflow/signer/anchors and updates
#     signer direct children to {'keystore', 'nonces'}.
# - DEP02A-R4-01 & DEP02A-R5-01:
#   * Enforces exact permitted direct-child set for parent directories (/var/lib/resolveflow -> {'signer', 'dispatcher'};
#     signer -> {'keystore', 'nonces'}; /run/resolveflow -> {'oracle', 'signer_client', 'dispatcher_client'}).
#   * Maintains fail-closed empty directory policy at host provisioning setup before daemons are deployed.
# - DEP02A-R3-01 (Descriptor-Anchored Directory Content Verification):
#   * Evaluates directory type, UID, GID, mode, and empty contents exclusively through
#     the open, pinned directory descriptor (os.open with O_NOFOLLOW | O_DIRECTORY, fstat,
#     and os.scandir(fd)).
# - DEP02A-R3-02 (Strict Supplementary Group Verification for Daemon Accounts):
#   * Enforces that daemon accounts possess ZERO supplementary groups (only primary GID permitted:
#     901 for _resolveflow_signer, 902 for _resolveflow_dispatcher). Rejects pre-existing accounts
#     with unexpected group memberships fail-closed.
# - INERT BY DEFAULT: Requires explicit '--apply' flag + EUID 0 to perform mutations.
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

APPLY_MODE=0

# Parse CLI arguments
for arg in "$@"; do
    case "$arg" in
        --apply)
            APPLY_MODE=1
            ;;
        --help|-h)
            cat << 'EOF'
Usage: provision_resolveflow_principals.sh [--apply]

Options:
  --apply    Execute provisioning operations (requires EUID 0).
             Without this flag, the script runs in PLAN mode (dry-run).
  --help     Show this help message.

Environment Variables:
  RESOLVEFLOW_RUNNER_USER    Override the interactive user account to be added
                             to 'resolveflow_clients' (defaults to SUDO_USER or current user).
EOF
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $arg" >&2
            echo "Use --help for usage instructions." >&2
            exit 1
            ;;
    esac
done

fail_closed() {
    echo "FATAL [FAIL-CLOSED]: $1" >&2
    exit 1
}

# ------------------------------------------------------------------------------
# 1. Complete Read-Only Preflight Validations (DEP02A-R2-02 & DEP02A-R3-02)
# All checks execute BEFORE any mutation to users, groups, or directories.
# ------------------------------------------------------------------------------
validate_group_conflict() {
    local target_name="$1"
    local target_gid="$2"

    if getent group "$target_name" > /dev/null 2>&1; then
        local existing_gid
        existing_gid="$(getent group "$target_name" | cut -d: -f3)"
        if [ "$existing_gid" -ne "$target_gid" ]; then
            fail_closed "Group '$target_name' already exists with GID $existing_gid (expected $target_gid)."
        fi
    fi

    local existing_name
    existing_name="$(getent group "$target_gid" 2>/dev/null | cut -d: -f1 || true)"
    if [ -n "$existing_name" ] && [ "$existing_name" != "$target_name" ]; then
        fail_closed "GID $target_gid is already assigned to group '$existing_name' (expected '$target_name')."
    fi
}

validate_user_conflict() {
    local target_name="$1"
    local target_uid="$2"
    local target_gid="$3"

    if getent passwd "$target_name" > /dev/null 2>&1; then
        local existing_uid existing_user_gid
        existing_uid="$(getent passwd "$target_name" | cut -d: -f3)"
        existing_user_gid="$(getent passwd "$target_name" | cut -d: -f4)"
        if [ "$existing_uid" -ne "$target_uid" ]; then
            fail_closed "User '$target_name' already exists with UID $existing_uid (expected $target_uid)."
        fi
        if [ "$existing_user_gid" -ne "$target_gid" ]; then
            fail_closed "User '$target_name' has primary GID $existing_user_gid (expected $target_gid)."
        fi

        # DEP02A-R3-02: Verify existing daemon account has zero supplementary groups
        local user_gids
        user_gids="$(id -G "$target_name" 2>/dev/null || echo "")"
        if [ "$user_gids" != "$target_gid" ]; then
            fail_closed "User '$target_name' has unauthorized supplementary groups '$user_gids' (expected only primary GID $target_gid)."
        fi
    fi

    local existing_user
    existing_user="$(getent passwd "$target_uid" 2>/dev/null | cut -d: -f1 || true)"
    if [ -n "$existing_user" ] && [ "$existing_user" != "$target_name" ]; then
        fail_closed "UID $target_uid is already assigned to user '$existing_user' (expected '$target_name')."
    fi
}

# Validate identity conflicts
validate_group_conflict "$SIGNER_GROUP" "$SIGNER_GID"
validate_group_conflict "$DISPATCHER_GROUP" "$DISPATCHER_GID"
validate_group_conflict "$CLIENTS_GROUP" "$CLIENTS_GID"

validate_user_conflict "$SIGNER_USER" "$SIGNER_UID" "$SIGNER_GID"
validate_user_conflict "$DISPATCHER_USER" "$DISPATCHER_UID" "$DISPATCHER_GID"

if ! getent passwd "$RUNNER_USER" > /dev/null 2>&1; then
    fail_closed "Designated runner user '$RUNNER_USER' does not exist on this host."
fi

# Validate filesystem hierarchy, ancestors, and empty content policy (Read-Only)
/usr/bin/python3 - << 'PY_PREFLIGHT_EOF'
import os
import stat
import sys

SIGNER_UID = 901
SIGNER_GID = 901
DISPATCHER_UID = 902
DISPATCHER_GID = 902
CLIENTS_GID = 903

def fail_closed(msg: str) -> None:
    sys.stderr.write(f"FATAL [PREFLIGHT FAIL-CLOSED]: {msg}\n")
    sys.exit(1)

def verify_ancestor(fd: int, path_name: str) -> None:
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        fail_closed(f"Ancestor {path_name} is not a directory.")
    if st.st_uid != 0:
        fail_closed(f"Ancestor {path_name} owned by UID {st.st_uid} (expected 0).")
    if (st.st_mode & 0o002) and not (st.st_mode & stat.S_ISVTX):
        fail_closed(f"Ancestor {path_name} is world-writable without sticky bit (mode {oct(st.st_mode)}).")
    if (st.st_mode & 0o020) and st.st_gid != 0:
        fail_closed(f"Ancestor {path_name} is group-writable by non-root group {st.st_gid}.")

def open_dir_nofollow(parent_fd: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)

def inspect_and_open_if_exists(parent_fd: int, name: str, expected_uid: int, expected_gid: int, expected_mode: int, full_path: str):
    try:
        fd = open_dir_nofollow(parent_fd, name)
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        fail_closed(f"Path '{full_path}' exists and is not a directory.")
    except OSError as e:
        fail_closed(f"Failed to open '{full_path}': {e}")

    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            fail_closed(f"Path '{full_path}' is a symbolic link.")
        if not stat.S_ISDIR(st.st_mode):
            fail_closed(f"Path '{full_path}' is not a directory.")

        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != expected_uid or st.st_gid != expected_gid or actual_mode != expected_mode:
            fail_closed(
                f"Pre-existing directory '{full_path}' has conflicting DAC "
                f"(found {st.st_uid}:{st.st_gid} mode {oct(actual_mode)}, expected {expected_uid}:{expected_gid} mode {oct(expected_mode)})."
            )
        return fd
    except Exception:
        os.close(fd)
        raise

def enforce_direct_children(fd: int, full_path: str, expected_children: set) -> None:
    with os.scandir(fd) as it:
        entries = set(e.name for e in it)
    extra = entries - expected_children
    missing = expected_children - entries
    if extra:
        fail_closed(f"Pre-existing directory '{full_path}' contains unreviewed entries: {sorted(extra)}.")
    if missing:
        fail_closed(f"Pre-existing directory '{full_path}' is missing expected entries: {sorted(missing)}.")

root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
verify_ancestor(root_fd, "/")

var_fd = open_dir_nofollow(root_fd, "var")
verify_ancestor(var_fd, "/var")

lib_fd = open_dir_nofollow(var_fd, "lib")
verify_ancestor(lib_fd, "/var/lib")

run_fd = open_dir_nofollow(root_fd, "run")
verify_ancestor(run_fd, "/run")

etc_fd = open_dir_nofollow(root_fd, "etc")
verify_ancestor(etc_fd, "/etc")

# Preflight check /var/lib/resolveflow subtree
rf_lib_fd = inspect_and_open_if_exists(lib_fd, "resolveflow", 0, 0, 0o755, "/var/lib/resolveflow")
if rf_lib_fd is not None:
    enforce_direct_children(rf_lib_fd, "/var/lib/resolveflow", {"signer", "dispatcher"})
    signer_lib_fd = inspect_and_open_if_exists(rf_lib_fd, "signer", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer")
    if signer_lib_fd is not None:
        enforce_direct_children(signer_lib_fd, "/var/lib/resolveflow/signer", {"keystore", "nonces"})
        ks_fd = inspect_and_open_if_exists(signer_lib_fd, "keystore", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer/keystore")
        if ks_fd is not None:
            enforce_direct_children(ks_fd, "/var/lib/resolveflow/signer/keystore", set())
            os.close(ks_fd)
        nonces_fd = inspect_and_open_if_exists(signer_lib_fd, "nonces", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer/nonces")
        if nonces_fd is not None:
            enforce_direct_children(nonces_fd, "/var/lib/resolveflow/signer/nonces", set())
            os.close(nonces_fd)
        os.close(signer_lib_fd)
    dispatcher_lib_fd = inspect_and_open_if_exists(rf_lib_fd, "dispatcher", DISPATCHER_UID, DISPATCHER_GID, 0o700, "/var/lib/resolveflow/dispatcher")
    if dispatcher_lib_fd is not None:
        enforce_direct_children(dispatcher_lib_fd, "/var/lib/resolveflow/dispatcher", set())
        os.close(dispatcher_lib_fd)
    os.close(rf_lib_fd)

# Preflight check /etc/resolveflow trust-root directory
KNOWN_ANCHORS = {"oracle_public_key.pem", "preflight_verifier_public_key.pem", "tech_lead_public_key.pem"}
rf_etc_fd = inspect_and_open_if_exists(etc_fd, "resolveflow", 0, 0, 0o755, "/etc/resolveflow")
if rf_etc_fd is not None:
    with os.scandir(rf_etc_fd) as it:
        for entry in it:
            if entry.name == "revoked_keys.jsonl":
                fail_closed(
                    "Pre-existing '/etc/resolveflow/revoked_keys.jsonl' encountered "
                    "without established DAC policy (unresolved packaging prerequisite)."
                )
            if entry.name not in KNOWN_ANCHORS:
                fail_closed(
                    f"Pre-existing directory '/etc/resolveflow' contains unreviewed entry: {entry.name}."
                )
            st = os.stat(entry.name, dir_fd=rf_etc_fd, follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode):
                fail_closed(f"Security violation: Anchor file '/etc/resolveflow/{entry.name}' is a symbolic link.")
            if not stat.S_ISREG(st.st_mode):
                fail_closed(f"Anchor file '/etc/resolveflow/{entry.name}' is not a regular file.")
            if st.st_nlink != 1:
                fail_closed(f"Anchor file '/etc/resolveflow/{entry.name}' link count is {st.st_nlink} (expected 1).")
            if st.st_uid != 0 or st.st_gid != 0:
                fail_closed(f"Anchor file '/etc/resolveflow/{entry.name}' owned by {st.st_uid}:{st.st_gid} (expected 0:0).")
            actual_mode = stat.S_IMODE(st.st_mode)
            if actual_mode != 0o644:
                fail_closed(f"Anchor file '/etc/resolveflow/{entry.name}' mode is {oct(actual_mode)} (expected 0644).")
    os.close(rf_etc_fd)
os.close(etc_fd)

# Preflight check /run/resolveflow subtree (mode strictly 02750 setgid per DEP02A-DAC-REALIGN)
rf_run_fd = inspect_and_open_if_exists(run_fd, "resolveflow", 0, 0, 0o755, "/run/resolveflow")
if rf_run_fd is not None:
    enforce_direct_children(rf_run_fd, "/run/resolveflow", {"oracle", "signer_client", "dispatcher_client"})
    oracle_fd = inspect_and_open_if_exists(rf_run_fd, "oracle", SIGNER_UID, DISPATCHER_GID, 0o2750, "/run/resolveflow/oracle")
    if oracle_fd is not None:
        enforce_direct_children(oracle_fd, "/run/resolveflow/oracle", set())
        os.close(oracle_fd)
    signer_client_fd = inspect_and_open_if_exists(rf_run_fd, "signer_client", SIGNER_UID, CLIENTS_GID, 0o2750, "/run/resolveflow/signer_client")
    if signer_client_fd is not None:
        enforce_direct_children(signer_client_fd, "/run/resolveflow/signer_client", set())
        os.close(signer_client_fd)
    dispatcher_client_fd = inspect_and_open_if_exists(rf_run_fd, "dispatcher_client", DISPATCHER_UID, CLIENTS_GID, 0o2750, "/run/resolveflow/dispatcher_client")
    if dispatcher_client_fd is not None:
        enforce_direct_children(dispatcher_client_fd, "/run/resolveflow/dispatcher_client", set())
        os.close(dispatcher_client_fd)
    os.close(rf_run_fd)

os.close(run_fd)
os.close(lib_fd)
os.close(var_fd)
os.close(root_fd)
PY_PREFLIGHT_EOF

# ------------------------------------------------------------------------------
# 2. PLAN MODE (Inert Default)
# ------------------------------------------------------------------------------
if [ "$APPLY_MODE" -eq 0 ]; then
    echo "========================================================================"
    echo "ResolveFlow Provisioning Plan (DRY-RUN / INERT MODE)"
    echo "All preflight conflict checks, ancestor safety, and empty content checks PASSED."
    echo "No modifications have been made to the host."
    echo "To execute this plan, re-run with: sudo $0 --apply"
    echo "========================================================================"
    echo ""
    echo "[Target Principals]"
    echo "  - Group '$SIGNER_GROUP' (GID $SIGNER_GID)"
    echo "  - Group '$DISPATCHER_GROUP' (GID $DISPATCHER_GID)"
    echo "  - Group '$CLIENTS_GROUP' (GID $CLIENTS_GID)"
    echo "  - User  '$SIGNER_USER' (UID $SIGNER_UID, GID $SIGNER_GID, supplementary=[], shell=/usr/sbin/nologin, home=/var/lib/resolveflow/signer)"
    echo "  - User  '$DISPATCHER_USER' (UID $DISPATCHER_UID, GID $DISPATCHER_GID, supplementary=[], shell=/usr/sbin/nologin, home=/nonexistent)"
    echo "  - Supplementary: Add '$RUNNER_USER' to group '$CLIENTS_GROUP'"
    echo ""
    echo "[Target Directory Hierarchy & DAC Permissions (Descriptor-Anchored)]"
    echo "  - /var/lib/resolveflow                  (Owner 0:0, Mode 0755)"
    echo "  - /var/lib/resolveflow/signer           (Owner $SIGNER_UID:$SIGNER_GID, Mode 0700)"
    echo "  - /var/lib/resolveflow/signer/keystore  (Owner $SIGNER_UID:$SIGNER_GID, Mode 0700, Empty)"
    echo "  - /var/lib/resolveflow/signer/nonces    (Owner $SIGNER_UID:$SIGNER_GID, Mode 0700, Empty)"
    echo "  - /var/lib/resolveflow/dispatcher       (Owner $DISPATCHER_UID:$DISPATCHER_GID, Mode 0700, Empty)"
    echo "  - /etc/resolveflow                      (Owner 0:0, Mode 0755)"
    echo "  - /run/resolveflow                      (Owner 0:0, Mode 0755)"
    echo "  - /run/resolveflow/oracle               (Owner $SIGNER_UID:$DISPATCHER_GID, Mode 02750, Empty)"
    echo "  - /run/resolveflow/signer_client        (Owner $SIGNER_UID:$CLIENTS_GID, Mode 02750, Empty)"
    echo "  - /run/resolveflow/dispatcher_client    (Owner $DISPATCHER_UID:$CLIENTS_GID, Mode 02750, Empty)"
    echo ""
    echo "[Security & Invariant Guarantees]"
    echo "  - Ancestor directories validated: no group/world-writable permissions."
    echo "  - Pre-existing subtree protection: fail-closed if directories exist with foreign ownership or contents."
    echo "  - No path-following chmod/chown: all mutations applied to pinned file descriptors."
    echo "  - Zero services started or enabled."
    echo "  - Zero cryptographic keys or nonces created."
    echo "  - Zero socket files created."
    echo "  - Zero DaVinci Resolve API calls or IPC access."
    echo "========================================================================"
    exit 0
fi

# ------------------------------------------------------------------------------
# 3. APPLY MODE (Guarded by EUID 0)
# All preflight checks have succeeded prior to reaching this section.
# ------------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    fail_closed "The '--apply' flag requires root privileges (EUID 0). Exiting without changes."
fi

echo "INFO: Applying ResolveFlow principal and DAC provisioning..."

# Group provisioning (Idempotent)
ensure_group() {
    local gname="$1"
    local gid="$2"
    if getent group "$gname" > /dev/null 2>&1; then
        echo "  [OK] Group '$gname' (GID $gid) already exists."
    else
        echo "  [CREATE] Group '$gname' (GID $gid)..."
        groupadd -g "$gid" --system "$gname"
    fi
}

ensure_group "$SIGNER_GROUP" "$SIGNER_GID"
ensure_group "$DISPATCHER_GROUP" "$DISPATCHER_GID"
ensure_group "$CLIENTS_GROUP" "$CLIENTS_GID"

# User provisioning (Idempotent)
ensure_user() {
    local uname="$1"
    local uid="$2"
    local gid="$3"
    local home_dir="$4"
    local comment="$5"

    if getent passwd "$uname" > /dev/null 2>&1; then
        echo "  [OK] User '$uname' (UID $uid) already exists."
    else
        echo "  [CREATE] User '$uname' (UID $uid, GID $gid)..."
        useradd -u "$uid" -g "$gid" --system -d "$home_dir" -s /usr/sbin/nologin -c "$comment" "$uname"
    fi
}

ensure_user "$SIGNER_USER" "$SIGNER_UID" "$SIGNER_GID" "/var/lib/resolveflow/signer" "ResolveFlow Signer Daemon"
ensure_user "$DISPATCHER_USER" "$DISPATCHER_UID" "$DISPATCHER_GID" "/nonexistent" "ResolveFlow Dispatcher Daemon"

# Add runner user to supplementary group
if id -nG "$RUNNER_USER" | grep -qw "$CLIENTS_GROUP"; then
    echo "  [OK] User '$RUNNER_USER' is already a member of '$CLIENTS_GROUP'."
else
    echo "  [ADD] Adding '$RUNNER_USER' to supplementary group '$CLIENTS_GROUP'..."
    usermod -a -G "$CLIENTS_GROUP" "$RUNNER_USER"
fi

# ------------------------------------------------------------------------------
# 4. Descriptor-Anchored Filesystem Creation
# ------------------------------------------------------------------------------
echo "INFO: Executing descriptor-anchored filesystem boundary provisioning..."

/usr/bin/python3 - << 'PY_APPLY_EOF'
import os
import stat
import sys

SIGNER_UID = 901
SIGNER_GID = 901
DISPATCHER_UID = 902
DISPATCHER_GID = 902
CLIENTS_GID = 903

def fail_closed(msg: str) -> None:
    sys.stderr.write(f"FATAL [APPLY FAIL-CLOSED]: {msg}\n")
    sys.exit(1)

def open_dir_nofollow(parent_fd: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)

def enforce_direct_children(fd: int, full_path: str, expected_children: set) -> None:
    with os.scandir(fd) as it:
        entries = set(e.name for e in it)
    extra = entries - expected_children
    missing = expected_children - entries
    if extra:
        fail_closed(f"Directory '{full_path}' contains unreviewed entries: {sorted(extra)}.")
    if missing:
        fail_closed(f"Directory '{full_path}' is missing expected entries: {sorted(missing)}.")

def provision_node(parent_fd: int, name: str, expected_uid: int, expected_gid: int, expected_mode: int, full_path: str) -> int:
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        already_exists = True
    except FileNotFoundError:
        already_exists = False

    if already_exists:
        if stat.S_ISLNK(st.st_mode):
            fail_closed(f"Path '{full_path}' exists and is a symbolic link.")
        if not stat.S_ISDIR(st.st_mode):
            fail_closed(f"Path '{full_path}' exists and is not a directory.")
        
        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != expected_uid or st.st_gid != expected_gid or actual_mode != expected_mode:
            fail_closed(
                f"Pre-existing directory '{full_path}' has conflicting DAC "
                f"(found {st.st_uid}:{st.st_gid} mode {oct(actual_mode)}, expected {expected_uid}:{expected_gid} mode {oct(expected_mode)})."
            )
        fd = open_dir_nofollow(parent_fd, name)
        print(f"  [OK] Pre-existing '{full_path}' ({expected_uid}:{expected_gid}, mode {oct(expected_mode)}) matches exactly.")
        return fd
    else:
        os.mkdir(name, mode=expected_mode, dir_fd=parent_fd)
        fd = open_dir_nofollow(parent_fd, name)
        os.fchown(fd, expected_uid, expected_gid)
        os.fchmod(fd, expected_mode)
        st = os.fstat(fd)
        actual_mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != expected_uid or st.st_gid != expected_gid or actual_mode != expected_mode:
            fail_closed(f"Descriptor verification failed on newly created '{full_path}'.")
        print(f"  [CREATE] Pinned directory '{full_path}' established ({expected_uid}:{expected_gid}, mode {oct(expected_mode)}).")
        return fd

root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
var_fd = open_dir_nofollow(root_fd, "var")
lib_fd = open_dir_nofollow(var_fd, "lib")
run_fd = open_dir_nofollow(root_fd, "run")
etc_fd = open_dir_nofollow(root_fd, "etc")

# 1. Provision /var/lib/resolveflow hierarchy
rf_lib_fd = provision_node(lib_fd, "resolveflow", 0, 0, 0o755, "/var/lib/resolveflow")
signer_lib_fd = provision_node(rf_lib_fd, "signer", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer")

ks_fd = provision_node(signer_lib_fd, "keystore", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer/keystore")
enforce_direct_children(ks_fd, "/var/lib/resolveflow/signer/keystore", set())
os.close(ks_fd)

nonces_fd = provision_node(signer_lib_fd, "nonces", SIGNER_UID, SIGNER_GID, 0o700, "/var/lib/resolveflow/signer/nonces")
enforce_direct_children(nonces_fd, "/var/lib/resolveflow/signer/nonces", set())
os.close(nonces_fd)

# Enforce exact permitted direct-child set on pinned signer descriptor
enforce_direct_children(signer_lib_fd, "/var/lib/resolveflow/signer", {"keystore", "nonces"})
os.close(signer_lib_fd)

# Provision /var/lib/resolveflow/dispatcher (empty at initial provisioning)
dispatcher_lib_fd = provision_node(rf_lib_fd, "dispatcher", DISPATCHER_UID, DISPATCHER_GID, 0o700, "/var/lib/resolveflow/dispatcher")
enforce_direct_children(dispatcher_lib_fd, "/var/lib/resolveflow/dispatcher", set())
os.close(dispatcher_lib_fd)

# Enforce exact permitted direct-child set on pinned /var/lib/resolveflow descriptor
enforce_direct_children(rf_lib_fd, "/var/lib/resolveflow", {"signer", "dispatcher"})
os.close(rf_lib_fd)

os.close(lib_fd)
os.close(var_fd)

# 2. Provision /etc/resolveflow trust-root directory (Owner 0:0, Mode 0755)
# Does NOT generate, copy, install, replace, remove, parse, or read any key contents.
rf_etc_fd = provision_node(etc_fd, "resolveflow", 0, 0, 0o755, "/etc/resolveflow")
KNOWN_ANCHORS = {"oracle_public_key.pem", "preflight_verifier_public_key.pem", "tech_lead_public_key.pem"}
with os.scandir(rf_etc_fd) as it:
    for entry in it:
        if entry.name == "revoked_keys.jsonl":
            fail_closed(
                "Pre-existing '/etc/resolveflow/revoked_keys.jsonl' encountered "
                "without established DAC policy (unresolved packaging prerequisite)."
            )
        if entry.name not in KNOWN_ANCHORS:
            fail_closed(
                f"Directory '/etc/resolveflow' contains unreviewed entry: {entry.name}."
            )
os.close(rf_etc_fd)
os.close(etc_fd)

# 3. Provision /run/resolveflow hierarchy (mode strictly 02750 setgid per DEP02A-DAC-REALIGN)
rf_run_fd = provision_node(run_fd, "resolveflow", 0, 0, 0o755, "/run/resolveflow")

oracle_fd = provision_node(rf_run_fd, "oracle", SIGNER_UID, DISPATCHER_GID, 0o2750, "/run/resolveflow/oracle")
enforce_direct_children(oracle_fd, "/run/resolveflow/oracle", set())
os.close(oracle_fd)

signer_client_fd = provision_node(rf_run_fd, "signer_client", SIGNER_UID, CLIENTS_GID, 0o2750, "/run/resolveflow/signer_client")
enforce_direct_children(signer_client_fd, "/run/resolveflow/signer_client", set())
os.close(signer_client_fd)

dispatcher_client_fd = provision_node(rf_run_fd, "dispatcher_client", DISPATCHER_UID, CLIENTS_GID, 0o2750, "/run/resolveflow/dispatcher_client")
enforce_direct_children(dispatcher_client_fd, "/run/resolveflow/dispatcher_client", set())
os.close(dispatcher_client_fd)

# DEP02A-R4-01: Enforce exact permitted direct-child set on pinned runtime parent descriptor
enforce_direct_children(rf_run_fd, "/run/resolveflow", {"oracle", "signer_client", "dispatcher_client"})
os.close(rf_run_fd)

os.close(run_fd)
os.close(root_fd)

print("INFO: Descriptor-anchored boundary hierarchy verified.")
PY_APPLY_EOF

echo "========================================================================"
echo "SUCCESS: ResolveFlow principal and DAC provisioning complete (Realigned 02750)."
echo "No services have been started. No keys or sockets have been generated."
echo "========================================================================"
