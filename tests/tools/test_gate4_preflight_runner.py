"""Unit tests for Gate 4 Preflight Runner Core (Local-Only).

Workorder: G07-07B7-PREFLIGHT-GATE4-R10 (Revision R8)
Mode: LOCAL_CODE_AND_FAKE_ADAPTER_TESTS_ONLY; NO_RESOLVE_IMPORT_OR_CONNECTION.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from types import MappingProxyType
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import src.tools.gate4_preflight_runner as runner_module
from src.tools.gate4_preflight_runner import (
    BLACKLISTED_UIDS,
    AuditedResolveProxy,
    AuditLogDurabilityError,
    AuditLogIntegrityError,
    DisallowedMethodError,
    Gate4PreflightResult,
    Gate4PreflightRunner,
    HashChainedAuditLogger,
    PreflightVerificationError,
    canonical_json_bytes,
    verify_audit_log_chain,
)


class FakeMediaPoolItem:
    def __init__(self, name: str, media_id: str = "item_1"):
        self.name = name
        self.media_id = media_id

    def GetName(self) -> str:
        return self.name

    def GetMediaId(self) -> str:
        return self.media_id

    def GetClipProperty(self) -> Dict[str, Any]:
        return {"FPS": 24.0}


class FakeTimelineItem:
    def __init__(self, name: str = "clip1"):
        self.name = name

    def GetName(self) -> str:
        return self.name

    def GetDuration(self) -> int:
        return 240

    def GetStart(self) -> int:
        return 86400

    def GetEnd(self) -> int:
        return 86640

    def GetMediaPoolItem(self) -> FakeMediaPoolItem:
        return FakeMediaPoolItem(name=self.name)

    def Delete(self) -> bool:
        return False


class FakeTimeline:
    def __init__(self, name: str, uid: str = "tl_uid_123"):
        self.name = name
        self.uid = uid
        self.settings: Dict[str, Any] = {
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
            "timelineFrameRate": 24.0,
        }
        self.video_tracks = 1
        self.audio_tracks = 1
        self.v1_items: List[Any] = []
        self.a1_items: List[Any] = []
        self.start_frame = 86400
        self.end_frame = 86400
        self.markers: Dict[int, Any] = {}
        self.append_called = False

    def GetName(self) -> str:
        return self.name

    def GetUniqueId(self) -> str:
        return self.uid

    def GetSetting(self, key: str) -> Any:
        return self.settings.get(key)

    def SetSetting(self, key: str, val: Any) -> bool:
        self.settings[key] = val
        return True

    def GetTrackCount(self, track_type: str) -> int:
        if track_type == "video":
            return self.video_tracks
        elif track_type == "audio":
            return self.audio_tracks
        return 0

    def GetItemListInTrack(self, track_type: str, index: int) -> List[Any]:
        if track_type == "video" and index == 1:
            return list(self.v1_items)
        elif track_type == "audio" and index == 1:
            return list(self.a1_items)
        return []

    def GetStartFrame(self) -> int:
        return self.start_frame

    def GetEndFrame(self) -> int:
        return self.end_frame

    def GetMarkers(self) -> Dict[int, Any]:
        return dict(self.markers)

    def AppendToTimeline(self, items: Any) -> bool:
        self.append_called = True
        return True

    def InsertItems(self, items: Any) -> bool:
        self.append_called = True
        return True


class FakeMediaPool:
    def __init__(self, project: FakeProject):
        self.project = project
        self.items: List[FakeMediaPoolItem] = []

    def CreateEmptyTimeline(self, name: str) -> FakeTimeline:
        tl = FakeTimeline(name=name, uid=f"tl_uid_{name}")
        self.project.timelines.append(tl)
        return tl

    def ImportMedia(self, paths: List[str]) -> List[FakeMediaPoolItem]:
        created: List[FakeMediaPoolItem] = []
        for p in paths:
            item = FakeMediaPoolItem(name=os.path.basename(p), media_id=f"id_{len(self.items)+1}")
            self.items.append(item)
            created.append(item)
        return created


class FakeProject:
    def __init__(self, name: str, uid: str = "proj_uid_123"):
        self.name = name
        self.uid = uid
        self.settings: Dict[str, Any] = {
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
            "timelineFrameRate": 24.0,
        }
        self.timelines: List[FakeTimeline] = []
        self.media_pool = FakeMediaPool(self)

    def GetName(self) -> str:
        return self.name

    def GetUniqueId(self) -> str:
        return self.uid

    def GetSetting(self, key: str) -> Any:
        return self.settings.get(key)

    def SetSetting(self, key: str, val: Any) -> bool:
        self.settings[key] = val
        return True

    def GetMediaPool(self) -> FakeMediaPool:
        return self.media_pool

    def GetTimelineCount(self) -> int:
        return len(self.timelines)

    def GetTimelineByIndex(self, idx: int) -> Optional[FakeTimeline]:
        if 1 <= idx <= len(self.timelines):
            return self.timelines[idx - 1]
        return None

    def SetCurrentTimeline(self, tl: Any) -> bool:
        return True

    def DeleteTimeline(self, tl: Any) -> bool:
        return False


class FakeProjectManager:
    def __init__(self):
        self.projects: Dict[str, FakeProject] = {}
        self.current_project: Optional[FakeProject] = None

    def CreateProject(self, name: str) -> FakeProject:
        proj = FakeProject(name=name, uid=f"proj_uid_{name}")
        self.projects[name] = proj
        self.current_project = proj
        return proj

    def GetCurrentProject(self) -> Optional[FakeProject]:
        return self.current_project

    def DeleteProject(self, name: str) -> bool:
        return False


class FakeResolveApp:
    def __init__(self, version: str = "20.3.2.9"):
        self.version = version
        self.pm = FakeProjectManager()

    def GetVersionString(self) -> str:
        return self.version

    def GetProjectManager(self) -> FakeProjectManager:
        return self.pm

    def dispatch(self, action: Any) -> Any:
        raise RuntimeError("Dispatch must never be reached")


class TestGate4PreflightRunner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.test_dir.name, "execution.log")
        self.nonce = "A1B2C3D4E5F60718"

    def tearDown(self):
        self.test_dir.cleanup()

    def test_01_zero_resolve_sdk_import_on_module_load_and_run(self):
        """Module import and core execution cause zero Resolve SDK imports/connections."""
        check_code = (
            "import sys\n"
            "import src.tools.gate4_preflight_runner\n"
            "assert 'DaVinciResolveScript' not in sys.modules, 'DaVinciResolveScript was imported'\n"
            "assert 'fusionscript' not in sys.modules, 'fusionscript was imported'\n"
            "assert 'bmd' not in sys.modules, 'bmd was imported'\n"
            "print('CLEAN_IMPORT_VERIFIED')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", check_code],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"Clean import failed: {proc.stderr}")
        self.assertIn("CLEAN_IMPORT_VERIFIED", proc.stdout)

        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

    def test_02_start_record_durable_before_first_adapter_call(self):
        """Start record is durable before any fake adapter method is invoked."""
        observed_log_on_first_call = []

        class InterceptingResolveApp(FakeResolveApp):
            def GetProjectManager(self):
                with open(self._test_log_path, "r", encoding="utf-8") as f:
                    observed_log_on_first_call.append(f.readlines())
                return super().GetProjectManager()

        app = InterceptingResolveApp()
        app._test_log_path = self.log_path

        runner = Gate4PreflightRunner(
            resolve_app=app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

        self.assertEqual(len(observed_log_on_first_call), 1)
        lines = observed_log_on_first_call[0]
        self.assertGreaterEqual(len(lines), 1)
        start_rec = json.loads(lines[0])
        self.assertEqual(start_rec["seq"], 0)
        self.assertEqual(start_rec["event_type"], "AUDIT_START")
        self.assertEqual(start_rec["prev_hash"], "0" * 64)

    def test_03_nested_adapters_remain_wrapped_and_calls_recorded(self):
        """Nested adapter objects remain wrapped and all allowed calls record method, args, return, ts."""
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

        verification = verify_audit_log_chain(self.log_path)
        self.assertTrue(verification.valid, verification.error)
        self.assertGreater(verification.records_count, 10)

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]

        api_call_events = [r for r in records if r["event_type"] == "API_CALL"]
        methods_called = [r["details"]["method_name"] for r in api_call_events]
        self.assertIn("GetProjectManager", methods_called)
        self.assertIn("CreateProject", methods_called)
        self.assertIn("SetSetting", methods_called)
        self.assertIn("CreateEmptyTimeline", methods_called)
        self.assertIn("GetItemListInTrack", methods_called)

        for r in api_call_events:
            d = r["details"]
            self.assertIn("object_role", d)
            self.assertIn("method_name", d)
            self.assertIn("arguments", d)
            self.assertIn("return_type", d)
            self.assertIn("call_index", d)

    def test_04_hash_chain_verifier_accepts_valid_and_rejects_tampering(self):
        """Hash-chain verifier accepts a complete log and rejects tampering, missing, reordered, torn."""
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

        # 1. Valid log passes
        v = verify_audit_log_chain(self.log_path)
        self.assertTrue(v.valid)
        self.assertIsNone(v.error)

        with open(self.log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 2. Tampered content fails
        tampered_lines = list(lines)
        rec = json.loads(tampered_lines[1])
        rec["message"] = "tampered content"
        tampered_lines[1] = json.dumps(rec) + "\n"
        tampered_path = os.path.join(self.test_dir.name, "tampered.log")
        with open(tampered_path, "w", encoding="utf-8") as f:
            f.writelines(tampered_lines)
        v_tamper = verify_audit_log_chain(tampered_path)
        self.assertFalse(v_tamper.valid)
        self.assertIn("hash mismatch", v_tamper.error.lower())

        # 3. Missing record (sequence gap) fails
        gap_lines = [lines[0]] + lines[2:]
        gap_path = os.path.join(self.test_dir.name, "gap.log")
        with open(gap_path, "w", encoding="utf-8") as f:
            f.writelines(gap_lines)
        v_gap = verify_audit_log_chain(gap_path)
        self.assertFalse(v_gap.valid)
        self.assertIn("sequence", v_gap.error.lower())

        # 4. Reordered records fail
        reorder_lines = [lines[0], lines[2], lines[1]] + lines[3:]
        reorder_path = os.path.join(self.test_dir.name, "reorder.log")
        with open(reorder_path, "w", encoding="utf-8") as f:
            f.writelines(reorder_lines)
        v_reorder = verify_audit_log_chain(reorder_path)
        self.assertFalse(v_reorder.valid)

        # 5. Missing terminal record fails
        no_term_lines = lines[:-1]
        no_term_path = os.path.join(self.test_dir.name, "no_term.log")
        with open(no_term_path, "w", encoding="utf-8") as f:
            f.writelines(no_term_lines)
        v_no_term = verify_audit_log_chain(no_term_path)
        self.assertFalse(v_no_term.valid)
        self.assertIn("terminal", v_no_term.error.lower())

        # 6. Torn tail fails
        torn_lines = lines[:-1] + [lines[-1][: len(lines[-1]) // 2]]
        torn_path = os.path.join(self.test_dir.name, "torn.log")
        with open(torn_path, "w", encoding="utf-8") as f:
            f.writelines(torn_lines)
        v_torn = verify_audit_log_chain(torn_path)
        self.assertFalse(v_torn.valid)

    def test_05_audit_sink_durability_failure_fails_closed(self):
        """Audit sink write/fsync failures prevent a success result."""
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )

        with patch("os.fsync", side_effect=OSError(errno.EIO, "Disk I/O error")):
            with self.assertRaises((AuditLogDurabilityError, OSError)):
                runner.run()

    def test_06_unapproved_methods_blocked_and_audited_never_invoked(self):
        """Unapproved dispatch/insertion/delete methods are logged as blocked attempts and never invoked."""
        fake_app = FakeResolveApp()
        logger = HashChainedAuditLogger(self.log_path, runner_script_path=__file__)

        proxy_app = AuditedResolveProxy(fake_app, logger=logger, object_role="Resolve")

        with self.assertRaises(DisallowedMethodError):
            proxy_app.dispatch({"action": "INSERT_CLIP"})

        pm_proxy = proxy_app.GetProjectManager()
        with self.assertRaises(DisallowedMethodError):
            pm_proxy.DeleteProject("RF_TRIAL")

        proj_proxy = pm_proxy.CreateProject(f"RF_TRIAL_{self.nonce}")
        mp_proxy = proj_proxy.GetMediaPool()
        tl_proxy = mp_proxy.CreateEmptyTimeline(f"RF_TRIAL_TL_{self.nonce}")

        with self.assertRaises(DisallowedMethodError):
            tl_proxy.AppendToTimeline(["item1"])

        with self.assertRaises(DisallowedMethodError):
            tl_proxy.InsertItems(["item1"])

        raw_tl = fake_app.pm.projects[f"RF_TRIAL_{self.nonce}"].timelines[0]
        self.assertFalse(raw_tl.append_called)

        logger.close_failure(reason="Blocked call test complete")

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]

        blocked_events = [r for r in records if r["event_type"] == "BLOCKED_METHOD_ATTEMPT"]
        self.assertGreaterEqual(len(blocked_events), 3)
        blocked_methods = [r["details"]["method_name"] for r in blocked_events]
        self.assertIn("dispatch", blocked_methods)
        self.assertIn("DeleteProject", blocked_methods)
        self.assertIn("AppendToTimeline", blocked_methods)

    def test_07_dynamic_counters_derived_from_observed_calls_no_constant_zeros(self):
        """Dynamic counters are derived from observed calls, and there are no constant-zero success counters."""
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

        self.assertGreater(res.counters["total_calls"], 10)
        self.assertGreater(res.counters["allowed_calls"], 10)
        self.assertEqual(res.counters["blocked_calls"], 0)
        self.assertEqual(res.counters["clip_insertions_attempted"], 0)
        self.assertEqual(res.counters["dispatch_calls_attempted"], 0)

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        term_rec = records[-1]
        self.assertEqual(term_rec["event_type"], "AUDIT_TERMINAL_SUCCESS")
        self.assertEqual(term_rec["details"]["total_calls"], res.counters["total_calls"])
        self.assertEqual(term_rec["details"]["allowed_calls"], res.counters["allowed_calls"])

    def test_08_raw_object_escape_prevention_finding_g4_r10_01_and_r2_01(self):
        """Findings G4-R10-01 & G4-R10-R2-01: Raw adapter objects cannot escape via attributes or registry."""
        fake_app = FakeResolveApp()
        logger = HashChainedAuditLogger(self.log_path, runner_script_path=__file__)
        proxy_app = AuditedResolveProxy(fake_app, logger=logger, object_role="Resolve")

        # 1. Module surface does NOT expose any raw target registry
        self.assertFalse(hasattr(runner_module, "_PROXY_STATE_REGISTRY"))

        # 2. Direct access to private attribute _target is blocked
        with self.assertRaises(AttributeError):
            _ = proxy_app._target

        with self.assertRaises(AttributeError):
            _ = proxy_app._logger

        # 3. Objects nested in list results (e.g. GetItemListInTrack) are wrapped in AuditedResolveProxy
        class ItemTrackTimeline(FakeTimeline):
            def GetItemListInTrack(self, track_type: str, index: int) -> List[Any]:
                return [FakeTimelineItem("clip1"), FakeTimelineItem("clip2")]

        tl_raw = ItemTrackTimeline("test_tl")
        tl_proxy = AuditedResolveProxy(tl_raw, logger=logger, object_role="Timeline")
        items = tl_proxy.GetItemListInTrack("video", 1)
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 2)
        for it in items:
            # Confirm item is an audited proxy closure
            self.assertEqual(it.GetName(), "clip1" if it == items[0] else "clip2")
            with self.assertRaises(DisallowedMethodError):
                it.Delete()

        # 4. Arbitrary unclassified return objects are wrapped in UnknownSubObject and fail closed
        class SneakyResolveApp:
            def GetVersionString(self):
                return "20.3.2.9"

            def GetProjectManager(self):
                class SneakyPM:
                    def CreateProject(self, name):
                        class UnwrappedObject:
                            def execute_arbitrary(self):
                                return "escaped"
                        return UnwrappedObject()
                return SneakyPM()

        sneaky_proxy = AuditedResolveProxy(SneakyResolveApp(), logger=logger, object_role="Resolve")
        pm = sneaky_proxy.GetProjectManager()
        proj = pm.CreateProject("test")
        with self.assertRaises(DisallowedMethodError):
            proj.execute_arbitrary()

        logger.close_failure(reason="Escape prevention test complete")

    def test_09_successful_fake_preflight_returns_structured_evidence_without_live_claims(self):
        """Successful fake preflight returns structured evidence object without claiming live observations."""
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
            operator_attestation="Test operator statement",
            screenshot_path="/tmp/fake_screenshot.png",
            screenshot_sha256="deadbeef" * 8,
        )
        res = runner.run()
        self.assertTrue(res.success)
        self.assertIsInstance(res, Gate4PreflightResult)

        self.assertFalse(res.is_live_resolve_session)
        self.assertEqual(res.ready_to_execute, False)
        self.assertEqual(
            res.zero_dispatch_scope,
            "NO_DISPATCH_CAPABLE_CALL_OBSERVED_THROUGH_AUDITED_ADAPTER",
        )

        for p_id in ["1.1", "1.2", "1.3", "1.4", "2.1", "3.1", "4.1", "5.1", "6.1", "7.1", "8.1", "8.2", "9.1", "10.1"]:
            self.assertIn(p_id, res.provenance_by_predicate)

    def test_10_negative_predicate_tests_finding_g4_r10_02(self):
        """Finding G4-R10-02: Fails closed on any violated Gate 4 predicate."""
        # 1. Project name mismatch
        class BadProjNameApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                pm.CreateProject = lambda name: orig_create("WRONG_NAME")
                return pm

        r = Gate4PreflightRunner(BadProjNameApp(), self.nonce, os.path.join(self.test_dir.name, "bad_name.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 2. Blacklisted project UID & Finding G4-R10-R7-01: BLACKLISTED_UIDS is immutable frozenset
        self.assertIsInstance(runner_module.BLACKLISTED_UIDS, frozenset)
        with self.assertRaises(AttributeError):
            runner_module.BLACKLISTED_UIDS.clear()  # type: ignore

        class BlacklistedProjUidApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    p.uid = "0ee71404-6405-47ca-89e1-aaeaac142398"
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(BlacklistedProjUidApp(), self.nonce, os.path.join(self.test_dir.name, "bl_p.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # Proving monkeypatching / rebinding module blacklist globals cannot cause blacklisted UID to pass (Finding G4-R10-R8-01 Fix)
        with patch.object(runner_module, "BLACKLISTED_UIDS", frozenset()), \
             patch.object(runner_module, "_CANONICAL_INCIDENT_BLACKLIST", frozenset()):
            r_patched = Gate4PreflightRunner(BlacklistedProjUidApp(), self.nonce, os.path.join(self.test_dir.name, "bl_p_patched.log"))
            with self.assertRaises(PreflightVerificationError):
                r_patched.run()

        # 3. Blacklisted timeline UID
        class BlacklistedTlUidApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_mp = p.GetMediaPool
                    def _mp():
                        mp = orig_mp()
                        orig_tl = mp.CreateEmptyTimeline
                        def _ctl(tname):
                            tl = orig_tl(tname)
                            tl.uid = "e4e5a587-661d-4854-8f37-8a279d3f2ee1"
                            return tl
                        mp.CreateEmptyTimeline = _ctl
                        return mp
                    p.GetMediaPool = _mp
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(BlacklistedTlUidApp(), self.nonce, os.path.join(self.test_dir.name, "bl_tl.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 4. Wrong resolution (e.g. 3840x2160)
        class WrongResApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    p.GetSetting = lambda k: "3840" if k == "timelineResolutionWidth" else p.settings.get(k)
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(WrongResApp(), self.nonce, os.path.join(self.test_dir.name, "wrong_res.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 5. Wrong FPS (e.g. 29.97)
        class WrongFpsApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    p.GetSetting = lambda k: 29.97 if k == "timelineFrameRate" else p.settings.get(k)
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(WrongFpsApp(), self.nonce, os.path.join(self.test_dir.name, "wrong_fps.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 6. Video track count != 1 (e.g. 2)
        class BadTracksApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_mp = p.GetMediaPool
                    def _mp():
                        mp = orig_mp()
                        orig_tl = mp.CreateEmptyTimeline
                        def _ctl(tname):
                            tl = orig_tl(tname)
                            tl.video_tracks = 2
                            return tl
                        mp.CreateEmptyTimeline = _ctl
                        return mp
                    p.GetMediaPool = _mp
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(BadTracksApp(), self.nonce, os.path.join(self.test_dir.name, "bad_tracks.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 7. Non-empty timeline (v1_items populated)
        class NonEmptyTlApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_mp = p.GetMediaPool
                    def _mp():
                        mp = orig_mp()
                        orig_tl = mp.CreateEmptyTimeline
                        def _ctl(tname):
                            tl = orig_tl(tname)
                            tl.v1_items = [FakeTimelineItem("stray_clip")]
                            return tl
                        mp.CreateEmptyTimeline = _ctl
                        return mp
                    p.GetMediaPool = _mp
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(NonEmptyTlApp(), self.nonce, os.path.join(self.test_dir.name, "non_empty.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 8. Start frame != end frame
        class BadFramesApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_mp = p.GetMediaPool
                    def _mp():
                        mp = orig_mp()
                        orig_tl = mp.CreateEmptyTimeline
                        def _ctl(tname):
                            tl = orig_tl(tname)
                            tl.end_frame = 86450
                            return tl
                        mp.CreateEmptyTimeline = _ctl
                        return mp
                    p.GetMediaPool = _mp
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(BadFramesApp(), self.nonce, os.path.join(self.test_dir.name, "bad_frames.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

        # 9. Non-zero markers
        class MarkersApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_mp = p.GetMediaPool
                    def _mp():
                        mp = orig_mp()
                        orig_tl = mp.CreateEmptyTimeline
                        def _ctl(tname):
                            tl = orig_tl(tname)
                            tl.markers = {86400: {"color": "Blue", "name": "M1"}}
                            return tl
                        mp.CreateEmptyTimeline = _ctl
                        return mp
                    p.GetMediaPool = _mp
                    return p
                pm.CreateProject = _create
                return pm

        r = Gate4PreflightRunner(MarkersApp(), self.nonce, os.path.join(self.test_dir.name, "markers.log"))
        with self.assertRaises(PreflightVerificationError):
            r.run()

    def test_11_verifier_derived_counters_reconciliation_finding_g4_r10_03_and_r2_02(self):
        """Findings G4-R10-03 & G4-R10-R2-02: Verifier requires complete terminal counters and rejects forgeries."""
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

        with open(self.log_path, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]

        # 1. Forge terminal record details (claim total_calls: 0 while real calls > 0)
        forged_records = json.loads(json.dumps(records))
        last_rec = forged_records[-1]
        last_rec["details"]["total_calls"] = 0

        p = {
            "seq": last_rec["seq"],
            "prev_hash": last_rec["prev_hash"],
            "timestamp_utc": last_rec["timestamp_utc"],
            "event_type": last_rec["event_type"],
            "message": last_rec["message"],
            "details": last_rec["details"],
        }
        last_rec["record_hash"] = hashlib.sha256(canonical_json_bytes(p)).hexdigest()

        forged_path = os.path.join(self.test_dir.name, "forged_term.log")
        with open(forged_path, "w", encoding="utf-8") as f:
            for r in forged_records:
                f.write(json.dumps(r) + "\n")

        v_forged = verify_audit_log_chain(forged_path)
        self.assertFalse(v_forged.valid)
        self.assertIn("does not match derived", v_forged.error)

        # 2. Terminal record missing a mandatory summary field (e.g. omit 'dispatch_calls_attempted')
        missing_field_records = json.loads(json.dumps(records))
        last_rec = missing_field_records[-1]
        del last_rec["details"]["dispatch_calls_attempted"]

        p_mf = {
            "seq": last_rec["seq"],
            "prev_hash": last_rec["prev_hash"],
            "timestamp_utc": last_rec["timestamp_utc"],
            "event_type": last_rec["event_type"],
            "message": last_rec["message"],
            "details": last_rec["details"],
        }
        last_rec["record_hash"] = hashlib.sha256(canonical_json_bytes(p_mf)).hexdigest()
        mf_path = os.path.join(self.test_dir.name, "missing_field.log")
        with open(mf_path, "w", encoding="utf-8") as f:
            for r in missing_field_records:
                f.write(json.dumps(r) + "\n")

        v_mf = verify_audit_log_chain(mf_path)
        self.assertFalse(v_mf.valid)
        self.assertIn("missing mandatory summary field", v_mf.error)

        # 3. Terminal record followed by additional events (multiple terminal records)
        multi_term_records = json.loads(json.dumps(records))
        # Append an extra event after terminal
        extra_seq = len(multi_term_records)
        prev_h = multi_term_records[-1]["record_hash"]
        extra_rec = {
            "seq": extra_seq,
            "prev_hash": prev_h,
            "timestamp_utc": "2026-11-04T16:00:00Z",
            "event_type": "AUDIT_TERMINAL_SUCCESS",
            "message": "Extra terminal",
            "details": multi_term_records[-1]["details"],
        }
        extra_rec["record_hash"] = hashlib.sha256(canonical_json_bytes(dict(extra_rec))).hexdigest()
        multi_term_records.append(extra_rec)

        mt_path = os.path.join(self.test_dir.name, "multi_term.log")
        with open(mt_path, "w", encoding="utf-8") as f:
            for r in multi_term_records:
                f.write(json.dumps(r) + "\n")

        v_mt = verify_audit_log_chain(mt_path)
        self.assertFalse(v_mt.valid)
        self.assertIn("encountered at line", v_mt.error)

        # 4. Omit AUDIT_START at seq 0
        no_start_records = json.loads(json.dumps(records))
        no_start_records[0]["event_type"] = "API_CALL"
        p0 = {
            "seq": no_start_records[0]["seq"],
            "prev_hash": no_start_records[0]["prev_hash"],
            "timestamp_utc": no_start_records[0]["timestamp_utc"],
            "event_type": no_start_records[0]["event_type"],
            "message": no_start_records[0]["message"],
            "details": no_start_records[0]["details"],
        }
        no_start_records[0]["record_hash"] = hashlib.sha256(canonical_json_bytes(p0)).hexdigest()
        no_start_path = os.path.join(self.test_dir.name, "no_start.log")
        with open(no_start_path, "w", encoding="utf-8") as f:
            for r in no_start_records:
                f.write(json.dumps(r) + "\n")

        v_no_start = verify_audit_log_chain(no_start_path)
        self.assertFalse(v_no_start.valid)
        self.assertIn("must be AUDIT_START", v_no_start.error)

    def test_12_insecure_audit_directory_fails_closed_finding_g4_r10_04_and_r2_03(self):
        """Findings G4-R10-04 & G4-R10-R2-03: Insecure audit directory mode or non-0700 fails closed."""
        fake_app = FakeResolveApp()
        insecure_dir = os.path.join(self.test_dir.name, "insecure_dir")
        log_path = os.path.join(insecure_dir, "execution.log")

        # 1. os.chmod raising OSError must fail immediately without calling adapter
        with patch("os.chmod", side_effect=OSError(errno.EPERM, "Operation not permitted")):
            with self.assertRaises(AuditLogDurabilityError):
                Gate4PreflightRunner(fake_app, self.nonce, log_path)

        # 2. Mode not exactly 0700 (e.g. 0500, no group/world bits but missing owner write)
        class Mode0500StatResult:
            st_mode = 0o040500  # Directory with 0500 mode
            st_uid = os.getuid() if hasattr(os, "getuid") else 1000

        with patch("os.stat", return_value=Mode0500StatResult()):
            with self.assertRaises(AuditLogDurabilityError):
                Gate4PreflightRunner(fake_app, self.nonce, log_path)

        # 3. Mode with group/world bits (e.g. 0777)
        class Mode0777StatResult:
            st_mode = 0o040777  # Directory with 0777 mode
            st_uid = os.getuid() if hasattr(os, "getuid") else 1000

        with patch("os.stat", return_value=Mode0777StatResult()):
            with self.assertRaises(AuditLogDurabilityError):
                Gate4PreflightRunner(fake_app, self.nonce, log_path)

    def test_13_unforgeable_proxy_identity_and_registration_surface_finding_g4_r10_r4_01(self):
        """Findings G4-R10-R3-01, G4-R10-R4-01 & G4-R10-R5-01: Marker construction, logger binding, and role policy fail closed."""
        fake_app = FakeResolveApp()
        logger = HashChainedAuditLogger(self.log_path, runner_script_path=__file__)

        # 1. Module surface exposes NO marker class, construction key, or registration API
        self.assertFalse(hasattr(runner_module, "_PROXY_CONSTRUCTION_KEY"))
        self.assertFalse(hasattr(runner_module, "_AuditedProxyMarker"))
        self.assertFalse(hasattr(runner_module, "_InternalAuditedMarker"))
        self.assertFalse(hasattr(runner_module, "register_proxy"))
        self.assertFalse(hasattr(logger, "register_proxy"))
        self.assertFalse(hasattr(logger, "is_registered_proxy"))

        # 2. Extract base marker from a legitimate proxy and prove external subclassing is strictly forbidden
        legit_proxy = AuditedResolveProxy(fake_app, logger=logger, object_role="Resolve")
        base_marker_cls = type(legit_proxy).__bases__[0]
        self.assertEqual(base_marker_cls.__name__, "_InternalAuditedMarker")

        with self.assertRaises(TypeError) as ctx:
            class ForgedSubclass(base_marker_cls):  # type: ignore
                pass
        self.assertIn("strictly forbidden", str(ctx.exception))

        # 3. Finding G4-R10-R5-01: Proxy factory rejects caller-supplied allowed_methods
        with self.assertRaises(TypeError):
            AuditedResolveProxy(fake_app, logger=logger, object_role="Resolve", allowed_methods={"dispatch"})  # type: ignore

        # 4. Finding G4-R10-R6-01: Role allowlist mapping is strictly immutable MappingProxyType
        self.assertIsInstance(runner_module.ALLOWED_METHODS_BY_ROLE, MappingProxyType)
        with self.assertRaises(TypeError):
            runner_module.ALLOWED_METHODS_BY_ROLE["Timeline"] = frozenset({"AppendToTimeline"})  # type: ignore
        with self.assertRaises(TypeError):
            del runner_module.ALLOWED_METHODS_BY_ROLE["Timeline"]  # type: ignore
        with self.assertRaises(AttributeError):
            runner_module.ALLOWED_METHODS_BY_ROLE.clear()  # type: ignore

        # Proving attempts to replace the module mapping cannot authorize a blocked method
        with patch.object(runner_module, "ALLOWED_METHODS_BY_ROLE", {"Timeline": frozenset({"AppendToTimeline"})}):
            tl_tampered = AuditedResolveProxy(FakeTimeline("tl_tampered"), logger=logger, object_role="Timeline")
            with self.assertRaises(DisallowedMethodError):
                tl_tampered.AppendToTimeline()

        # 5. Finding G4-R10-R5-01: Cross-logger proxy returned directly or in container must not escape current log
        other_log_path = os.path.join(self.test_dir.name, "other_execution.log")
        other_logger = HashChainedAuditLogger(other_log_path, runner_script_path=__file__)

        class ItemWithExtra:
            def __init__(self, name: str = "item1"):
                self.name = name

            def GetName(self) -> str:
                return self.name

            def execute_arbitrary(self) -> str:
                return "CROSS_LOGGER_ESCAPE"

        foreign_proxy = AuditedResolveProxy(ItemWithExtra("foreign_clip"), logger=other_logger, object_role="TimelineItem")

        # Nested cross-logger proxy in container (GetItemListInTrack)
        class CrossLoggerTimeline(FakeTimeline):
            def GetItemListInTrack(self, track_type: str, index: int) -> List[Any]:
                return [foreign_proxy]

        tl_raw = CrossLoggerTimeline("cross_tl")
        tl_proxy = AuditedResolveProxy(tl_raw, logger=logger, object_role="Timeline")
        wrapped_items = tl_proxy.GetItemListInTrack("video", 1)

        self.assertEqual(len(wrapped_items), 1)
        item0 = wrapped_items[0]
        # Calling GetName() must record to current logger, not just other_logger
        self.assertEqual(item0.GetName(), "foreign_clip")
        current_names = [r["details"]["method_name"] for r in logger.records if r["event_type"] == "API_CALL"]
        self.assertIn("GetName", current_names)

        # Calling unapproved method fails closed and records BLOCKED_METHOD_ATTEMPT in current logger
        with self.assertRaises(DisallowedMethodError):
            item0.execute_arbitrary()
        self.assertGreater(logger.blocked_calls, 0)

        # 6. Finding G4-R10-R5-01: Role-mismatched proxy returned from method must be re-wrapped with expected role
        class MismatchedRoleTimeline(FakeTimeline):
            def GetItemListInTrack(self, track_type: str, index: int) -> List[Any]:
                # Returns a proxy created as Project where TimelineItem is expected
                fake_p = FakeProject("mismatched_proj")
                return [AuditedResolveProxy(fake_p, logger=logger, object_role="Project")]

        mm_raw = MismatchedRoleTimeline("mm_tl")
        mm_proxy = AuditedResolveProxy(mm_raw, logger=logger, object_role="Timeline")
        mm_items = mm_proxy.GetItemListInTrack("video", 1)
        self.assertEqual(len(mm_items), 1)
        # Calling Project-allowed method (e.g. SetSetting or GetMediaPool) fails closed because role is TimelineItem
        with self.assertRaises(DisallowedMethodError):
            mm_items[0].GetMediaPool()

        # 7. Forged imposter object named _ClosureAuditedProxy cannot be registered and fails closed
        class _ClosureAuditedProxy:
            def __init__(self, name: str = "imposter_target"):
                self.name = name

            def GetName(self) -> str:
                return self.name

            def execute_arbitrary(self) -> str:
                return "UNAUDITED_ESCAPE"

            def dispatch(self, action: Any) -> Any:
                return "UNAUDITED_DISPATCH"

        raw_imposter = _ClosureAuditedProxy()
        self.assertEqual(type(raw_imposter).__name__, "_ClosureAuditedProxy")

        # Imposter returned nested in container (e.g. GetItemListInTrack returning list of imposters)
        class ImposterTimeline(FakeTimeline):
            def GetItemListInTrack(self, track_type: str, index: int) -> List[Any]:
                return [_ClosureAuditedProxy("clip_imposter_1"), _ClosureAuditedProxy("clip_imposter_2")]

        tl_imposter_raw = ImposterTimeline("test_tl")
        tl_imposter_proxy = AuditedResolveProxy(tl_imposter_raw, logger=logger, object_role="Timeline")
        items = tl_imposter_proxy.GetItemListInTrack("video", 1)

        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 2)
        for idx, it in enumerate(items, start=1):
            self.assertEqual(it.GetName(), f"clip_imposter_{idx}")
            with self.assertRaises(DisallowedMethodError):
                it.execute_arbitrary()
            with self.assertRaises(DisallowedMethodError):
                it.dispatch("attack")

        # Imposter returned nested in dict (e.g. via GetMarkers returning dict of imposters)
        class DictImposterTimeline(FakeTimeline):
            def GetMarkers(self) -> Dict[str, Any]:
                return {"marker_1": _ClosureAuditedProxy("dict_val")}

        dt_raw = DictImposterTimeline("dt_tl")
        dt_proxy = AuditedResolveProxy(dt_raw, logger=logger, object_role="Timeline")
        markers = dt_proxy.GetMarkers()
        self.assertIn("marker_1", markers)
        dict_wrapped = markers["marker_1"]
        with self.assertRaises(DisallowedMethodError):
            dict_wrapped.execute_arbitrary()

        # Imposter returned as unclassified object fails closed with UnknownSubObject role
        class SneakyAppReturningDirectImposter:
            def GetProjectManager(self) -> Any:
                return _ClosureAuditedProxy("pm_imposter")

        sneaky_app = SneakyAppReturningDirectImposter()
        sneaky_proxy = AuditedResolveProxy(sneaky_app, logger=logger, object_role="Resolve")
        pm_wrapped = sneaky_proxy.GetProjectManager()

        with self.assertRaises(DisallowedMethodError):
            pm_wrapped.execute_arbitrary()

        # Verify blocked method events recorded in audit log
        logger.close_failure(reason="Imposter and foreign proxy test complete")
        other_logger.close_failure(reason="Foreign logger test complete")
        v = verify_audit_log_chain(self.log_path)
        self.assertTrue(v.valid, v.error)
        self.assertGreater(logger.blocked_calls, 0)
        self.assertGreater(logger.dispatch_calls_attempted, 0)

    def test_14_rfc8785_canonical_json_known_answer_vectors_finding_g4_r10_r7_02(self):
        """Findings G4-R10-R7-02, G4-R10-R8-02, G4-R10-R9-01: RFC 8785 / JCS canonicalization matches standard ECMAScript vectors."""
        # 1. Integral floats format as integers without decimal point
        self.assertEqual(canonical_json_bytes(24.0), b"24")
        self.assertEqual(canonical_json_bytes(1920.0), b"1920")
        self.assertEqual(canonical_json_bytes(1080.0), b"1080")
        self.assertEqual(canonical_json_bytes(0.0), b"0")

        # 2. Negative zero formats as "0" (ECMAScript Number.toString)
        self.assertEqual(canonical_json_bytes(-0.0), b"0")

        # 3. Exponent formatting & thresholds
        self.assertEqual(canonical_json_bytes(1e-6), b"0.000001")
        self.assertEqual(canonical_json_bytes(1e-7), b"1e-7")
        self.assertEqual(canonical_json_bytes(1e20), b"100000000000000000000")
        self.assertEqual(canonical_json_bytes(1e21), b"1e+21")
        self.assertEqual(canonical_json_bytes(-1e21), b"-1e+21")
        self.assertEqual(canonical_json_bytes(1.23e30), b"1.23e+30")
        self.assertEqual(canonical_json_bytes(1e-10), b"1e-10")

        # 4. Large integral binary64 shortest round-trip representation (Finding G4-R10-R8-02 Fix)
        # 1000000000000000100.0 has exact binary64 int value 1000000000000000128, but shortest decimal is 1000000000000000100
        self.assertEqual(canonical_json_bytes(1000000000000000100.0), b"1000000000000000100")

        # 5. Fixed-decimal notation with leading fractional zeros in 1e-4 through 1e-3 (Finding G4-R10-R9-01 Fix)
        self.assertEqual(canonical_json_bytes(0.0001), b"0.0001")
        self.assertEqual(canonical_json_bytes(0.0002), b"0.0002")
        self.assertEqual(canonical_json_bytes(0.0009), b"0.0009")
        self.assertEqual(canonical_json_bytes(0.001), b"0.001")
        self.assertEqual(canonical_json_bytes(0.002), b"0.002")
        self.assertEqual(canonical_json_bytes(0.000123), b"0.000123")
        self.assertEqual(canonical_json_bytes(0.00001), b"0.00001")
        self.assertEqual(canonical_json_bytes(0.0000123), b"0.0000123")

        # 6. Safe integer boundary around 2^53 (9007199254740991) and boundary near 1e21 (Finding G4-R10-R8-02 Fix)
        self.assertEqual(canonical_json_bytes(9007199254740991), b"9007199254740991")
        self.assertEqual(canonical_json_bytes(-9007199254740991), b"-9007199254740991")
        self.assertEqual(canonical_json_bytes(9007199254740992.0), b"9007199254740992")
        with self.assertRaises(ValueError):
            canonical_json_bytes(9007199254740992)
        with self.assertRaises(ValueError):
            canonical_json_bytes(-9007199254740992)

        # Boundary values near 1e21: 1e21 - 1e5 = 9.999999999999999e+20
        self.assertEqual(canonical_json_bytes(1e21 - 1e5), b"999999999999999900000")

        # 7. Non-string dictionary keys rejected (Finding G4-R10-R9-01 Fix)
        with self.assertRaises(TypeError):
            canonical_json_bytes({1: "int_key"})
        with self.assertRaises(TypeError):
            canonical_json_bytes({True: "bool_key"})
        with self.assertRaises(TypeError):
            canonical_json_bytes({None: "none_key"})
        with self.assertRaises(TypeError):
            canonical_json_bytes({1.5: "float_key"})

        # 8. UTF-16 code unit key ordering
        # U+1F600 (surrogate pair \uD83D\uDE00) must sort before U+E000 (\uE000)
        obj = {"\U0001f600": 1, "\ue000": 2}
        expected_bytes = b'{"\xf0\x9f\x98\x80":1,"\xee\x80\x80":2}'
        self.assertEqual(canonical_json_bytes(obj), expected_bytes)

        # 9. Composite payload matching preflight profile values
        profile_payload = {
            "fps": 24.0,
            "width": 1920,
            "height": 1080,
            "zero": -0.0,
            "clips": []
        }
        self.assertEqual(
            canonical_json_bytes(profile_payload),
            b'{"clips":[],"fps":24,"height":1080,"width":1920,"zero":0}'
        )

        # 10. Audit log chain verification with conforming RFC 8785 hashes
        fake_app = FakeResolveApp()
        runner = Gate4PreflightRunner(
            resolve_app=fake_app,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res = runner.run()
        self.assertTrue(res.success)

        verification = verify_audit_log_chain(self.log_path)
        self.assertTrue(verification.valid, verification.error)
        self.assertGreater(verification.records_count, 10)

        # Verify each individual line hash matches canonical_json_bytes digest
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                rec_hash = rec["record_hash"]
                payload_to_hash = {
                    "seq": rec["seq"],
                    "prev_hash": rec["prev_hash"],
                    "timestamp_utc": rec["timestamp_utc"],
                    "event_type": rec["event_type"],
                    "message": rec["message"],
                    "details": rec["details"],
                }
                expected_hash = hashlib.sha256(canonical_json_bytes(payload_to_hash)).hexdigest()
                self.assertEqual(rec_hash, expected_hash)

        # 11. Finding G4-R10-R9-01: Direct and nested adapter returns with integer key 1 and string key "1" fail closed with ValueError
        with self.assertRaises(ValueError):
            runner_module.safe_serialize_val({1: "int_one", "1": "str_one"})
        with self.assertRaises(ValueError):
            runner_module.safe_serialize_val({"data": [{1: "int_one", "1": "str_one"}]})

        class CollidingDirectReturnApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    p.GetSetting = lambda k: {1: "int_one", "1": "str_one"}
                    return p
                pm.CreateProject = _create
                return pm

        col_dir_log = os.path.join(self.test_dir.name, "col_dir.log")
        r_dir = Gate4PreflightRunner(CollidingDirectReturnApp(), self.nonce, col_dir_log)
        with self.assertRaises(ValueError):
            r_dir.run()

        # Finding G4-R10-R10-01 Fix: Verify direct collision failure log records the call and passes verification
        v_dir = verify_audit_log_chain(col_dir_log)
        self.assertTrue(v_dir.valid, v_dir.error)
        self.assertEqual(v_dir.terminal_event_type, "AUDIT_TERMINAL_FAILURE")
        self.assertGreater(v_dir.measured_total_calls, 0)
        self.assertGreater(v_dir.measured_allowed_calls, 0)
        with open(col_dir_log, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
            ev_types = [entry["event_type"] for entry in lines]
            self.assertIn("API_CALL_SERIALIZATION_FAILURE", ev_types)
            self.assertEqual(lines[-1]["event_type"], "AUDIT_TERMINAL_FAILURE")

        class CollidingNestedReturnApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    p.GetSetting = lambda k: [{"nested": {1: "int_one", "1": "str_one"}}]
                    return p
                pm.CreateProject = _create
                return pm

        col_nest_log = os.path.join(self.test_dir.name, "col_nest.log")
        r_nest = Gate4PreflightRunner(CollidingNestedReturnApp(), self.nonce, col_nest_log)
        with self.assertRaises(ValueError):
            r_nest.run()

        # Finding G4-R10-R10-01 Fix: Verify nested collision failure log records the call and passes verification
        v_nest = verify_audit_log_chain(col_nest_log)
        self.assertTrue(v_nest.valid, v_nest.error)
        self.assertEqual(v_nest.terminal_event_type, "AUDIT_TERMINAL_FAILURE")
        self.assertGreater(v_nest.measured_total_calls, 0)
        self.assertGreater(v_nest.measured_allowed_calls, 0)
        with open(col_nest_log, "r", encoding="utf-8") as f:
            lines_nest = [json.loads(line) for line in f]
            ev_types_nest = [entry["event_type"] for entry in lines_nest]
            self.assertIn("API_CALL_SERIALIZATION_FAILURE", ev_types_nest)
            self.assertEqual(lines_nest[-1]["event_type"], "AUDIT_TERMINAL_FAILURE")

        # 12. Finding G4-R10-R11-01 Fix: Colliding keys in allowed method argument rejected before adapter invocation
        adapter_invoked = False

        class CollidingArgumentApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_set = p.SetSetting
                    def _set(k, v):
                        nonlocal adapter_invoked
                        adapter_invoked = True
                        return orig_set(k, v)
                    p.SetSetting = _set
                    return p
                pm.CreateProject = _create
                return pm

        fake_app = CollidingArgumentApp()
        col_arg_log = os.path.join(self.test_dir.name, "col_arg.log")
        logger_arg = HashChainedAuditLogger(col_arg_log, __file__)
        proxy_app = AuditedResolveProxy(fake_app, logger=logger_arg, object_role="Resolve")
        pm = proxy_app.GetProjectManager()
        proj = pm.CreateProject(f"RF_TRIAL_{self.nonce}")

        colliding_arg = {1: "int_one", "1": "str_one"}
        with self.assertRaises(ValueError):
            proj.SetSetting("timelineResolutionWidth", colliding_arg)

        # Confirm adapter method was NOT invoked (zero side effect)
        self.assertFalse(adapter_invoked)

        # Close failure and verify resulting audit log is internally consistent and valid
        logger_arg.close_failure(reason="Argument serialization collision test")
        v_arg = verify_audit_log_chain(col_arg_log)
        self.assertTrue(v_arg.valid, v_arg.error)
        self.assertEqual(v_arg.terminal_event_type, "AUDIT_TERMINAL_FAILURE")
        with open(col_arg_log, "r", encoding="utf-8") as f:
            lines_arg = [json.loads(line) for line in f]
            ev_types_arg = [entry["event_type"] for entry in lines_arg]
            self.assertIn("ARGUMENT_SERIALIZATION_FAILURE", ev_types_arg)
            self.assertEqual(lines_arg[-1]["event_type"], "AUDIT_TERMINAL_FAILURE")

        # 13. Finding G4-R10-R12-01 Fix: Colliding keys in allowed method keyword arguments rejected before adapter invocation
        kwarg_adapter_invoked = False

        class CollidingKwargApp(FakeResolveApp):
            def GetProjectManager(self):
                pm = super().GetProjectManager()
                orig_create = pm.CreateProject
                def _create(name):
                    p = orig_create(name)
                    orig_set = p.SetSetting
                    def _set(*args, **kwargs):
                        nonlocal kwarg_adapter_invoked
                        kwarg_adapter_invoked = True
                        return orig_set(*args, **kwargs)
                    p.SetSetting = _set
                    return p
                pm.CreateProject = _create
                return pm

        fake_kw_app = CollidingKwargApp()
        col_kwarg_log = os.path.join(self.test_dir.name, "col_kwarg.log")
        logger_kwarg = HashChainedAuditLogger(col_kwarg_log, __file__)
        proxy_kw_app = AuditedResolveProxy(fake_kw_app, logger=logger_kwarg, object_role="Resolve")
        pm_kw = proxy_kw_app.GetProjectManager()
        proj_kw = pm_kw.CreateProject(f"RF_TRIAL_{self.nonce}")

        colliding_kwarg = {1: "int_one", "1": "str_one"}
        with self.assertRaises(ValueError):
            proj_kw.SetSetting(settingName="timelineResolutionWidth", settingValue=colliding_kwarg)

        # Confirm adapter method was NOT invoked (zero side effect)
        self.assertFalse(kwarg_adapter_invoked)

        # Close failure and verify resulting audit log is internally consistent, valid, and records keyword details
        logger_kwarg.close_failure(reason="Keyword argument serialization collision test")
        v_kwarg = verify_audit_log_chain(col_kwarg_log)
        self.assertTrue(v_kwarg.valid, v_kwarg.error)
        self.assertEqual(v_kwarg.terminal_event_type, "AUDIT_TERMINAL_FAILURE")
        with open(col_kwarg_log, "r", encoding="utf-8") as f:
            lines_kwarg = [json.loads(line) for line in f]
            ev_types_kwarg = [entry["event_type"] for entry in lines_kwarg]
            self.assertIn("ARGUMENT_SERIALIZATION_FAILURE", ev_types_kwarg)
            fail_event = next(e for e in lines_kwarg if e["event_type"] == "ARGUMENT_SERIALIZATION_FAILURE")
            self.assertIn("kwargs", fail_event["details"])
            self.assertEqual(fail_event["details"]["kwargs"]["__error__"], "KWARGS_SERIALIZATION_FAILED")
            self.assertEqual(lines_kwarg[-1]["event_type"], "AUDIT_TERMINAL_FAILURE")

    def test_15_write_ahead_intent_durability_and_linkage_finding_g4_r15(self):
        """Workorder G07-07B7-PREFLIGHT-GATE4-R15: Write-ahead audit intent, outcome linkage, fail-closed durability."""
        # 1. Intent bytes are durable on disk before the adapter method starts
        observed_intents_during_call = []

        class IntentVerifyingResolveApp(FakeResolveApp):
            def GetProjectManager(self):
                with open(self._test_log_path, "r", encoding="utf-8") as f:
                    lines = [json.loads(line) for line in f]
                # The latest line on disk must be the API_CALL_INTENT for this exact call
                latest_event = lines[-1]
                observed_intents_during_call.append(latest_event)
                return super().GetProjectManager()

        app1 = IntentVerifyingResolveApp()
        app1._test_log_path = self.log_path
        runner1 = Gate4PreflightRunner(
            resolve_app=app1,
            nonce=self.nonce,
            audit_log_path=self.log_path,
        )
        res1 = runner1.run()
        self.assertTrue(res1.success)

        self.assertEqual(len(observed_intents_during_call), 1)
        intent_during_call = observed_intents_during_call[0]
        self.assertEqual(intent_during_call["event_type"], "API_CALL_INTENT")
        self.assertEqual(intent_during_call["details"]["object_role"], "Resolve")
        self.assertEqual(intent_during_call["details"]["method_name"], "GetProjectManager")
        self.assertEqual(intent_during_call["details"]["call_id"], "call_1")
        self.assertEqual(intent_during_call["details"]["call_index"], 1)

        # 2. Failure to write/fsync intent produces zero adapter invocations and fails closed
        adapter_invocation_count = 0

        class CountingResolveApp(FakeResolveApp):
            def GetProjectManager(self):
                nonlocal adapter_invocation_count
                adapter_invocation_count += 1
                return super().GetProjectManager()

        fault_log = os.path.join(self.test_dir.name, "intent_fault.log")
        app2 = CountingResolveApp()
        runner2 = Gate4PreflightRunner(
            resolve_app=app2,
            nonce=self.nonce,
            audit_log_path=fault_log,
        )

        orig_append = runner2.logger.append_event

        def fault_append(event_type: str, message: str, details: Optional[Dict[str, Any]] = None):
            if event_type == "API_CALL_INTENT":
                raise AuditLogDurabilityError("Injected failure during intent write/fsync")
            return orig_append(event_type, message, details)

        runner2.logger.append_event = fault_append

        with self.assertRaises(AuditLogDurabilityError):
            runner2.run()

        # Zero adapter invocations confirmed
        self.assertEqual(adapter_invocation_count, 0)

        # 3. Successful calls, exceptions, and return-serialization failures link to their intents
        link_log = os.path.join(self.test_dir.name, "linkage.log")
        link_logger = HashChainedAuditLogger(link_log, runner_script_path=__file__)

        class MockProjectTarget:
            def __init__(self):
                self.invocations = 0

            def GetName(self):
                self.invocations += 1
                return "ValidName"

            def SetSetting(self, k, v):
                self.invocations += 1
                if k == "fail":
                    raise RuntimeError("Simulated adapter exception")
                if k == "bad_ret":
                    return {1: "one", "1": "str_one"}
                return True

        mock_target = MockProjectTarget()
        proxy_proj = AuditedResolveProxy(mock_target, logger=link_logger, object_role="Project")

        # 3a: Success call
        ret_ok = proxy_proj.GetName()
        self.assertEqual(ret_ok, "ValidName")

        # 3b: Exception call
        with self.assertRaises(RuntimeError):
            proxy_proj.SetSetting("fail", "val")

        # 3c: Return serialization failure call
        with self.assertRaises(ValueError):
            proxy_proj.SetSetting("bad_ret", "val")

        link_logger.close_failure(reason="Linkage tests complete")
        v_link = verify_audit_log_chain(link_log)
        self.assertTrue(v_link.valid, v_link.error)
        self.assertEqual(v_link.measured_allowed_calls, 3)

        with open(link_log, "r", encoding="utf-8") as f:
            link_records = [json.loads(line) for line in f]

        intents = [r for r in link_records if r["event_type"] == "API_CALL_INTENT"]
        outcomes = [r for r in link_records if r["event_type"] in ("API_CALL", "API_CALL_EXCEPTION", "API_CALL_SERIALIZATION_FAILURE")]

        self.assertEqual(len(intents), 3)
        self.assertEqual(len(outcomes), 3)

        # Check exact linkage
        for intent, outcome in zip(intents, outcomes):
            self.assertEqual(intent["details"]["call_id"], outcome["details"]["call_id"])
            self.assertEqual(intent["details"]["call_index"], outcome["details"]["call_index"])
            self.assertEqual(outcome["details"]["intent_seq"], intent["seq"])
            self.assertEqual(intent["details"]["arguments"], outcome["details"]["arguments"])
            self.assertEqual(intent["details"]["kwargs"], outcome["details"]["kwargs"])
            self.assertEqual(intent["details"]["object_role"], outcome["details"]["object_role"])
            self.assertEqual(intent["details"]["method_name"], outcome["details"]["method_name"])

        # 4. Verifier rejects duplicate, missing, reordered, or mismatched intent/outcome records
        def rehash_records(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            prev_h = "0" * 64
            for idx, r in enumerate(recs):
                r["seq"] = idx
                r["prev_hash"] = prev_h
                payload = {
                    "seq": r["seq"],
                    "prev_hash": r["prev_hash"],
                    "timestamp_utc": r["timestamp_utc"],
                    "event_type": r["event_type"],
                    "message": r["message"],
                    "details": r["details"],
                }
                r["record_hash"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
                prev_h = r["record_hash"]
            return recs

        def write_test_log(fname: str, recs: List[Dict[str, Any]]) -> str:
            path = os.path.join(self.test_dir.name, fname)
            with open(path, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r) + "\n")
            return path

        with open(self.log_path, "r", encoding="utf-8") as f:
            base_records = [json.loads(line) for line in f]

        # 4.1 Duplicate intent
        dup_intent_records = json.loads(json.dumps(base_records))
        first_intent_idx = next(i for i, r in enumerate(dup_intent_records) if r["event_type"] == "API_CALL_INTENT")
        dup_intent_records.insert(first_intent_idx + 1, json.loads(json.dumps(dup_intent_records[first_intent_idx])))
        p_dup = write_test_log("dup_intent.log", rehash_records(dup_intent_records))
        v_dup = verify_audit_log_chain(p_dup)
        self.assertFalse(v_dup.valid)
        self.assertIn("Duplicate API_CALL_INTENT", v_dup.error)

        # 4.2 Outcome with missing preceding intent
        missing_intent_records = json.loads(json.dumps(base_records))
        first_intent_idx = next(i for i, r in enumerate(missing_intent_records) if r["event_type"] == "API_CALL_INTENT")
        del missing_intent_records[first_intent_idx]
        p_miss = write_test_log("missing_intent.log", rehash_records(missing_intent_records))
        v_miss = verify_audit_log_chain(p_miss)
        self.assertFalse(v_miss.valid)
        self.assertIn("no preceding API_CALL_INTENT", v_miss.error)

        # 4.3 Unresolved intent before terminal success
        unresolved_records = json.loads(json.dumps(base_records))
        term_idx = len(unresolved_records) - 1
        next_ci = len([r for r in unresolved_records if r["event_type"] == "API_CALL_INTENT"]) + 1
        orphan_call_id = f"call_{next_ci}"
        fake_intent = {
            "seq": 0,
            "prev_hash": "",
            "timestamp_utc": "2026-11-04T18:00:00Z",
            "event_type": "API_CALL_INTENT",
            "message": "Orphan intent",
            "details": {
                "call_id": orphan_call_id,
                "call_index": next_ci,
                "object_role": "Resolve",
                "method_name": "GetProjectManager",
                "arguments": [],
                "kwargs": {},
            },
        }
        unresolved_records.insert(term_idx, fake_intent)
        p_unres = write_test_log("unresolved_intent.log", rehash_records(unresolved_records))
        v_unres = verify_audit_log_chain(p_unres)
        self.assertFalse(v_unres.valid)
        self.assertIn("Unresolved API_CALL_INTENT", v_unres.error)
        self.assertIn(orphan_call_id, v_unres.unresolved_intents)

        # 4.4 Mismatched metadata (method name)
        mismatch_method_records = json.loads(json.dumps(base_records))
        outcome_idx = next(i for i, r in enumerate(mismatch_method_records) if r["event_type"] == "API_CALL")
        mismatch_method_records[outcome_idx]["details"]["method_name"] = "MismatchedMethod"
        p_mismatch_m = write_test_log("mismatch_method.log", rehash_records(mismatch_method_records))
        v_mismatch_m = verify_audit_log_chain(p_mismatch_m)
        self.assertFalse(v_mismatch_m.valid)
        self.assertIn("role/method mismatch", v_mismatch_m.error)

        # 4.5 Mismatched arguments
        mismatch_args_records = json.loads(json.dumps(base_records))
        outcome_idx = next(i for i, r in enumerate(mismatch_args_records) if r["event_type"] == "API_CALL")
        mismatch_args_records[outcome_idx]["details"]["arguments"] = ["tampered_arg"]
        p_mismatch_a = write_test_log("mismatch_args.log", rehash_records(mismatch_args_records))
        v_mismatch_a = verify_audit_log_chain(p_mismatch_a)
        self.assertFalse(v_mismatch_a.valid)
        self.assertIn("arguments mismatch", v_mismatch_a.error)

        # 4.6 Duplicate outcome for same intent
        dup_outcome_records = json.loads(json.dumps(base_records))
        outcome_idx = next(i for i, r in enumerate(dup_outcome_records) if r["event_type"] == "API_CALL")
        dup_outcome_records.insert(outcome_idx + 1, json.loads(json.dumps(dup_outcome_records[outcome_idx])))
        p_dup_out = write_test_log("dup_outcome.log", rehash_records(dup_outcome_records))
        v_dup_out = verify_audit_log_chain(p_dup_out)
        self.assertFalse(v_dup_out.valid)
        self.assertIn("Duplicate outcome event", v_dup_out.error)

        # 4.7 Ordering violation (outcome before intent)
        order_records = json.loads(json.dumps(base_records))
        first_intent_idx = next(i for i, r in enumerate(order_records) if r["event_type"] == "API_CALL_INTENT")
        first_outcome_idx = next(i for i, r in enumerate(order_records) if r["event_type"] == "API_CALL" and r["details"]["call_id"] == order_records[first_intent_idx]["details"]["call_id"])
        # Swap intent and outcome
        order_records[first_intent_idx], order_records[first_outcome_idx] = order_records[first_outcome_idx], order_records[first_intent_idx]
        p_order = write_test_log("order_violation.log", rehash_records(order_records))
        v_order = verify_audit_log_chain(p_order)
        self.assertFalse(v_order.valid)
        self.assertTrue("no preceding API_CALL_INTENT" in v_order.error or "Ordering violation" in v_order.error)

        # 4.8 Finding G4-R15-01: Mismatched outcome call_index
        mismatch_ci_records = json.loads(json.dumps(base_records))
        outcome_idx = next(i for i, r in enumerate(mismatch_ci_records) if r["event_type"] == "API_CALL")
        mismatch_ci_records[outcome_idx]["details"]["call_index"] = 9999
        p_mismatch_ci = write_test_log("mismatch_call_index.log", rehash_records(mismatch_ci_records))
        v_mismatch_ci = verify_audit_log_chain(p_mismatch_ci)
        self.assertFalse(v_mismatch_ci.valid)
        self.assertIn("call_index (9999) does not match intent call_index", v_mismatch_ci.error)

        # 4.9 Finding G4-R15-01: Non-monotonic intent call_index
        nonmono_ci_records = json.loads(json.dumps(base_records))
        intent_indices = [i for i, r in enumerate(nonmono_ci_records) if r["event_type"] == "API_CALL_INTENT"]
        if len(intent_indices) >= 2:
            nonmono_ci_records[intent_indices[1]]["details"]["call_index"] = 42
            p_nonmono_ci = write_test_log("nonmono_call_index.log", rehash_records(nonmono_ci_records))
            v_nonmono_ci = verify_audit_log_chain(p_nonmono_ci)
            self.assertFalse(v_nonmono_ci.valid)
            self.assertIn("non-monotonic call_index", v_nonmono_ci.error)


if __name__ == "__main__":
    unittest.main()
