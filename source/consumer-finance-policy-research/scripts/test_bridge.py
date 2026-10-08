#!/usr/bin/env python3
"""Offline contract tests using synthetic payloads and a mocked transport.

All document names, timestamps, handles and report text are test fixtures.
No real research material or conversation transcript is required.
"""
from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import types
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, call, patch


class _ConnectTimeout(Exception):
    pass


class _ReadTimeout(Exception):
    pass


class _ConnectionError(Exception):
    pass


class _Timeout(Exception):
    pass


_mock_requests = MagicMock()
_mock_requests.exceptions = types.SimpleNamespace(
    ConnectTimeout=_ConnectTimeout,
    ReadTimeout=_ReadTimeout,
    ConnectionError=_ConnectionError,
    Timeout=_Timeout,
)
_mock_coze = types.SimpleNamespace(requests=_mock_requests)
sys.modules["coze_workload_identity"] = _mock_coze

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import _bridge
import add_material_file
import ask_research_job
import create_artifact_download_grant
import create_material_draft
import confirm_reference_material
import get_material_draft
import get_research_job_result
import get_research_job_status
import remove_material_file
import seal_material_draft
import submit_research_job
import prepare_reference_material


DRAFT_ID = "rdraft_" + "a" * 24
MATERIAL_REF = "dmat_" + "b" * 24
JOB_ID = "rjob_" + "c" * 24
LEGACY_ARTIFACT_ID = "art_docx_" + "d" * 12
RENDER_ARTIFACT_ID = "art_pdf_" + "e" * 20
ARTIFACT_ID = "art_docx_" + "d" * 24
REFERENCE_AUTHORIZATION_ID = "rauth_" + "R" * 32
REFERENCE_URL = "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1"
PUBLIC_PERMISSION = {
    "scope": "public",
    "confidentiality": "public",
    "license_basis": "public_web",
    "content_use": "full_analysis",
    "authorized_by": "channel_verified",
    "retention_mode": "task_only",
}
CANONICAL_EXISTING_PERMISSION = {
    "scope": "licensed_external",
    "confidentiality": "licensed_external",
    "license_basis": "user_owned",
    "content_use": "full_analysis",
    "authorized_by": "user_explicit",
    "retention_mode": "session_only",
}
REFERENCE_ITEM = {
    "authorization_id": REFERENCE_AUTHORIZATION_ID,
    "kind": "url",
    "content": REFERENCE_URL,
}


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def draft_body(*, status="collecting", count=0, materials=None):
    return {
        "draft_id": DRAFT_ID,
        "status": status,
        "material_count": count,
        "materials": materials or [],
        "created_at": 1.0,
        "updated_at": 1.0,
        "sealed_at": 0.0,
    }


def job_status_body(status="queued"):
    return {
        "job_id": JOB_ID,
        "status": status,
        "progress": {"stage": "policy_verification", "percent": 25},
        "error": None,
        "limited_delivery": False,
        "delivery_notice": None,
        "recovery_required": False,
        "recovery_note": "",
    }


def report_artifact(report_format="docx"):
    if report_format == "docx":
        return {
            "artifact_id": ARTIFACT_ID,
            "format": "docx",
            "filename": "consumer_finance_policy_reference_candidate_example.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "status": "ready",
            "byte_size": 1234,
            "error": None,
        }
    return {
        "artifact_id": RENDER_ARTIFACT_ID,
        "format": "pdf",
        "filename": "consumer_finance_policy_reference_candidate_example.pdf",
        "mime_type": "application/pdf",
        "status": "ready",
        "byte_size": 2345,
        "error": None,
    }


def result_body(*, quality_passed, delivery_state, human_review_required, artifacts=None):
    is_candidate = delivery_state == "reference_candidate"
    return {
        "job_id": JOB_ID,
        "status": "completed" if quality_passed else "completed_with_gaps",
        "limited_delivery": not quality_passed,
        "delivery_notice": None,
        "quality_passed": quality_passed,
        "delivery_state": delivery_state,
        "delivery": {
            "verified": quality_passed and delivery_state == "verified_report",
            "reference_candidate": is_candidate,
            "reference_candidate_eligible": is_candidate,
            "has_reference_candidate": is_candidate,
            "reference_candidate_ready": is_candidate,
            "blocked": False,
            "has_final_report": True,
            "has_report_package": True,
            "human_review_required": human_review_required,
            "research_report_package": {
                "body_char_count": 4,
                "report_body": "正文内容",
                "material_usage_ledger": [],
                "delivery_status": delivery_state,
                "human_review_required": human_review_required,
            },
        },
        "artifacts": [report_artifact()] if artifacts is None else artifacts,
        "snapshot": {
            "executive_summary": "摘要",
            "key_conclusions": ["结论一"],
            "impacts": ["影响一"],
            "actions": ["建议一"],
            "coverage_gaps": [],
            "supplement_suggestions": [],
            "human_review_required": human_review_required,
            "source_list": [],
        },
    }


def apply_v3_reference_candidate_contract(
    body,
    error_codes,
    *,
    origin="managed_post_corpus",
    canonical_report_body=None,
    public_report_body=None,
):
    error_codes = sorted(error_codes)
    canonical_report_body = canonical_report_body or (
        "现有合法材料支持形成来源归因摘要，相关结论仍需结合证据编号逐项人工复核。" * 40
    )
    public_report_body = public_report_body or canonical_report_body
    report_sha256 = hashlib.sha256(
        canonical_report_body.encode("utf-8")
    ).hexdigest()
    body_char_count = len(_bridge._project_report_quality_body(
        canonical_report_body
    ))
    public_body_char_count = len(_bridge._project_report_quality_body(
        public_report_body
    ))
    length_policy = {
        "schema_version": "report-length-policy.v1",
        "report_profile": "standard_report",
        "target_length": None,
        "soft_min_length": 5000,
        "soft_max_length": 9000,
        "absolute_min_length": 1000,
        "absolute_max_length": 30000,
        "strict_min_length": None,
        "strict_max_length": None,
        "enforced_min_length": 1000,
        "enforced_max_length": 30000,
        "strict": False,
        "minimum_decision_deltas": 2,
    }
    package = body["delivery"]["research_report_package"]
    package.clear()
    package.update({
        "report_id": "rpt_v3_contract_fixture",
        "version": "v8.1",
        "gate_receipt_id": "grcpt_v3_contract_fixture",
        "request_id": "req_v3_contract_fixture",
        "run_id": "run_v3_contract_fixture",
        "deep_dive_report": canonical_report_body,
        "body_char_count": body_char_count,
        "appendix_char_count": 0,
        "report_profile": "standard_report",
        "target_length": None,
        "min_length": None,
        "max_length": None,
        "length_policy": length_policy,
        "cover_info": {"title": "消费金融研究参考稿"},
        "executive_summary": "",
        "toc": "",
        "report_body": canonical_report_body,
        "appendix_a_source_list": "",
        "appendix_b_evidence_matrix": "",
        "appendix_c_material_coverage": "",
        "material_usage_ledger": [],
        "material_input_count": 0,
        "material_usage_status": "not_applicable_no_user_or_internal_materials",
        "evidence_ledger": [{
            "evidence_id": "ev-v3-safe-1",
            "title": "可安全引用的公开材料",
            "url": "https://example.org/safe-reference",
        }],
        "key_conclusions": [],
        "impacts": [],
        "actions": [],
        "requirement_ledger": [],
        "usefulness_review": {},
        "report_quality_diagnostics": {},
        "human_review_required": True,
        "human_review_notes": "供讨论参考，使用前需人工复核。",
        "docx_artifact": None,
        "pdf_artifact": None,
        "pptx_artifact": None,
        "artifacts": [],
        "document_style_spec": None,
        "template_version": "v8.0",
        "export_status": "not_requested",
        "delivery_status": "reference_candidate",
    })
    semantic_payload = deepcopy(package)
    for field_name in (
        "docx_artifact", "pdf_artifact", "pptx_artifact", "artifacts",
        "export_status",
    ):
        semantic_payload.pop(field_name)
    package_sha256 = hashlib.sha256(json.dumps(
        semantic_payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    body["reference_candidate_schema"] = "reference-candidate.v3"
    body["reference_candidate_reason"] = "content_quality_limited"
    body["delivery"]["reference_candidate_schema"] = "reference-candidate.v3"
    body["delivery"]["reference_candidate_reason"] = "content_quality_limited"
    body["delivery"]["final_report"] = public_report_body
    body["snapshot"]["report_body"] = public_report_body
    metrics = {
        "reference_candidate_schema": "reference-candidate.v3",
        "reference_candidate_reason": "content_quality_limited",
        "reference_candidate_server_recomputed": True,
        "reference_candidate_integrity_gate_passed": True,
        "reference_candidate_hard_gate_passed": False,
        "hard_gate_pass": False,
        "reference_candidate_eligible": True,
        "candidate_authorization_origin": origin,
        "eligible_evidence_nonempty": True,
        "content_degradation_applied": True,
        "content_degradation_error_codes": error_codes,
        "content_degradation_mode": "source_attributed_digest",
        "content_degradation_safe_evidence_ids": ["ev-v3-safe-1"],
        "content_degradation_safe_evidence_count": 1,
        "content_degradation_unsafe_reference_excluded_count": 0,
        "content_degradation_report_sha256": report_sha256,
        "content_degradation_body_char_count": body_char_count,
        "content_degradation_length_policy": length_policy,
        "content_degradation_action_matrix_mode": "research_only",
        "content_degradation_hard_scan_pass": True,
        "content_degradation_hard_scan_error_codes": [],
        "content_degradation_human_projection_pass": True,
        "content_degradation_human_projection_body_char_count": public_body_char_count,
        "content_degradation_human_projection_error": "",
        "reference_candidate_semantic_gate_passed": False,
        "semantic_review_pass": False,
        "reference_candidate_package_sha256": package_sha256,
    }
    gate = {
        "gate_pass": False,
        "error_codes": error_codes,
        "gate_metrics": metrics,
        "report_sha256": report_sha256,
    }
    body["delivery"]["gate_receipt"] = gate
    body["delivery"]["orchestrator_gate_receipt"] = deepcopy(gate)
    return body


class TestBridgeBasics(unittest.TestCase):
    def setUp(self):
        # All transport calls use _mock_requests; no private deployment is used.
        origin = patch.object(_bridge, "PILOT_API_ORIGIN", "https://research.example")
        origin.start()
        self.addCleanup(origin.stop)

    def test_missing_backend_configuration_fails_before_network(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(_bridge, "BASE_URL", ""):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_pilot_missing_origin_fails_before_network(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "pilot_direct_env",
            "POLICY_RESEARCH_PILOT_ACCESS_TOKEN": "bridge",
            "POLICY_RESEARCH_PILOT_SUBJECT_ID": "subject-token",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                with patch.object(_bridge, "PILOT_API_ORIGIN", ""):
                    result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_permission_profiles_build_complete_json_object(self):
        value = _bridge.build_permission(
            "licensed_external/user_owned",
            authorized_by="user_explicit",
        )
        self.assertEqual(value, {
            "scope": "licensed_external",
            "confidentiality": "licensed_external",
            "license_basis": "user_owned",
            "content_use": "full_analysis",
            "authorized_by": "user_explicit",
            "retention_mode": "task_only",
        })

    def test_permission_cli_profiles_are_canonical_and_legacy_aliases_match(self):
        self.assertEqual(
            set(_bridge.VALID_PERMISSIONS),
            {"public_web", "user_owned", "licensed_enterprise"},
        )
        self.assertTrue(all("/" not in value for value in _bridge.VALID_PERMISSIONS))
        canonical = _bridge.build_permission(
            "public_web", authorized_by="channel_verified",
        )
        legacy = _bridge.build_permission(
            "public/public_web", authorized_by="channel_verified",
        )
        self.assertEqual(canonical, legacy)

    def test_material_cli_hides_path_and_exposes_only_canonical_permissions(self):
        parser = add_material_file.build_parser()
        permission_action = next(
            action for action in parser._actions
            if action.dest == "permission_profile"
        )
        self.assertEqual(
            set(permission_action.choices),
            {"public_web", "user_owned", "licensed_enterprise"},
        )
        self.assertNotIn("--file-path", parser.format_help())

        legacy_args = parser.parse_args([
            "--draft-id", DRAFT_ID,
            "--filename", "政策原文.pdf",
            "--permission-profile", "public/public_web",
            "--authorized-by", "channel_verified",
        ])
        self.assertEqual(legacy_args.permission_profile, "public_web")

    def test_runtime_attachment_root_does_not_widen_to_configured_or_default_roots(self):
        runtime = os.path.join(tempfile.gettempdir(), "agent-drive")
        configured = os.path.join(tempfile.gettempdir(), "configured-root")
        self.assertEqual(
            _bridge._build_attachment_roots(
                runtime_root=runtime,
                configured_roots=configured,
            ),
            (os.path.realpath(runtime),),
        )
        self.assertEqual(
            _bridge._build_attachment_roots(
                runtime_root="",
                configured_roots=configured,
            ),
            (os.path.realpath(configured),),
        )

    def test_idempotency_is_canonical_and_complete(self):
        first = _bridge.make_idempotency_key({"b": [2], "a": 1})
        second = _bridge.make_idempotency_key({"a": 1, "b": [2]})
        changed = _bridge.make_idempotency_key({"a": 1, "b": [3]})
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertTrue(16 <= len(first) <= 200)

    def test_handle_validation(self):
        self.assertEqual(_bridge.validate_handle(DRAFT_ID, "draft"), DRAFT_ID)
        self.assertEqual(_bridge.validate_handle(MATERIAL_REF, "material"), MATERIAL_REF)
        self.assertEqual(_bridge.validate_handle(JOB_ID, "job"), JOB_ID)
        self.assertEqual(_bridge.validate_handle(ARTIFACT_ID, "artifact"), ARTIFACT_ID)
        self.assertEqual(
            _bridge.validate_handle(LEGACY_ARTIFACT_ID, "artifact"),
            LEGACY_ARTIFACT_ID,
        )
        self.assertEqual(
            _bridge.validate_handle(RENDER_ARTIFACT_ID, "artifact"),
            RENDER_ARTIFACT_ID,
        )
        with self.assertRaises(ValueError):
            _bridge.validate_handle("../../etc/passwd", "draft")

    def test_artifact_handle_rejects_unpublished_lengths_and_shapes(self):
        for invalid in (
            "art_docx_" + "a" * 11,
            "art_docx_" + "a" * 13,
            "art_pdf_" + "a" * 19,
            "art_pdf_" + "a" * 21,
            "art_pdf_" + "a" * 23,
            "art_pdf_" + "a" * 25,
            "art_xlsx_" + "a" * 20,
            "art_docx_" + "A" * 24,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    _bridge.validate_handle(invalid, "artifact")

    def test_cross_account_credential_id_override(self):
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_SKILL_ID": "new-skill",
            "COZE_RESEARCH_BRIDGE_KEY_new-skill": "bridge",
            "COZE_RESEARCH_SUBJECT_new-skill": "subject-token",
        }, clear=True):
            self.assertEqual(_bridge.load_bridge_key(), "bridge")
            self.assertEqual(_bridge.load_subject(), "subject-token")

    def test_cross_account_credential_unique_auto_discovery(self):
        with patch.dict(os.environ, {
                "COZE_RESEARCH_BRIDGE_KEY_new-skill": "bridge",
                "COZE_RESEARCH_SUBJECT_new-skill": "subject-token",
        }, clear=True):
            self.assertEqual(_bridge.load_bridge_key(), "bridge")
            self.assertEqual(_bridge.load_subject(), "subject-token")

    def test_synthetic_skill_uses_unique_credential_placeholders(self):
        with patch.dict(os.environ, {
            "COZE_RESEARCH_BRIDGE_KEY_example-skill": "bridge",
            "COZE_RESEARCH_SUBJECT_example-skill": "subject-token",
        }, clear=True):
            self.assertEqual(_bridge.load_bridge_key(), "bridge")
            self.assertEqual(_bridge.load_subject(), "subject-token")

    def test_runtime_skill_id_selects_exact_credential_when_candidates_are_ambiguous(self):
        with patch.dict(os.environ, {
            "skill_id": "runtime-skill",
            "COZE_RESEARCH_BRIDGE_KEY_old-skill": "first",
            "COZE_RESEARCH_BRIDGE_KEY_runtime-skill": "second",
            "COZE_RESEARCH_SUBJECT_old-skill": "first",
            "COZE_RESEARCH_SUBJECT_runtime-skill": "second",
        }, clear=True):
            self.assertEqual(_bridge.load_bridge_key(), "second")
            self.assertEqual(_bridge.load_subject(), "second")

    def test_ambiguous_credentials_fail_closed(self):
        with patch.dict(os.environ, {
            "COZE_RESEARCH_BRIDGE_KEY_a": "first",
            "COZE_RESEARCH_BRIDGE_KEY_b": "second",
        }, clear=True):
            self.assertEqual(_bridge.load_bridge_key(), "")

    def test_pilot_mode_reads_only_explicit_direct_variables(self):
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "pilot_direct_env",
            "POLICY_RESEARCH_PILOT_ACCESS_TOKEN": "bridge",
            "POLICY_RESEARCH_PILOT_SUBJECT_ID": "subject-token",
            "COZE_RESEARCH_BRIDGE_KEY_runtime-skill": "first",
            "COZE_RESEARCH_SUBJECT_runtime-skill": "second",
        }, clear=True):
            self.assertEqual(_bridge.load_bridge_key(), "bridge")
            self.assertEqual(_bridge.load_subject(), "subject-token")

    def test_invalid_credential_mode_fails_closed_without_network(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "unexpected-mode",
        }, clear=True):
            result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_pilot_mode_requires_direct_values_without_network(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "pilot_direct_env",
            "COZE_RESEARCH_BRIDGE_KEY_runtime-skill": "first",
            "COZE_RESEARCH_SUBJECT_runtime-skill": "second",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", _bridge.PILOT_API_ORIGIN):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_pilot_mode_requires_direct_subject_without_network(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "pilot_direct_env",
            "POLICY_RESEARCH_PILOT_ACCESS_TOKEN": "bridge",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", _bridge.PILOT_API_ORIGIN):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "SUBJECT_MISSING")
        _mock_requests.request.assert_not_called()

    def test_pilot_mode_rejects_nonfixed_origin_without_network(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "pilot_direct_env",
            "POLICY_RESEARCH_PILOT_ACCESS_TOKEN": "bridge",
            "POLICY_RESEARCH_PILOT_SUBJECT_ID": "subject-token",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://other.example"):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_pilot_mode_blocks_redirects_and_disables_following(self):
        _mock_requests.request.reset_mock()
        _mock_requests.request.return_value = FakeResponse(
            302, {}, {"Location": "https://other.example/collect"}
        )
        with patch.dict(os.environ, {
            "RESEARCH_CREDENTIAL_MODE": "pilot_direct_env",
            "POLICY_RESEARCH_PILOT_ACCESS_TOKEN": "bridge",
            "POLICY_RESEARCH_PILOT_SUBJECT_ID": "subject-token",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", _bridge.PILOT_API_ORIGIN):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "REDIRECT_BLOCKED")
        self.assertFalse(_mock_requests.request.call_args.kwargs["allow_redirects"])
        headers = _mock_requests.request.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Research-Bridge-Key"], "bridge")
        self.assertEqual(headers["X-Research-Subject"], "subject-token")

    def test_subject_fails_closed_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                with patch.object(_bridge, "load_bridge_key", return_value="bridge"):
                    with patch.object(_bridge, "load_subject", return_value=""):
                        _mock_requests.request.reset_mock()
                        result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "SUBJECT_MISSING")
        _mock_requests.request.assert_not_called()

    def test_non_https_backend_fails_closed_without_credentials_or_network(self):
        _mock_requests.request.reset_mock()
        with patch.object(_bridge, "BASE_URL", "http://research.example"):
            result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_malformed_backend_numbers_are_bounded_not_crashes(self):
        projected = _bridge.project_job_status({
            "job_id": JOB_ID,
            "status": "running",
            "progress": {"stage": "x", "percent": "not-a-number"},
        })
        self.assertEqual(projected["完成比例"], 0)
        projected = _bridge.project_job_status({
            "job_id": JOB_ID,
            "status": "running",
            "progress": {"stage": "x", "percent": 999},
        })
        self.assertEqual(projected["完成比例"], 100)

    def test_credentials_are_headers_not_body(self):
        _mock_requests.request.reset_mock()
        _mock_requests.request.return_value = FakeResponse(200, {})
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                with patch.object(_bridge, "load_bridge_key", return_value="bridge-secret"):
                    with patch.object(_bridge, "load_subject", return_value="opaque-user-token"):
                        _bridge.do_request("POST", "/x", json_body={"safe": True})
        kwargs = _mock_requests.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-Research-Bridge-Key"], "bridge-secret")
        self.assertEqual(kwargs["headers"]["X-Research-Subject"], "opaque-user-token")
        self.assertNotIn("bridge-secret", json.dumps(kwargs["json"]))
        self.assertNotIn("opaque-user-token", json.dumps(kwargs["json"]))
        self.assertNotIn("allow_redirects", kwargs)

    def test_proxy_without_materialized_credentials_fails_closed(self):
        _mock_requests.request.reset_mock()
        _mock_requests.request.return_value = FakeResponse(200, {})
        with patch.dict(os.environ, {
            "COZE_SKILL_PROXY_DOMAIN": "https://proxy.invalid",
            "identity_ticket": "runtime-ticket",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                with patch.object(_bridge, "load_bridge_key", return_value=""):
                    with patch.object(_bridge, "load_subject", return_value=""):
                        result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_proxy_with_both_placeholders_sends_both_business_headers(self):
        _mock_requests.request.reset_mock()
        _mock_requests.request.return_value = FakeResponse(200, {})
        with patch.dict(os.environ, {
            "COZE_SKILL_PROXY_DOMAIN": "https://proxy.invalid",
            "identity_ticket": "runtime-ticket",
            "COZE_RESEARCH_BRIDGE_KEY_example-skill": "bridge-placeholder",
            "COZE_RESEARCH_SUBJECT_example-skill": "subject-placeholder",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                result = _bridge.do_request("GET", "/x")
        self.assertIsInstance(result, FakeResponse)
        headers = _mock_requests.request.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Research-Bridge-Key"], "bridge-placeholder")
        self.assertEqual(headers["X-Research-Subject"], "subject-placeholder")

    def test_proxy_with_only_bridge_placeholder_rejects_missing_subject(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "COZE_SKILL_PROXY_DOMAIN": "https://proxy.invalid",
            "identity_ticket": "runtime-ticket",
            "COZE_RESEARCH_BRIDGE_KEY_example-skill": "bridge-placeholder",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "SUBJECT_MISSING")
        _mock_requests.request.assert_not_called()

    def test_proxy_with_only_subject_placeholder_rejects_missing_bridge_key(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {
            "COZE_SKILL_PROXY_DOMAIN": "https://proxy.invalid",
            "identity_ticket": "runtime-ticket",
            "COZE_RESEARCH_SUBJECT_example-skill": "subject-placeholder",
        }, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_historical_proxy_name_still_delegates(self):
        with patch.dict(os.environ, {
            "COZE_OUTBOUND_AUTH_PROXY": "https://proxy.invalid",
            "identity_ticket": "runtime-ticket",
        }, clear=True):
            self.assertTrue(_bridge.uses_outbound_auth_proxy())

    def test_missing_credentials_without_proxy_fails_closed(self):
        _mock_requests.request.reset_mock()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(_bridge, "BASE_URL", "https://research.example"):
                with patch.object(_bridge, "load_bridge_key", return_value=""):
                    with patch.object(_bridge, "load_subject", return_value=""):
                        result = _bridge.do_request("GET", "/x")
        self.assertEqual(result["错误码"], "CONFIG_ERROR")
        _mock_requests.request.assert_not_called()

    def test_proxy_requires_identity_ticket_before_delegating(self):
        with patch.dict(os.environ, {
            "COZE_OUTBOUND_AUTH_PROXY": "https://proxy.invalid",
        }, clear=True):
            self.assertFalse(_bridge.uses_outbound_auth_proxy())

    def test_proxy_auth_error_has_actionable_message(self):
        result = _bridge._map_http_error(FakeResponse(401, {}))
        self.assertEqual(result["错误码"], "HTTP_401")
        self.assertIn("认证代理", result["错误信息"])


class TestHTTPErrorContract(unittest.TestCase):
    def test_new_authorization_same_business_key_fails_safe_without_retry(self):
        result = _bridge._map_http_error(FakeResponse(
            409,
            {"detail": {"error_code": "E_IDEMPOTENCY_KEY_REUSED", "message": "conflict"}},
        ))
        self.assertEqual(result["错误码"], "E_IDEMPOTENCY_KEY_REUSED")
        self.assertIn("继续查询原研究任务", result["错误信息"])
        self.assertFalse(result["可重试"])

    def test_error_envelope_projects_only_a_valid_existing_job_handle(self):
        result = _bridge._map_http_error(FakeResponse(
            409,
            {"detail": {
                "error_code": "E_IDEMPOTENCY_KEY_REUSED",
                "message": "conflict",
                "job_id": JOB_ID,
            }},
        ))
        self.assertEqual(result["_job_id"], JOB_ID)

    def test_error_envelope_does_not_project_a_malformed_job_handle(self):
        result = _bridge._map_http_error(FakeResponse(
            409,
            {"detail": {
                "error_code": "E_IDEMPOTENCY_KEY_REUSED",
                "message": "conflict",
                "job_id": "rjob_not-a-valid-handle",
            }},
        ))
        self.assertNotIn("_job_id", result)

    def test_combined_material_limit_has_actionable_message(self):
        result = _bridge._map_http_error(FakeResponse(
            400,
            {"detail": {"error_code": "E_JOB_MATERIAL_LIMIT", "message": "too many"}},
        ))
        self.assertEqual(result["错误码"], "E_JOB_MATERIAL_LIMIT")
        self.assertIn("合计最多 10 项", result["错误信息"])

    def test_detail_object(self):
        result = _bridge._map_http_error(FakeResponse(
            409,
            {"detail": {"error_code": "E_DRAFT_SEALED", "message": "sealed"}},
        ))
        self.assertEqual(result["错误码"], "E_DRAFT_SEALED")
        self.assertEqual(result["错误信息"], "sealed")

    def test_detail_string(self):
        result = _bridge._map_http_error(FakeResponse(
            400, {"detail": "E_MATERIAL_PERMISSION_REQUIRED: missing"}
        ))
        self.assertEqual(result["错误码"], "E_MATERIAL_PERMISSION_REQUIRED")

    def test_top_level_pending_and_retry_after(self):
        result = _bridge._map_http_error(FakeResponse(
            409,
            {"error_code": "E_RESEARCH_JOB_NOT_COMPLETE", "message": "pending"},
            {"Retry-After": "5"},
        ))
        self.assertEqual(result["错误码"], "E_RESEARCH_JOB_NOT_COMPLETE")
        self.assertTrue(result["可重试"])
        self.assertEqual(result["重试等待秒"], 5)

    def test_non_json_error(self):
        result = _bridge._map_http_error(FakeResponse(503, ValueError("not json")))
        self.assertEqual(result["错误码"], "HTTP_503")
        self.assertTrue(result["可重试"])


class TestDraftTools(unittest.TestCase):
    def test_create_contract(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(201, draft_body())):
            result = create_material_draft.create_material_draft()
        self.assertEqual(result["材料数量"], 0)
        self.assertEqual(result["_draft_id"], DRAFT_ID)

    def test_create_missing_id_fails_closed(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(201, {"status": "collecting"})):
            result = create_material_draft.create_material_draft()
        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")

    def test_upload_exact_multipart_and_nested_response(self):
        material = {
            "material_ref": MATERIAL_REF,
            "filename": "政策原文.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 4,
            "sha256": "never-expose",
            "permission": dict(PUBLIC_PERMISSION),
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.pdf")
            with open(path, "wb") as handle:
                handle.write(b"%PDF")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request", return_value=FakeResponse(
                    201, {"action": "created", "material": material}
                )) as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        file_path=path,
                        filename="政策原文.pdf",
                        permission_profile="public/public_web",
                        authorized_by="channel_verified",
                    )
        kwargs = request.call_args.kwargs
        self.assertEqual(set(kwargs["files"]), {"file"})
        self.assertEqual(set(kwargs["data"]), {"permission"})
        permission = json.loads(kwargs["data"]["permission"])
        self.assertEqual(permission["scope"], "public")
        self.assertEqual(permission["content_use"], "full_analysis")
        self.assertEqual(result["_material_ref"], MATERIAL_REF)
        self.assertNotIn("sha256", json.dumps(result))

    def test_upload_rejects_material_receipt_identity_mismatch(self):
        expected = {
            "material_ref": MATERIAL_REF,
            "filename": "政策原文.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 4,
            "permission": dict(PUBLIC_PERMISSION),
        }
        mismatches = {
            "filename": {**expected, "filename": "其他文件.pdf"},
            "mime_type": {**expected, "mime_type": "application/octet-stream"},
            "size_bytes": {**expected, "size_bytes": 5},
            "string_size": {**expected, "size_bytes": "4"},
            "permission": {
                **expected,
                "permission": {**PUBLIC_PERMISSION, "retention_mode": "session_only"},
            },
            "missing_permission": {
                key: value for key, value in expected.items() if key != "permission"
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.pdf")
            with open(path, "wb") as handle:
                handle.write(b"%PDF")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                for label, material in mismatches.items():
                    with self.subTest(label=label):
                        with patch.object(
                            _bridge,
                            "do_request",
                            return_value=FakeResponse(
                                201, {"action": "created", "material": material}
                            ),
                        ):
                            result = add_material_file.add_material_file(
                                draft_id=DRAFT_ID,
                                file_path=path,
                                filename="政策原文.pdf",
                                permission_profile="public_web",
                                authorized_by="channel_verified",
                            )
                        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")
                        self.assertNotIn("_material_ref", result)

    def test_upload_existing_material_binds_bytes_but_keeps_canonical_identity(self):
        payload = b"same-payload"
        material = {
            "material_ref": MATERIAL_REF,
            "filename": "第一次命名.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "permission": dict(CANONICAL_EXISTING_PERMISSION),
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "第二次命名.pdf")
            with open(path, "wb") as handle:
                handle.write(payload)
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(
                    _bridge,
                    "do_request",
                    return_value=FakeResponse(
                        200, {"action": "existing", "material": material}
                    ),
                ):
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        file_path=path,
                        filename="第二次命名.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(
            result["接收结果"], "同一内容已存在，沿用首次授权和保留方式"
        )
        self.assertEqual(result["文件名"], "第一次命名.pdf")
        self.assertEqual(result["保留方式"], "session_only")
        self.assertEqual(result["_material_ref"], MATERIAL_REF)

    def test_upload_existing_material_rejects_unbound_digest(self):
        payload = b"same-payload"
        base = {
            "material_ref": MATERIAL_REF,
            "filename": "第一次命名.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(payload),
            "permission": dict(CANONICAL_EXISTING_PERMISSION),
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "第二次命名.pdf")
            with open(path, "wb") as handle:
                handle.write(payload)
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                for label, digest in (("missing", None), ("wrong", "0" * 64)):
                    with self.subTest(label=label):
                        material = dict(base)
                        if digest is not None:
                            material["sha256"] = digest
                        with patch.object(
                            _bridge,
                            "do_request",
                            return_value=FakeResponse(
                                200, {"action": "existing", "material": material}
                            ),
                        ):
                            result = add_material_file.add_material_file(
                                draft_id=DRAFT_ID,
                                file_path=path,
                                filename="第二次命名.pdf",
                                permission_profile="public_web",
                                authorized_by="channel_verified",
                            )
                        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")
                        self.assertNotIn("_material_ref", result)

    def test_upload_existing_material_rejects_invalid_canonical_permission(self):
        payload = b"same-payload"
        base = {
            "material_ref": MATERIAL_REF,
            "filename": "第一次命名.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        invalid_permissions = {
            "missing": None,
            "library_retention": {
                **CANONICAL_EXISTING_PERMISSION,
                "retention_mode": "library",
            },
            "inconsistent_scope": {
                **CANONICAL_EXISTING_PERMISSION,
                "scope": "public",
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "第二次命名.pdf")
            with open(path, "wb") as handle:
                handle.write(payload)
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                for label, permission in invalid_permissions.items():
                    with self.subTest(label=label):
                        material = dict(base)
                        if permission is not None:
                            material["permission"] = permission
                        with patch.object(
                            _bridge,
                            "do_request",
                            return_value=FakeResponse(
                                200, {"action": "existing", "material": material}
                            ),
                        ):
                            result = add_material_file.add_material_file(
                                draft_id=DRAFT_ID,
                                file_path=path,
                                filename="第二次命名.pdf",
                                permission_profile="public_web",
                                authorized_by="channel_verified",
                            )
                        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")
                        self.assertNotIn("_material_ref", result)

    def test_upload_resolves_materialized_filename_and_preserves_original_name(self):
        material = {
            "material_ref": MATERIAL_REF,
            "filename": "政策原文.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": 4,
            "permission": dict(PUBLIC_PERMISSION),
        }
        with tempfile.TemporaryDirectory() as folder:
            actual = os.path.join(folder, "政策原文_1700000000000_0_abcd.docx")
            with open(actual, "wb") as handle:
                handle.write(b"DOCX")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request", return_value=FakeResponse(
                    201, {"action": "created", "material": material}
                )) as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.docx",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["_material_ref"], MATERIAL_REF)
        self.assertEqual(request.call_args.kwargs["files"]["file"][0], "政策原文.docx")

    def test_upload_collapses_exact_and_materialized_byte_identical_copies(self):
        original_name = "示例日期--示例消费金融政策(1).docx"
        material = {
            "material_ref": MATERIAL_REF,
            "filename": original_name,
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(b"same-upload"),
            "permission": dict(PUBLIC_PERMISSION),
        }
        with tempfile.TemporaryDirectory() as folder:
            paths = (
                os.path.join(folder, original_name),
                os.path.join(
                    folder,
                    "示例日期--示例消费金融政策(1)_1700000000001_0_efgh.docx",
                ),
            )
            for path in paths:
                with open(path, "wb") as handle:
                    handle.write(b"same-upload")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request", return_value=FakeResponse(
                    201, {"action": "created", "material": material}
                )) as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename=original_name,
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["_material_ref"], MATERIAL_REF)
        uploaded = request.call_args.kwargs["files"]["file"]
        self.assertEqual(uploaded[0], original_name)
        self.assertEqual(uploaded[1], b"same-upload")

    def test_upload_collapses_multiple_materialized_byte_identical_copies(self):
        material = {
            "material_ref": MATERIAL_REF,
            "filename": "政策原文.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(b"same-upload"),
            "permission": dict(PUBLIC_PERMISSION),
        }
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ("1700000000001_0_efgh", "1700000000001_0_k9p2"):
                with open(os.path.join(folder, f"政策原文_{suffix}.docx"), "wb") as handle:
                    handle.write(b"same-upload")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request", return_value=FakeResponse(
                    201, {"action": "created", "material": material}
                )) as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.docx",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["_material_ref"], MATERIAL_REF)
        self.assertEqual(request.call_args.kwargs["files"]["file"][0], "政策原文.docx")

    def test_snapshot_accepts_fuse_metadata_refresh_when_bytes_are_stable(self):
        payload = b"stable-coze-drive-bytes"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.docx")
            with open(path, "wb") as handle:
                handle.write(payload)
            real_stat = os.stat
            real_fstat = os.fstat
            sequence = iter(range(100, 200))

            def volatile(value):
                token = next(sequence)
                return types.SimpleNamespace(
                    st_mode=value.st_mode,
                    st_size=value.st_size,
                    st_dev=value.st_dev + token,
                    st_ino=value.st_ino + token,
                    st_mtime=value.st_mtime + token,
                    st_mtime_ns=getattr(value, "st_mtime_ns", 0) + token,
                )

            with patch.object(
                _bridge.os, "fstat", side_effect=lambda fd: volatile(real_fstat(fd))
            ), patch.object(
                _bridge.os,
                "stat",
                side_effect=lambda name, follow_symlinks=True: volatile(
                    real_stat(name, follow_symlinks=follow_symlinks)
                ),
            ):
                proof = _bridge._attachment_content_snapshot(path)

        self.assertEqual(proof["size_bytes"], len(payload))
        self.assertEqual(proof["sha256"], hashlib.sha256(payload).hexdigest())

    def test_upload_accepts_fuse_metadata_refresh_but_rechecks_digest(self):
        payload = b"stable-coze-drive-bytes"
        material = {
            "material_ref": MATERIAL_REF,
            "filename": "政策原文.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(payload),
            "permission": dict(PUBLIC_PERMISSION),
        }
        proof = {
            "signature": (1, 2, len(payload), 3),
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.docx")
            with open(path, "wb") as handle:
                handle.write(payload)
            real_stat = os.stat
            real_fstat = os.fstat
            sequence = iter(range(200, 300))

            def volatile(value):
                token = next(sequence)
                return types.SimpleNamespace(
                    st_mode=value.st_mode,
                    st_size=value.st_size,
                    st_dev=value.st_dev + token,
                    st_ino=value.st_ino + token,
                    st_mtime=value.st_mtime + token,
                    st_mtime_ns=getattr(value, "st_mtime_ns", 0) + token,
                )

            with patch.object(
                _bridge, "resolve_attachment_file", return_value=(path, proof)
            ), patch.object(
                add_material_file.os,
                "fstat",
                side_effect=lambda fd: volatile(real_fstat(fd)),
            ), patch.object(
                add_material_file.os,
                "stat",
                side_effect=lambda name, follow_symlinks=True: volatile(
                    real_stat(name, follow_symlinks=follow_symlinks)
                ),
            ), patch.object(
                _bridge,
                "do_request",
                return_value=FakeResponse(201, {"action": "created", "material": material}),
            ) as request:
                result = add_material_file.add_material_file(
                    draft_id=DRAFT_ID,
                    filename="政策原文.docx",
                    permission_profile="public_web",
                    authorized_by="channel_verified",
                )

        self.assertEqual(result["_material_ref"], MATERIAL_REF)
        self.assertEqual(request.call_args.kwargs["files"]["file"][1], payload)

    def test_upload_filename_resolution_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)), patch.object(
                _bridge, "ATTACHMENT_DISCOVERY_TIMEOUT_SECONDS", 0
            ):
                with patch.object(_bridge, "do_request") as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="不存在.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        request.assert_not_called()

    def test_attachment_resolution_waits_for_delayed_direct_child(self):
        now = [100.0]
        sleeps = []
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.pdf")

            def monotonic():
                return now[0]

            def sleep(seconds):
                sleeps.append(seconds)
                now[0] += seconds
                with open(path, "wb") as handle:
                    handle.write(b"%PDF")

            resolved = _bridge.resolve_attachment_file(
                "政策原文.pdf",
                roots=(folder,),
                wait_seconds=12,
                monotonic_fn=monotonic,
                sleep_fn=sleep,
            )

        self.assertEqual(resolved, path)
        self.assertEqual(sleeps, [_bridge.ATTACHMENT_DISCOVERY_INITIAL_POLL_SECONDS])

    def test_attachment_resolution_timeout_is_bounded(self):
        now = [50.0]
        sleeps = []

        def monotonic():
            return now[0]

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "未找到"):
                _bridge.resolve_attachment_file(
                    "不存在.pdf",
                    roots=(folder,),
                    wait_seconds=0.6,
                    monotonic_fn=monotonic,
                    sleep_fn=sleep,
                )

        self.assertAlmostEqual(sum(sleeps), 0.6)
        self.assertEqual(now[0], 50.6)

    def test_attachment_security_conflict_does_not_wait(self):
        sleep = MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            for suffix, content in (("a1", b"AAAA"), ("b2", b"BBBB")):
                path = os.path.join(folder, f"政策原文_1700000000000_0_{suffix}.pdf")
                with open(path, "wb") as handle:
                    handle.write(content)
            with self.assertRaisesRegex(ValueError, "内容不同"):
                _bridge.resolve_attachment_file(
                    "政策原文.pdf",
                    roots=(folder,),
                    wait_seconds=12,
                    monotonic_fn=lambda: 1.0,
                    sleep_fn=sleep,
                )

        sleep.assert_not_called()

    def test_nested_attachment_is_not_discovered(self):
        with tempfile.TemporaryDirectory() as folder:
            nested = os.path.join(folder, "nested")
            os.mkdir(nested)
            with open(os.path.join(nested, "政策原文.pdf"), "wb") as handle:
                handle.write(b"%PDF")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)), patch.object(
                _bridge, "ATTACHMENT_DISCOVERY_TIMEOUT_SECONDS", 0
            ):
                with patch.object(_bridge, "do_request") as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        request.assert_not_called()

    def test_attachment_disappearing_before_read_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.pdf")
            with open(path, "wb") as handle:
                handle.write(b"%PDF")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(add_material_file.os, "open", side_effect=FileNotFoundError):
                    with patch.object(_bridge, "do_request") as request:
                        result = add_material_file.add_material_file(
                            draft_id=DRAFT_ID,
                            filename="政策原文.pdf",
                            permission_profile="public_web",
                            authorized_by="channel_verified",
                        )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        request.assert_not_called()

    def test_upload_duplicate_materialized_names_fail_closed_without_network(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            for folder, suffix, content in (
                (first, "a1", b"first-content"),
                (second, "b2", b"other-content"),
            ):
                path = os.path.join(folder, f"政策原文_1700000000000_0_{suffix}.pdf")
                with open(path, "wb") as handle:
                    handle.write(content)
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (first, second)):
                with patch.object(_bridge, "do_request") as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        self.assertIn("内容不同", result["错误信息"])
        request.assert_not_called()

    def test_upload_same_size_different_content_still_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix, content in (("a1", b"AAAA"), ("b2", b"BBBB")):
                path = os.path.join(folder, f"政策原文_1700000000000_0_{suffix}.pdf")
                with open(path, "wb") as handle:
                    handle.write(content)
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request") as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        self.assertIn("内容不同", result["错误信息"])
        request.assert_not_called()

    def test_upload_excessive_matching_aliases_fail_closed_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            for index in range(_bridge._MAX_MATCHING_ATTACHMENT_CANDIDATES + 1):
                path = os.path.join(
                    folder,
                    f"政策原文_1700000000000_{index}_alias{index}.pdf",
                )
                with open(path, "wb") as handle:
                    handle.write(b"same-upload")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request") as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        self.assertIn("同名附件过多", result["错误信息"])
        request.assert_not_called()

    def test_upload_rejects_selected_alias_changed_after_resolution(self):
        original_name = "政策原文.docx"
        with tempfile.TemporaryDirectory() as folder:
            exact = os.path.join(folder, original_name)
            materialized = os.path.join(folder, "政策原文_1700000000001_0_efgh.docx")
            for path in (exact, materialized):
                with open(path, "wb") as handle:
                    handle.write(b"same-upload")
            real_resolver = _bridge.resolve_attachment_file

            def resolve_then_change(*args, **kwargs):
                resolved, proof = real_resolver(*args, **kwargs)
                with open(resolved, "wb") as handle:
                    handle.write(b"replacement")
                return resolved, proof

            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "resolve_attachment_file", side_effect=resolve_then_change):
                    with patch.object(_bridge, "do_request") as request:
                        result = add_material_file.add_material_file(
                            draft_id=DRAFT_ID,
                            filename=original_name,
                            permission_profile="public_web",
                            authorized_by="channel_verified",
                        )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        request.assert_not_called()

    def test_explicit_file_path_rejects_change_after_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "政策原文.pdf")
            with open(path, "wb") as handle:
                handle.write(b"original")
            real_snapshot = _bridge._attachment_content_snapshot

            def snapshot_then_change(selected):
                proof = real_snapshot(selected)
                with open(selected, "wb") as handle:
                    handle.write(b"replacement")
                return proof

            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(
                    _bridge,
                    "_attachment_content_snapshot",
                    side_effect=snapshot_then_change,
                ):
                    with patch.object(_bridge, "do_request") as request:
                        result = add_material_file.add_material_file(
                            draft_id=DRAFT_ID,
                            file_path=path,
                            filename="政策原文.pdf",
                            permission_profile="public_web",
                            authorized_by="channel_verified",
                        )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        request.assert_not_called()

    def test_explicit_empty_attachment_roots_do_not_fall_back_to_defaults(self):
        with self.assertRaisesRegex(ValueError, "未找到"):
            _bridge.resolve_attachment_file("政策原文.pdf", roots=())

    def test_matching_symlink_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "target.pdf")
            link = os.path.join(folder, "政策原文.pdf")
            with open(target, "wb") as handle:
                handle.write(b"%PDF")
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("当前 Windows 环境不允许创建符号链接")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "do_request") as request:
                    result = add_material_file.add_material_file(
                        draft_id=DRAFT_ID,
                        filename="政策原文.pdf",
                        permission_profile="public_web",
                        authorized_by="channel_verified",
                    )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")
        request.assert_not_called()

    def test_upload_rejects_path_outside_attachment_roots(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (os.path.join(handle.name, "nope"),)):
                result = add_material_file.add_material_file(
                    draft_id=DRAFT_ID,
                    file_path=handle.name,
                    filename=os.path.basename(handle.name),
                    permission_profile="public/public_web",
                    authorized_by="channel_verified",
                )
        self.assertEqual(result["错误码"], "INVALID_ATTACHMENT")

    def test_upload_reads_at_most_limit_plus_one_and_rejects_growth(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "policy.pdf")
            with open(path, "wb") as handle:
                handle.write(b"12345")
            with patch.object(_bridge, "ATTACHMENT_ROOTS", (folder,)):
                with patch.object(_bridge, "MAX_FILE_SIZE", 4):
                    with patch.object(add_material_file, "_read_attachment_bytes") as reader:
                        with patch.object(_bridge, "do_request") as request:
                            result = add_material_file.add_material_file(
                                draft_id=DRAFT_ID,
                                file_path=path,
                                filename="policy.pdf",
                                permission_profile="public/public_web",
                                authorized_by="channel_verified",
                            )
        self.assertEqual(result["错误码"], "FILE_TOO_LARGE")
        reader.assert_not_called()
        request.assert_not_called()

    def test_get_draft_uses_backend_count(self):
        material = {
            "material_ref": MATERIAL_REF,
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "permission": {"retention_mode": "task_only"},
        }
        body = draft_body(count=3, materials=[material])
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)):
            result = get_material_draft.get_material_draft(draft_id=DRAFT_ID)
        self.assertEqual(result["材料数量"], 3)
        self.assertEqual(len(result["材料列表"]), 1)

    def test_get_draft_rejects_cross_resource_response(self):
        body = draft_body()
        body["draft_id"] = "rdraft_" + "f" * 24
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)):
            result = get_material_draft.get_material_draft(draft_id=DRAFT_ID)
        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")

    def test_remove_requires_deleted_true(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, {"deleted": False})):
            result = remove_material_file.remove_material_file(
                draft_id=DRAFT_ID, material_ref=MATERIAL_REF
            )
        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")

    def test_empty_draft_is_not_sealed(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, draft_body())) as request:
            result = seal_material_draft.seal_material_draft(draft_id=DRAFT_ID)
        self.assertEqual(result["错误码"], "EMPTY_DRAFT")
        self.assertEqual(request.call_count, 1)

    def test_already_sealed_draft_is_resumed_without_second_post(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(
            200, draft_body(status="sealed", count=1, materials=[{}])
        )) as request:
            result = seal_material_draft.seal_material_draft(draft_id=DRAFT_ID)
        self.assertEqual(result["草稿状态"], "sealed")
        self.assertEqual(request.call_count, 1)


class TestReferenceAuthorizationTools(unittest.TestCase):
    @staticmethod
    def pending_body():
        return {
            "authorization_id": REFERENCE_AUTHORIZATION_ID,
            "kind": "pasted_text",
            "state": "pending",
            "confirmation_required": True,
            "material_label": "粘贴文字（6 字）",
            "expires_at": "2099-01-01T12:00:00Z",
            "retention": "task_only",
            "reuse_policy": "same_idempotent_job_only",
            "allowed_choices": [
                "public_web", "user_owned", "licensed_enterprise", "exclude",
            ],
        }

    @staticmethod
    def confirmed_body(*, kind="url", decision="public_web", state="confirmed"):
        return {
            "authorization_id": REFERENCE_AUTHORIZATION_ID,
            "kind": kind,
            "state": state,
            "confirmation_required": False,
            "material_label": "国家金融监督管理总局",
            "expires_at": "2099-01-01T12:00:00Z",
            "retention": "task_only",
            "reuse_policy": "same_idempotent_job_only",
            "decision": decision,
        }

    def test_government_url_auto_confirms_with_separate_user_projection(self):
        with patch.object(
            _bridge,
            "do_request",
            return_value=FakeResponse(201, self.confirmed_body()),
        ) as request:
            result = prepare_reference_material.prepare_reference_material(
                kind="url", content=REFERENCE_URL,
            )
        self.assertEqual(
            request.call_args.kwargs["json_body"],
            {"kind": "url", "content": REFERENCE_URL},
        )
        self.assertEqual(
            request.call_args.args,
            ("POST", "/integrations/research/reference-authorizations/prepare"),
        )
        self.assertEqual(result["用户可见信息"]["授权状态"], "政府官网已自动确认")
        self.assertFalse(result["用户可见信息"]["需要用户确认"])
        self.assertEqual(result["_reference_item"], REFERENCE_ITEM)
        serialized = json.dumps(result["用户可见信息"], ensure_ascii=False)
        self.assertNotIn("rauth_", serialized)
        self.assertNotIn("permission", serialized)

    def test_pasted_text_returns_clear_choices_and_opaque_context_only(self):
        with patch.object(
            _bridge,
            "do_request",
            return_value=FakeResponse(201, self.pending_body()),
        ):
            result = prepare_reference_material.prepare_reference_material(
                kind="pasted_text", content="政策原文",
            )
        self.assertTrue(result["用户可见信息"]["需要用户确认"])
        self.assertEqual(
            [item["选择"] for item in result["用户可见信息"]["授权选项"]],
            ["公开网页", "本人合法取得", "企业已授权", "不使用该材料"],
        )
        context = result["_authorization_context"]
        self.assertEqual(set(context), {
            "authorization_id", "kind", "content",
        })
        self.assertNotIn("rauth_", json.dumps(result["用户可见信息"], ensure_ascii=False))

    def test_confirm_maps_chinese_choice_and_returns_submit_item(self):
        context = {
            "authorization_id": REFERENCE_AUTHORIZATION_ID,
            "kind": "pasted_text",
            "content": "政策原文",
        }
        response = self.confirmed_body(kind="pasted_text", decision="user_owned")
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, response)) as request:
            result = confirm_reference_material.confirm_reference_material(
                authorization_context_raw=json.dumps(context),
                choice="本人合法取得",
            )
        self.assertEqual(
            request.call_args.kwargs["json_body"],
            {"choice": "user_owned"},
        )
        self.assertEqual(
            request.call_args.args,
            (
                "POST",
                "/integrations/research/reference-authorizations/"
                f"{REFERENCE_AUTHORIZATION_ID}/confirm",
            ),
        )
        self.assertEqual(result["用户可见信息"]["材料使用状态"], "可提交")
        self.assertEqual(result["_reference_item"], {
            "authorization_id": REFERENCE_AUTHORIZATION_ID,
            "kind": "pasted_text",
            "content": "政策原文",
        })
        self.assertNotIn("permission", json.dumps(result, ensure_ascii=False))

    def test_exclude_does_not_return_a_submit_item(self):
        context = {
            "authorization_id": REFERENCE_AUTHORIZATION_ID,
            "kind": "pasted_text",
            "content": "政策原文",
        }
        response = self.confirmed_body(
            kind="pasted_text", decision="exclude", state="excluded",
        )
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, response)):
            result = confirm_reference_material.confirm_reference_material(
                authorization_context_raw=context,
                choice="不使用该材料",
            )
        self.assertEqual(result["用户可见信息"]["材料使用状态"], "不用于本次研究")
        self.assertNotIn("_reference_item", result)

    def test_prepare_rejects_credentials_embedded_in_url_without_network(self):
        with patch.object(_bridge, "do_request") as request:
            result = prepare_reference_material.prepare_reference_material(
                kind="url", content="https://user:pass@example.com/report",
            )
        self.assertEqual(result["错误码"], "INVALID_REFERENCE")
        request.assert_not_called()

    def test_prepare_normalizes_url_before_authorization(self):
        normalized = "https://www.nfra.gov.cn/"
        body = self.confirmed_body()
        with patch.object(_bridge, "do_request", return_value=FakeResponse(201, body)) as request:
            result = prepare_reference_material.prepare_reference_material(
                kind="url",
                content="HTTPS://WWW.NFRA.GOV.CN:443#fragment",
            )
        self.assertEqual(
            request.call_args.kwargs["json_body"]["content"],
            normalized,
        )
        self.assertEqual(result["_reference_item"]["content"], normalized)

    def test_prepare_rejects_large_pasted_text_and_directs_to_file_without_network(self):
        with patch.object(_bridge, "do_request") as request:
            result = prepare_reference_material.prepare_reference_material(
                kind="pasted_text",
                content="政" * 6667,
            )
        self.assertEqual(result["错误码"], "INVALID_REFERENCE")
        self.assertIn("文件上传", result["错误信息"])
        request.assert_not_called()

    def test_prepare_preserves_policy_text_compatibility_characters(self):
        original = "ＡＢＣ第①条，利率５％，等级Ⅳ\r\n下一行"
        expected = "ＡＢＣ第①条，利率５％，等级Ⅳ\n下一行"
        body = self.pending_body()
        with patch.object(
            _bridge,
            "do_request",
            return_value=FakeResponse(201, body),
        ) as request:
            result = prepare_reference_material.prepare_reference_material(
                kind="pasted_text",
                content=original,
            )
        self.assertEqual(request.call_args.kwargs["json_body"]["content"], expected)
        self.assertEqual(result["_authorization_context"]["content"], expected)

    def test_prepare_fails_closed_on_invalid_server_reuse_scope(self):
        body = self.pending_body()
        body["reuse_policy"] = "cross_job"
        with patch.object(_bridge, "do_request", return_value=FakeResponse(201, body)):
            result = prepare_reference_material.prepare_reference_material(
                kind="pasted_text", content="政策原文",
            )
        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")

    def test_confirm_rejects_tampered_internal_context_without_network(self):
        with patch.object(_bridge, "do_request") as request:
            result = confirm_reference_material.confirm_reference_material(
                authorization_context_raw=json.dumps({
                    "authorization_id": REFERENCE_AUTHORIZATION_ID,
                    "kind": "pasted_text",
                    "content": "政策原文",
                    "permission": {"scope": "public"},
                }),
                choice="公开网页",
            )
        self.assertEqual(result["错误码"], "INVALID_REFERENCE_CONFIRMATION")
        request.assert_not_called()

    def test_confirm_rejects_internal_enum_instead_of_user_facing_choice(self):
        context = {
            "authorization_id": REFERENCE_AUTHORIZATION_ID,
            "kind": "pasted_text",
            "content": "政策原文",
        }
        with patch.object(_bridge, "do_request") as request:
            result = confirm_reference_material.confirm_reference_material(
                authorization_context_raw=context,
                choice="user_owned",
            )
        self.assertEqual(result["错误码"], "INVALID_REFERENCE_CONFIRMATION")
        request.assert_not_called()


class TestResearchTools(unittest.TestCase):
    def test_submit_returns_immediate_ack_and_ends_current_turn(self):
        response = FakeResponse(202, job_status_body(), {"Retry-After": "5"})
        with patch.object(_bridge, "do_request", return_value=response):
            result = submit_research_job.submit_research_job(
                policy_input="分析消费金融政策影响",
                operation_id="op-immediate-ack",
            )

        self.assertEqual(result["提交结果"], "研究任务已受理")
        self.assertTrue(result["本轮应结束"])
        self.assertFalse(result["自动跨轮推送"])
        self.assertIn("查看进度", result["立即回复用户"])
        self.assertIn("上个任务", result["立即回复用户"])
        self.assertIn("不支持", result["立即回复用户"])
        self.assertEqual(result["_job_id"], JOB_ID)

    def test_submit_same_request_is_idempotent_across_operation_ids(self):
        response = FakeResponse(202, job_status_body(), {"Retry-After": "5"})
        with patch.object(_bridge, "do_request", return_value=response) as request:
            first = submit_research_job.submit_research_job(
                policy_input="分析示例消费金融政策对消费金融公司的影响",
                operation_id="op-policy-001",
                reference_items_raw=json.dumps([REFERENCE_ITEM]),
            )
        body = request.call_args.kwargs["json_body"]
        self.assertIsInstance(body["output_spec"], dict)
        self.assertEqual(body["output_spec"]["report_profile"], "standard_report")
        self.assertNotIn("target_length", body["output_spec"])
        self.assertEqual(body["output_spec"]["formats"], ["docx", "pdf"])
        self.assertIn("idempotency_key", body)
        self.assertEqual(body["reference_items"], [REFERENCE_ITEM])
        self.assertNotIn("source_urls", body)
        self.assertNotIn("material_seeds", body)
        self.assertEqual(first["_idempotency_key"], body["idempotency_key"])

        with patch.object(_bridge, "do_request", return_value=response):
            second = submit_research_job.submit_research_job(
                policy_input="分析示例消费金融政策对消费金融公司的影响",
                operation_id="op-policy-001",
                reference_items_raw=json.dumps([REFERENCE_ITEM]),
            )
            third = submit_research_job.submit_research_job(
                policy_input="分析示例消费金融政策对消费金融公司的影响",
                operation_id="op-policy-002",
                reference_items_raw=json.dumps([REFERENCE_ITEM]),
            )
        self.assertEqual(first["_idempotency_key"], second["_idempotency_key"])
        self.assertEqual(first["_idempotency_key"], third["_idempotency_key"])

        with patch.object(_bridge, "do_request", return_value=response):
            changed_request = submit_research_job.submit_research_job(
                policy_input="分析示例消费金融政策对消费金融公司的影响，并补充实施时间表",
                operation_id="op-policy-003",
                reference_items_raw=json.dumps([REFERENCE_ITEM]),
            )
        self.assertNotEqual(first["_idempotency_key"], changed_request["_idempotency_key"])

    def test_submit_business_idempotency_ignores_random_authorization_id(self):
        response = FakeResponse(202, job_status_body())
        same_material_new_handle = {
            **REFERENCE_ITEM,
            "authorization_id": "rauth_" + "S" * 32,
        }
        with patch.object(_bridge, "do_request", return_value=response) as request:
            first = submit_research_job.submit_research_job(
                policy_input="分析政策影响",
                operation_id="op-reference-first",
                reference_items_raw=json.dumps([REFERENCE_ITEM]),
            )
            second = submit_research_job.submit_research_job(
                policy_input="分析政策影响",
                operation_id="op-reference-second",
                reference_items_raw=json.dumps([same_material_new_handle]),
            )
        self.assertEqual(first["_idempotency_key"], second["_idempotency_key"])
        sent_bodies = [call.kwargs["json_body"] for call in request.call_args_list]
        self.assertNotEqual(
            sent_bodies[0]["reference_items"][0]["authorization_id"],
            sent_bodies[1]["reference_items"][0]["authorization_id"],
        )

    def test_submit_canonicalizes_reference_order_in_key_and_post_body(self):
        response = FakeResponse(202, job_status_body())
        pasted_item = {
            "authorization_id": "rauth_" + "S" * 32,
            "kind": "pasted_text",
            "content": "政策原文摘录",
        }
        forward = [REFERENCE_ITEM, pasted_item]
        reverse = list(reversed(forward))
        with patch.object(_bridge, "do_request", return_value=response) as request:
            first = submit_research_job.submit_research_job(
                policy_input="分析政策影响",
                operation_id="op-order-first",
                reference_items_raw=json.dumps(forward),
            )
            second = submit_research_job.submit_research_job(
                policy_input="分析政策影响",
                operation_id="op-order-second",
                reference_items_raw=json.dumps(reverse),
            )
        sent_bodies = [call.kwargs["json_body"] for call in request.call_args_list]
        self.assertEqual(first["_idempotency_key"], second["_idempotency_key"])
        self.assertEqual(sent_bodies[0], sent_bodies[1])
        self.assertEqual(
            [(item["kind"], item["content"]) for item in sent_bodies[0]["reference_items"]],
            sorted((item["kind"], item["content"]) for item in forward),
        )

    def test_submit_terminal_predecessor_versions_an_explicit_rerun(self):
        response = FakeResponse(202, job_status_body())
        completed = FakeResponse(200, job_status_body("completed"))
        common = {
            "policy_input": "分析示例消费金融政策对消费金融公司的影响",
            "reference_items_raw": json.dumps([REFERENCE_ITEM]),
        }
        with patch.object(_bridge, "do_request", return_value=response):
            original = submit_research_job.submit_research_job(
                operation_id="op-policy-original",
                **common,
            )
        with patch.object(
            _bridge,
            "do_request",
            side_effect=[completed, response, completed, response],
        ) as request:
            rerun_first = submit_research_job.submit_research_job(
                operation_id="op-policy-rerun-a",
                rerun_after_job_id=JOB_ID,
                **common,
            )
            rerun_retry = submit_research_job.submit_research_job(
                operation_id="op-policy-rerun-b",
                rerun_after_job_id=JOB_ID,
                **common,
            )

        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [
                ("GET", f"/integrations/research/jobs/{JOB_ID}"),
                ("POST", "/integrations/research/jobs"),
                ("GET", f"/integrations/research/jobs/{JOB_ID}"),
                ("POST", "/integrations/research/jobs"),
            ],
        )
        submitted_bodies = [
            call.kwargs["json_body"]
            for call in request.call_args_list
            if call.args[0] == "POST"
        ]
        self.assertTrue(all("rerun_after_job_id" not in body for body in submitted_bodies))
        business_bodies = [
            {key: value for key, value in body.items() if key != "idempotency_key"}
            for body in submitted_bodies
        ]
        self.assertTrue(all(body == business_bodies[0] for body in business_bodies[1:]))
        self.assertNotEqual(original["_idempotency_key"], rerun_first["_idempotency_key"])
        self.assertEqual(rerun_first["_idempotency_key"], rerun_retry["_idempotency_key"])

    def test_submit_rejects_invalid_rerun_source_without_network(self):
        with patch.object(_bridge, "do_request") as request:
            result = submit_research_job.submit_research_job(
                policy_input="分析某项消费金融监管政策的行业与公司影响",
                operation_id="op-invalid-rerun",
                rerun_after_job_id="not-a-job-id",
            )
        self.assertEqual(result["错误码"], "INVALID_HANDLE")
        request.assert_not_called()

    def test_submit_rejects_rerun_while_predecessor_is_running(self):
        with patch.object(
            _bridge,
            "do_request",
            return_value=FakeResponse(200, job_status_body("running")),
        ) as request:
            result = submit_research_job.submit_research_job(
                policy_input="分析某项消费金融监管政策的行业与公司影响",
                operation_id="op-running-rerun",
                rerun_after_job_id=JOB_ID,
            )
        self.assertEqual(result["错误码"], "RERUN_SOURCE_NOT_TERMINAL")
        self.assertTrue(result["可重试"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")

    def test_submit_accepts_detailed_policy_input_over_200_chars(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(202, job_status_body())):
            result = submit_research_job.submit_research_job(
                policy_input="研究要求" * 100,
                operation_id="op-long-input",
            )
        self.assertEqual(result["status"], "success")

    def test_submit_rejects_model_invented_permission_fields_without_network(self):
        malicious = [{
            **REFERENCE_ITEM,
            "permission": {"scope": "public", "authorized_by": "channel_verified"},
        }]
        with patch.object(_bridge, "do_request") as request:
            result = submit_research_job.submit_research_job(
                policy_input="分析某项消费金融监管政策的行业与公司影响",
                operation_id="op-raw-permission",
                reference_items_raw=json.dumps(malicious),
            )
        self.assertEqual(result["错误码"], "INVALID_JSON")
        request.assert_not_called()

    def test_submit_rejects_duplicate_reference_content_without_network(self):
        duplicate = [
            REFERENCE_ITEM,
            {**REFERENCE_ITEM, "authorization_id": "rauth_" + "S" * 32},
        ]
        with patch.object(_bridge, "do_request") as request:
            result = submit_research_job.submit_research_job(
                policy_input="分析某项消费金融监管政策的行业与公司影响",
                operation_id="op-duplicate-reference",
                reference_items_raw=json.dumps(duplicate),
            )
        self.assertEqual(result["错误码"], "INVALID_JSON")
        request.assert_not_called()

    def test_submit_rejects_excessive_total_pasted_text_without_network(self):
        items = [
            {
                "authorization_id": "rauth_" + marker * 32,
                "kind": "pasted_text",
                "content": text,
            }
            for marker, text in (
                ("A", "a" * 15_000),
                ("B", "b" * 15_000),
                ("C", "c" * 10_001),
            )
        ]
        with patch.object(_bridge, "do_request") as request:
            result = submit_research_job.submit_research_job(
                policy_input="分析某项消费金融监管政策的行业与公司影响",
                operation_id="op-pasted-total-limit",
                reference_items_raw=json.dumps(items),
            )
        self.assertEqual(result["错误码"], "INVALID_JSON")
        self.assertIn("合计不能超过 40000", result["错误信息"])
        request.assert_not_called()

    def test_submit_accepts_exact_total_pasted_text_boundary(self):
        items = [
            {
                "authorization_id": "rauth_" + marker * 32,
                "kind": "pasted_text",
                "content": marker.lower() * 20_000,
            }
            for marker in ("A", "B")
        ]
        with patch.object(
            _bridge,
            "do_request",
            return_value=FakeResponse(202, job_status_body()),
        ) as request:
            result = submit_research_job.submit_research_job(
                policy_input="分析某项消费金融监管政策的行业与公司影响",
                operation_id="op-pasted-total-boundary",
                reference_items_raw=json.dumps(items),
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(request.call_args.kwargs["json_body"]["reference_items"]), 2)

    def test_status_projects_progress_and_retry_after(self):
        with patch.object(_bridge, "do_request", return_value=FakeResponse(
            200, job_status_body("running"), {"Retry-After": "5"}
        )):
            result = get_research_job_status.get_research_job_status(job_id=JOB_ID)
        self.assertEqual(result["当前阶段"], "policy_verification")
        self.assertEqual(result["完成比例"], 25)
        self.assertEqual(result["建议等待秒"], 5)

    def test_terminal_status_does_not_project_unverified_failure_reason(self):
        body = job_status_body("completed_with_gaps")
        body["delivery_notice"] = {
            "error_code": "E_FACT_GATE_FAILED",
            "message": "缺行业研究和主流媒体资料",
        }

        result = _bridge.project_job_status(body)

        self.assertEqual(
            result["交付说明"],
            "任务已结束，请查询一次最终结果并直接交付可用报告或终态说明",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("E_FACT_GATE_FAILED", serialized)
        self.assertNotIn("行业研究", serialized)

    def test_running_status_suppresses_mid_task_confirmation_request(self):
        body = job_status_body("running")
        body["delivery_notice"] = (
            "请确认这5篇公开来源并补充材料后重新提交"
        )
        body["recovery_required"] = True

        result = _bridge.project_job_status(body)

        self.assertEqual(
            result["交付说明"],
            "任务由系统继续处理，不需要你额外确认、补充材料或重新提交",
        )
        self.assertTrue(result["后台恢复处理中"])
        self.assertTrue(result["用户无需操作"])

    def test_running_status_suppresses_legacy_request_to_provide_links(self):
        legacy_requests = (
            "请提供监管官网链接后再继续",
            "你手上还有相关报告，可以直接发我；否则我先暂停。",
            "要我现在就去搜吗？",
            "可以补充监管官网链接后再继续。",
            "如有更多资料，可继续提供。",
        )
        for notice in legacy_requests:
            with self.subTest(notice=notice):
                body = job_status_body("running")
                body["delivery_notice"] = notice

                result = _bridge.project_job_status(body)

                self.assertEqual(
                    result["交付说明"],
                    "任务由系统继续处理，不需要你额外确认、补充材料或重新提交",
                )
                self.assertTrue(result["用户无需操作"])

    def test_failed_or_cancelled_status_does_not_offer_unavailable_result_query(self):
        expected = {
            "failed": "任务已结束，未生成可交付结果；不会自动重跑",
            "cancelled": "任务已取消，未生成可交付结果",
        }
        for status, notice in expected.items():
            with self.subTest(status=status):
                body = job_status_body(status)
                body["delivery_notice"] = {"error_code": "E_INTERNAL", "message": "raw"}

                result = _bridge.project_job_status(body)

                self.assertEqual(result["交付说明"], notice)
                self.assertNotIn("查询一次最终结果", result["交付说明"])
                self.assertNotIn("E_INTERNAL", json.dumps(result, ensure_ascii=False))

    def test_status_rejects_cross_resource_response(self):
        body = job_status_body("running")
        body["job_id"] = "rjob_" + "f" * 24
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)):
            result = get_research_job_status.get_research_job_status(job_id=JOB_ID)
        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")

    def test_result_projects_authoritative_body_char_count(self):
        cases = {
            "top_level": (26001, 999, 26001),
            "package_fallback": (None, "24002", 24002),
            "malformed_falls_back_to_body_length": ("not-a-number", None, 4),
            "negative_falls_back_to_body_length": (-5, None, 4),
        }
        for label, (top_level, package_value, expected) in cases.items():
            with self.subTest(label=label):
                body = result_body(
                    quality_passed=True,
                    delivery_state="verified_report",
                    human_review_required=False,
                    artifacts=[report_artifact("docx"), report_artifact("pdf")],
                )
                if top_level is not None:
                    body["body_char_count"] = top_level
                if package_value is not None:
                    body["delivery"]["research_report_package"][
                        "body_char_count"
                    ] = package_value

                result = _bridge.project_result(body)

                self.assertEqual(result["报告正文字符数"], expected)

    def test_unvalidated_legacy_output_does_not_bypass_backend_trust_contract(self):
        body = {
            "job_id": JOB_ID,
            "status": "completed_with_gaps",
            "limited_delivery": True,
            "delivery_notice": {"error_code": "E_GAPS", "message": "有待核资料"},
            "quality_passed": False,
            "delivery_state": "limited",
            "delivery": {"research_report_package": {"material_usage_ledger": [{"name": "a.pdf"}]}},
            "body_char_count": 12345,
            "artifacts": [{
                "artifact_id": ARTIFACT_ID,
                "format": "docx",
                "filename": "政策研究报告.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "status": "ready",
                "byte_size": 1234,
                "artifact_sha256": "never-expose",
            }],
            "snapshot": {
                "executive_summary": "摘要",
                "key_conclusions": ["结论一"],
                "impacts": ["影响一"],
                "actions": ["建议一"],
                "coverage_gaps": ["缺口一"],
                "supplement_suggestions": ["补充一"],
                "source_list": [{
                    "title": "监管原文", "source_level": "P0",
                    "url_or_locator": "https://www.nfra.gov.cn/policy",
                    "evidence_id": "never-expose",
                }],
                "evidence_ledger": [{"secret": "raw-snapshot"}],
            },
        }
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)):
            result = get_research_job_result.get_research_job_result(job_id=JOB_ID)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["完成类型"], "completed_with_gaps")
        self.assertTrue(result["降级交付"])
        self.assertFalse(result["可交付"])
        self.assertFalse(result["可供讨论参考"])
        self.assertEqual(result["交付状态"], "本次未形成报告文件")
        self.assertEqual(result["报告正文字符数"], 0)
        self.assertIn("未返回显式可信的正式报告或参考稿合同", result["交付说明"])
        self.assertEqual(result["报告局限"], [])
        self.assertEqual(result["可选完善方向"], [])
        self.assertEqual(result["内容摘要"], "")
        self.assertEqual(result["关键结论"], [])
        self.assertEqual(result["影响分析"], [])
        self.assertEqual(result["对策建议"], [])
        self.assertEqual(result["参考来源"], [])
        self.assertEqual(result["报告文件"], [])
        self.assertEqual(result["_artifact_ids"], [])
        self.assertTrue(result["用户无需操作"])
        self.assertNotIn("evidence_ledger", serialized)
        self.assertNotIn("artifact_sha256", serialized)
        self.assertNotIn("never-expose", serialized)
        self.assertNotIn("raw-snapshot", serialized)
        self.assertNotIn("E_GAPS", serialized)

    def test_evidence_gap_brief_never_turns_gaps_into_user_actions(self):
        body = {
            "job_id": JOB_ID,
            "status": "completed_with_gaps",
            "limited_delivery": True,
            "delivery_notice": "",
            "quality_passed": False,
            "delivery_state": "evidence_gap_brief",
            "delivery": {},
            "artifacts": [],
            "snapshot": {
                "coverage_gaps": ["缺少目标政策正式原文"],
                "supplement_suggestions": ["请提供监管部门正式发布链接"],
            },
        }

        result = _bridge.project_result(body)

        self.assertFalse(result["可交付"])
        self.assertEqual(result["报告局限"], [])
        self.assertEqual(result["可选完善方向"], [])
        self.assertEqual(
            result["交付说明"],
            "本次任务已经结束，但后端没有返回经过安全校验的可读报告正文或文件。"
            "你无需补充材料、链接或授权，系统不会自动重跑。",
        )
        self.assertNotIn("请提供", json.dumps(result, ensure_ascii=False))

    def test_evidence_gap_brief_without_both_action_fields_does_not_request_material(self):
        for gaps, suggestions in (([], []), (["缺口"], []), ([], ["建议"])):
            with self.subTest(gaps=gaps, suggestions=suggestions):
                body = {
                    "job_id": JOB_ID,
                    "status": "completed_with_gaps",
                    "limited_delivery": True,
                    "delivery_notice": "",
                    "quality_passed": False,
                    "delivery_state": "evidence_gap_brief",
                    "delivery": {},
                    "artifacts": [],
                    "snapshot": {
                        "coverage_gaps": gaps,
                        "supplement_suggestions": suggestions,
                    },
                }

                result = _bridge.project_result(body)

                self.assertEqual(result["报告局限"], [])
                self.assertEqual(result["可选完善方向"], [])
                self.assertEqual(
                    result["交付说明"],
                    "本次任务已经结束，但后端没有返回经过安全校验的可读报告正文或文件。"
                    "你无需补充材料、链接或授权，系统不会自动重跑。",
                )
                self.assertNotIn("请按", result["交付说明"])

    def test_passed_corpus_legacy_content_gate_uses_canonical_no_action_notice(self):
        cases = {
            "E_ACTION_FEASIBILITY_INCOMPLETE": "行动建议可行性校验",
            "E_IMPACT_ANALYSIS_INCOMPLETE": "影响分析完整性校验",
        }
        forbidden_phrases = (
            "补充材料再研究",
            "调整范围再研究",
            "你想走哪条",
            "几个选择",
            "换个思路",
            "直接用联网搜索",
            "绕过后端",
        )
        for error_code, expected_stage in cases.items():
            with self.subTest(error_code=error_code):
                body = result_body(
                    quality_passed=False,
                    delivery_state="preliminary_dossier",
                    human_review_required=True,
                    artifacts=[],
                )
                body["errors"] = [error_code]
                body["delivery"]["corpus_status"] = "PASS"
                package = body["delivery"]["research_report_package"]
                package["body_char_count"] = 0
                package["report_body"] = ""

                result = _bridge.project_result(body)
                serialized = json.dumps(result, ensure_ascii=False)

                self.assertFalse(result["可交付"])
                self.assertTrue(result["用户无需操作"])
                self.assertIn("公开资料检索与研究语料构建已经通过", result["交付说明"])
                self.assertIn(expected_stage, result["交付说明"])
                self.assertIn("来源覆盖提示不是本次停止原因", result["交付说明"])
                self.assertIn("系统不会自动重跑", result["交付说明"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])
                self.assertEqual(result["报告局限"], [])
                self.assertEqual(result["可选完善方向"], [])
                self.assertNotIn(error_code, serialized)
                for phrase in forbidden_phrases:
                    self.assertNotIn(phrase, serialized)

    def test_hard_output_gate_takes_priority_over_legacy_content_gate_notice(self):
        body = result_body(
            quality_passed=False,
            delivery_state="preliminary_dossier",
            human_review_required=True,
            artifacts=[],
        )
        body["errors"] = [
            "E_ACTION_FEASIBILITY_INCOMPLETE",
            "E_MACHINE_FIELD_LEAK",
        ]
        body["delivery"]["corpus_status"] = "PASS"
        package = body["delivery"]["research_report_package"]
        package["body_char_count"] = 0
        package["report_body"] = ""

        result = _bridge.project_result(body)

        self.assertFalse(result["可交付"])
        self.assertTrue(result["用户无需操作"])
        self.assertIn("可公开阅读稿的机器字段清理检查", result["交付说明"])
        self.assertNotIn("行动建议可行性校验阶段结束", result["交付说明"])

    def test_body_only_content_quality_result_is_delivered_as_reference_copy(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[],
        )
        body["errors"] = ["E_ACTION_FEASIBILITY_INCOMPLETE"]
        body["delivery"]["corpus_status"] = "PASS"
        apply_v3_reference_candidate_contract(
            body, ["E_ACTION_FEASIBILITY_INCOMPLETE"]
        )

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertTrue(result["可供讨论参考"])
        self.assertTrue(result["报告正文"].strip())
        self.assertEqual(result["报告文件"], [])
        self.assertGreaterEqual(result["报告正文字符数"], 1000)
        self.assertTrue(result["用户无需操作"])
        self.assertEqual(result["对策建议"], [])

    def test_single_ready_file_content_quality_result_is_delivered_without_body(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        body["errors"] = ["E_SOURCE_ASSURANCE_LIMITED"]
        package = body["delivery"]["research_report_package"]
        package["body_char_count"] = 0
        package["report_body"] = ""

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertTrue(result["可供讨论参考"])
        self.assertEqual(result["报告正文"], "")
        self.assertEqual(len(result["报告文件"]), 1)
        self.assertEqual(result["报告文件"][0]["格式"], "docx")
        self.assertTrue(any(
            "来源类别或交叉核验覆盖" in item for item in result["报告局限"]
        ))

    def test_reference_candidate_projects_as_deliverable_human_review_copy(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["delivery_notice"] = {
            "error_code": "E_SEMANTIC_REVIEW",
            "message": "E_SEMANTIC_REVIEW：少量判断需要人工复核",
        }

        result = _bridge.project_result(body)

        self.assertFalse(result["质量检查通过"])
        self.assertTrue(result["可交付"])
        self.assertTrue(result["可供讨论参考"])
        self.assertEqual(result["交付状态"], "可供讨论参考稿")
        self.assertTrue(result["需人工复核"])
        self.assertEqual(
            result["交付说明"],
            "本报告已基于现有材料完成，可供研究和讨论参考，使用前建议人工复核。",
        )
        self.assertNotIn("权威来源覆盖限制", result["交付说明"])
        self.assertEqual(
            result["_artifact_ids"], [ARTIFACT_ID, RENDER_ARTIFACT_ID]
        )
        self.assertEqual([item["格式"] for item in result["报告文件"]], ["docx", "pdf"])
        self.assertEqual(
            [item["文件名"] for item in result["报告文件"]],
            [
                "消费金融政策研究报告（供讨论参考，需人工复核）.docx",
                "消费金融政策研究报告（供讨论参考，需人工复核）.pdf",
            ],
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("reference_candidate", serialized)
        self.assertNotIn("consumer_finance_policy", serialized)
        self.assertNotIn("E_SEMANTIC_REVIEW", serialized)

    def test_deliverable_projects_only_used_web_and_user_file_references(self):
        body = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        web_url = "https://www.nfra.gov.cn/cn/article?id=7&month=07"
        exact_filename = "[点击](https://evil.example/a)示例材料.pdf"
        body["snapshot"]["source_list"] = [
            {
                "sequence": 1,
                "title": "监管政策原文",
                "source_title": "监管政策原文",
                "publisher": "国家金融监督管理总局",
                "published_date": "2026-07-15",
                "url": web_url,
                "url_or_locator": web_url,
                "source_type": "web",
                "source_material_ids": [],
                "locators": ["正文第3段"],
            },
            {
                "sequence": 2,
                "title": exact_filename,
                "source_title": exact_filename,
                "publisher": "",
                "published_date": "",
                "url": "",
                "url_or_locator": "用户提供文件（无公开链接）",
                "source_type": "user_file",
                "source_material_ids": ["dmat_user"],
                "locators": ["第2页"],
            },
            {
                "sequence": 3,
                "title": "未采用网页",
                "url": "https://unused.example/report",
                "source_type": "web",
                "use_decision": "not_used",
            },
            {
                "sequence": 4,
                "title": "缺少原始链接的网页",
                "url": "",
                "url_or_locator": "内部定位符",
                "source_type": "web",
            },
        ]

        result = _bridge.project_result(body)

        self.assertEqual(result["参考来源"], [
            {
                "来源类型": "网页",
                "标题": "监管政策原文",
                "文件名": "",
                "安全显示文本": "监管政策原文",
                "Markdown显示行": f"[监管政策原文]({web_url})",
                "来源等级": "",
                "链接": web_url,
                "位置或说明": "正文第3段",
            },
            {
                "来源类型": "用户文件",
                "标题": exact_filename,
                "文件名": exact_filename,
                "安全显示文本": (
                    "&#91;点击&#93;&#40;https&#58;&#47;&#47;evil&#46;example"
                    "&#47;a&#41;示例材料&#46;pdf"
                ),
                "Markdown显示行": (
                    "&#91;点击&#93;&#40;https&#58;&#47;&#47;evil&#46;example"
                    "&#47;a&#41;示例材料&#46;pdf"
                    "（用户提供文件（无公开链接））"
                ),
                "来源等级": "",
                "链接": "",
                "位置或说明": "用户提供文件（无公开链接）",
            },
        ])
        self.assertNotIn("unused.example", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("内部定位符", json.dumps(result, ensure_ascii=False))

    def test_canonical_sources_do_not_guess_legacy_fields_or_title_links(self):
        body = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        safe_url = "https://example.gov.cn/real"
        body["snapshot"]["source_list"] = [{
            "source_type": "web",
            "source_title": "监管[原文](https://evil.example/title)",
            "url": safe_url,
            "original_url": "https://evil.example/fallback",
        }, {
            "source_type": "web",
            "source_title": "只有旧字段的网页",
            "original_url": "https://evil.example/legacy",
        }, {
            "source_type": "uploaded_file",
            "source_title": "旧类型文件.pdf",
            "url": "",
        }]

        result = _bridge.project_result(body)

        self.assertEqual(len(result["参考来源"]), 1)
        source = result["参考来源"][0]
        self.assertEqual(source["链接"], safe_url)
        self.assertEqual(
            source["安全显示文本"],
            "监管&#91;原文&#93;&#40;https&#58;&#47;&#47;evil&#46;example"
            "&#47;title&#41;",
        )
        self.assertEqual(
            source["Markdown显示行"],
            "[监管&#91;原文&#93;&#40;https&#58;&#47;&#47;evil&#46;example"
            f"&#47;title&#41;]({safe_url})",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("evil.example/fallback", serialized)
        self.assertNotIn("evil.example/legacy", serialized)
        self.assertNotIn("旧类型文件", serialized)

    def test_missing_canonical_source_list_never_falls_back_to_raw_evidence(self):
        body = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["snapshot"].pop("source_list")
        body["snapshot"]["material_usage_ledger"] = [{
            "use_decision": "adopted",
            "used_evidence_ids": ["ev-used", "ev-blocked"],
        }]
        body["snapshot"]["evidence_ledger"] = [
            {
                "evidence_id": "ev-used",
                "title": "已采用公开来源",
                "source_type": "web",
                "url": "https://example.com/used",
                "output_eligible": True,
            },
            {
                "evidence_id": "ev-unused",
                "title": "未采用候选",
                "source_type": "web",
                "url": "https://example.com/unused",
                "output_eligible": True,
            },
            {
                "evidence_id": "ev-blocked",
                "title": "禁止输出候选",
                "source_type": "web",
                "url": "https://example.com/blocked",
                "output_eligible": False,
            },
        ]

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertEqual(result["参考来源"], [])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("example.com/used", serialized)
        self.assertNotIn("example.com/unused", serialized)
        self.assertNotIn("example.com/blocked", serialized)

    def test_public_source_urls_reject_private_ambiguous_and_custom_port_targets(self):
        body = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["snapshot"]["source_list"] = [
            {"title": "本机", "source_type": "web", "url": "http://localhost/report"},
            {"title": "私网", "source_type": "web", "url": "http://127.0.0.1/report"},
            {"title": "歧义数字", "source_type": "web", "url": "http://127.1/report"},
            {"title": "十六进制主机", "source_type": "web", "url": "http://0x7f000001/report"},
            {"title": "组播地址", "source_type": "web", "url": "http://239.255.255.250/report"},
            {"title": "自定义端口", "source_type": "web", "url": "https://example.com:8443/report"},
            {"title": "默认端口", "source_type": "web", "url": "https://example.com:443/report"},
        ]

        result = _bridge.project_result(body)

        self.assertEqual(
            [(item["标题"], item["链接"]) for item in result["参考来源"]],
            [("默认端口", "https://example.com:443/report")],
        )

    def test_reference_candidate_turns_source_gaps_into_post_delivery_limits(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["delivery_notice"] = "请确认这5篇公开来源并重新提交"
        body["snapshot"]["coverage_gaps"] = [
            {
                "lane": "industry_research",
                "description": "资料线 industry_research 无已验证证据覆盖",
                "blocking": False,
            },
            {
                "lane": "mainstream_media",
                "description": "资料线 mainstream_media 无已验证证据覆盖",
                "blocking": False,
            },
        ]
        body["snapshot"]["supplement_suggestions"] = [
            "请补材料后重提"
        ]

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertEqual(
            result["报告局限"],
            ["行业研究来源覆盖仍有限", "主流媒体来源覆盖仍有限"],
        )
        self.assertIn("部分结论受权威来源覆盖限制", result["交付说明"])
        self.assertEqual(result["可选完善方向"], [])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("重新提交", serialized)
        self.assertNotIn("请补材料", serialized)
        self.assertNotIn("industry_research", serialized)
        self.assertNotIn("mainstream_media", serialized)
        self.assertNotIn("后续如补充", serialized)

    def test_reference_candidate_naturalizes_legacy_string_gap_tokens(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["snapshot"]["coverage_gaps"] = [
            "资料线 industry_research 无已验证证据覆盖",
            "资料线 mainstream_media 无已验证证据覆盖",
            "资料线 official_policy 无已验证证据覆盖",
            "资料线 future_internal_lane 无已验证证据覆盖",
        ]

        result = _bridge.project_result(body)
        serialized = json.dumps(result, ensure_ascii=False)

        self.assertEqual(
            result["报告局限"],
            [
                "行业研究来源覆盖仍有限",
                "主流媒体来源覆盖仍有限",
                "政策原文来源覆盖仍有限",
                "相关来源覆盖仍有限",
            ],
        )
        self.assertNotIn("industry_research", serialized)
        self.assertNotIn("mainstream_media", serialized)
        self.assertNotIn("official_policy", serialized)
        self.assertNotIn("future_internal_lane", serialized)

    def test_reference_candidate_replaces_code_only_notice_with_natural_copy(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["delivery_notice"] = {
            "error_code": "E_FIRST",
            "message": "E_FIRST; E_SECOND；E_THIRD",
        }

        result = _bridge.project_result(body)

        self.assertEqual(
            result["交付说明"],
            "本报告已基于现有材料完成，可供研究和讨论参考，使用前建议人工复核。",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("E_FIRST", serialized)
        self.assertNotIn("E_SECOND", serialized)
        self.assertNotIn("E_THIRD", serialized)

    def test_candidate_trust_flags_remain_required(self):
        valid = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        missing_review = deepcopy(valid)
        missing_review["snapshot"]["human_review_required"] = False
        missing_review["delivery"]["human_review_required"] = False
        missing_review["delivery"]["research_report_package"]["human_review_required"] = False

        missing_ready_flag = deepcopy(valid)
        missing_ready_flag["delivery"]["reference_candidate_ready"] = False

        for label, body in {
            "missing human review": missing_review,
            "missing server ready flag": missing_ready_flag,
        }.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])
                self.assertEqual(result["_artifact_ids"], [])

    def test_explicit_candidate_keeps_body_and_each_safe_ready_format(self):
        valid = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        cases = {}

        missing_body = deepcopy(valid)
        missing_body["delivery"]["research_report_package"]["body_char_count"] = 0
        missing_body["delivery"]["research_report_package"]["report_body"] = ""
        cases["missing report body"] = missing_body

        empty_artifacts = deepcopy(valid)
        empty_artifacts["artifacts"] = []
        cases["empty artifacts"] = empty_artifacts

        single_format = deepcopy(valid)
        single_format["artifacts"] = [report_artifact("docx")]
        cases["only one required format"] = single_format

        failed_artifact = deepcopy(valid)
        failed_artifact["artifacts"][1]["status"] = "failed"
        cases["failed artifact"] = failed_artifact

        mismatched_handle = deepcopy(valid)
        mismatched_handle["artifacts"][0]["artifact_id"] = RENDER_ARTIFACT_ID
        cases["format and handle mismatch"] = mismatched_handle

        duplicate_format = deepcopy(valid)
        duplicate_format["artifacts"][1] = report_artifact("docx")
        cases["duplicate format and handle"] = duplicate_format

        expected_artifact_counts = {
            "missing report body": 2,
            "empty artifacts": 0,
            "only one required format": 1,
            "failed artifact": 1,
            "format and handle mismatch": 1,
            "duplicate format and handle": 0,
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertTrue(result["可交付"])
                self.assertTrue(result["可供讨论参考"])
                self.assertEqual(result["交付状态"], "可供讨论参考稿")
                self.assertEqual(
                    len(result["报告文件"]), expected_artifact_counts[label]
                )
                self.assertEqual(
                    len(result["_artifact_ids"]), expected_artifact_counts[label]
                )
                if label != "missing report body":
                    self.assertEqual(result["报告正文"], "正文内容")
                self.assertTrue(result["用户无需操作"])

    def test_content_quality_gates_downgrade_but_do_not_block_complete_report(self):
        quality_cases = {
            "E_IMPACT_ANALYSIS_INCOMPLETE": "影响分析未通过完整性复核",
            "E_ACTION_FEASIBILITY_INCOMPLETE": "行动建议未通过可行性复核",
            "E_POLICY_TEMPORAL_CONFLICT": "日期、效力状态或先后关系",
            "E_FACT_EMPTY": "事实、数字或政策效力依据",
            "E_REFERENCE_INTEGRITY": "事实、数字或政策效力依据",
            "E_UNRESOLVED_CONFLICT": "事实、数字或政策效力依据",
            "E_UNGROUNDED_NUMBER": "事实、数字或政策效力依据",
            "E_UNRESOLVED_POLICY_STATUS": "日期、效力状态或先后关系",
            "E_REPORT_USEFULNESS_INSUFFICIENT": "需求覆盖或实用性",
        }
        for error_code, expected_limit in quality_cases.items():
            with self.subTest(error_code=error_code):
                body = result_body(
                    quality_passed=False,
                    delivery_state="reference_candidate",
                    human_review_required=True,
                    artifacts=[report_artifact("docx"), report_artifact("pdf")],
                )
                body["errors"] = [error_code]
                apply_v3_reference_candidate_contract(body, [error_code])

                result = _bridge.project_result(body)

                self.assertTrue(result["可交付"])
                self.assertTrue(result["可供讨论参考"])
                self.assertTrue(result["需人工复核"])
                self.assertEqual(result["交付状态"], "可供讨论参考稿")
                self.assertEqual(len(result["报告文件"]), 2)
                self.assertTrue(
                    any(expected_limit in item for item in result["报告局限"]),
                    result["报告局限"],
                )
                self.assertNotIn(error_code, json.dumps(result, ensure_ascii=False))

                if "ACTION" in error_code:
                    self.assertEqual(result["对策建议"], [])
                    self.assertIn("已从对话摘要中删除", result["建议处理说明"])

    def test_clean_v3_rebuild_is_not_reblocked_by_discarded_writer_findings(self):
        rebuilt_error_codes = (
            "E_MACHINE_FIELD_LEAK",
            "E_SENSITIVE_DATA",
            "E_SCHEMA_INVALID",
            "E_TYPE_CONTRACT",
        )
        for error_code in rebuilt_error_codes:
            with self.subTest(error_code=error_code):
                body = result_body(
                    quality_passed=False,
                    delivery_state="reference_candidate",
                    human_review_required=True,
                    artifacts=[report_artifact("docx")],
                )
                body["errors"] = [error_code]
                apply_v3_reference_candidate_contract(body, [error_code])

                result = _bridge.project_result(body)
                serialized = json.dumps(result, ensure_ascii=False)

                self.assertTrue(result["可交付"])
                self.assertTrue(result["可供讨论参考"])
                self.assertGreaterEqual(result["报告正文字符数"], 1000)
                self.assertTrue(result["报告正文"].strip())
                self.assertEqual(len(result["报告文件"]), 1)
                self.assertNotIn(error_code, serialized)

    def test_v3_accepts_future_content_code_only_from_service_owned_origin(self):
        degradable_codes = (
            "E_FUTURE_PROVIDER_QUALITY_LIMITED",
            "E_MODEL_ROLE_UNKNOWN",
            "E_MODEL_ROLE_MISMATCH",
            "E_MODEL_INDEPENDENCE_VIOLATION",
            "E_MODEL_PROFILE_UNAVAILABLE",
            "E_MODEL_OR_DERIVED_STAGE_FAILURE",
        )
        for future_code in degradable_codes:
            for origin in ("service_default", "managed_post_corpus"):
                with self.subTest(error_code=future_code, origin=origin):
                    body = result_body(
                        quality_passed=False,
                        delivery_state="reference_candidate",
                        human_review_required=True,
                        artifacts=[report_artifact("docx")],
                    )
                    body["errors"] = [future_code]
                    apply_v3_reference_candidate_contract(
                        body, [future_code], origin=origin
                    )

                    result = _bridge.project_result(body)

                    self.assertTrue(result["可交付"])
                    self.assertTrue(result["可供讨论参考"])
                    self.assertTrue(result["报告正文"].strip())
                    self.assertNotIn(
                        future_code, json.dumps(result, ensure_ascii=False)
                    )

    def test_v3_rejects_missing_unknown_or_injected_authorization_origin(self):
        future_code = "E_FUTURE_PROVIDER_QUALITY_LIMITED"
        for origin in (None, "unknown", "injected"):
            with self.subTest(origin=origin):
                body = result_body(
                    quality_passed=False,
                    delivery_state="reference_candidate",
                    human_review_required=True,
                    artifacts=[report_artifact("docx")],
                )
                apply_v3_reference_candidate_contract(
                    body, [future_code], origin=origin
                )

                result = _bridge.project_result(body)

                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])
                self.assertIn("显式可信", result["交付说明"])

    def test_v3_binds_distinct_canonical_and_human_projection_bodies(self):
        canonical_body = (
            "根据 [E-DOC-001] 与 [E-WEB-002] 的可用证据，形成来源归因判断；"
            "证据编号仅用于规范报告包内部绑定。"
        ) * 30
        public_body = (
            "根据《用户提供的行业材料》与监管机构公开页面的可用证据，形成来源归因判断；"
            "具名引用便于读者核对。"
        ) * 30
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[],
        )
        apply_v3_reference_candidate_contract(
            body,
            ["E_FUTURE_PROVIDER_QUALITY_LIMITED"],
            canonical_report_body=canonical_body,
            public_report_body=public_body,
        )

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertTrue(result["可供讨论参考"])
        self.assertEqual(result["报告正文"], public_body)
        self.assertNotEqual(
            body["delivery"]["research_report_package"]["report_body"],
            result["报告正文"],
        )
        self.assertEqual(result["报告文件"], [])

    def test_v3_quality_length_proof_accepts_report_with_public_source_urls(self):
        canonical_body = (
            "研究正文说明政策与行业事实，来源页面 https://example.gov.cn/policy 。"
            "该链接只用于核对，不计入正文质量字符数。"
        ) * 40
        public_body = (
            "研究正文说明政策与行业事实，来源页面 https://example.gov.cn/policy 。"
            "读者可结合《监管公开页面》复核。"
        ) * 40
        self.assertNotEqual(
            len(canonical_body),
            len(_bridge._project_report_quality_body(canonical_body)),
        )
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[],
        )
        apply_v3_reference_candidate_contract(
            body,
            ["E_FUTURE_PROVIDER_QUALITY_LIMITED"],
            canonical_report_body=canonical_body,
            public_report_body=public_body,
        )

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertEqual(result["报告正文"], public_body)

    def test_verified_result_prefers_public_final_report_over_canonical_snapshot(self):
        canonical_body = "规范包内部正文含 [E-DOC-001]，不得直接展示给用户。"
        public_body = "公开报告正文使用《用户上传材料》具名引用。"
        body = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx")],
        )
        body["delivery"]["final_report"] = public_body
        body["snapshot"]["report_body"] = canonical_body
        body["delivery"]["research_report_package"]["report_body"] = canonical_body

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertEqual(result["报告正文"], public_body)
        self.assertNotIn("E-DOC", result["报告正文"])

    def test_v2_candidate_prefers_public_final_report_over_canonical_snapshot(self):
        canonical_body = "规范包内部正文含 [E-DOC-002]，仅用于证据绑定。"
        public_body = "参考稿正文使用《监管公开页面》具名引用。"
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        body["errors"] = ["E_SOURCE_ASSURANCE_LIMITED"]
        body["reference_candidate_schema"] = "reference-candidate.v2"
        body["delivery"]["reference_candidate_schema"] = "reference-candidate.v2"
        body["delivery"]["final_report"] = public_body
        body["snapshot"]["report_body"] = canonical_body
        body["delivery"]["research_report_package"]["report_body"] = canonical_body

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertTrue(result["可供讨论参考"])
        self.assertEqual(result["报告正文"], public_body)
        self.assertNotIn("E-DOC", result["报告正文"])

    def test_v3_service_digest_still_rejects_explicit_hard_gate_codes(self):
        hard_codes = (
            "E_ELIGIBLE_EVIDENCE_EMPTY",
            "E_CORPUS_NO_ELIGIBLE_EVIDENCE",
            "E_CORPUS_NO_FULLTEXT",
            "E_NO_READABLE_CORE",
            "E_PROHIBITED_EVIDENCE",
            "E_PERMISSION_UNKNOWN",
            "E_PERMISSION_REQUIRED",
            "E_PERMISSION_MIXED",
            "E_UNAUTHORIZED_OUTPUT",
            "E_NO_OUTPUT",
            "E_OUTPUT_NOT_ALLOWED",
            "E_REFERENCE_DANGLING",
            "E_REFERENCE_CROSS_RUN",
            "E_CORPUS_NOT_SEALED",
            "E_CORPUS_HASH_MISMATCH",
            "E_CORPUS_STATUS_MISSING",
            "E_CORPUS_STATUS_UNKNOWN",
            "E_CORPUS_IDENTITY_MISMATCH",
            "E_RUN_CONTEXT_CONFLICT",
            "E_CORPUS_MODIFIED_AFTER_SEAL",
            "E_CORPUS_CRITICAL_GAP_COUNT_MISMATCH",
            "E_DUPLICATE_REPORT_WRITE",
            "E_DUPLICATE_CORPUS_BUILD",
            "E_DUPLICATE_FINAL_ASSEMBLY",
            "E_DEDUP_CONFLICT",
            "E_LIVE_ADAPTER_CONTRACT",
            "E_MATERIAL_USAGE_CONTRACT_INVALID",
            "E_EVIDENCE_INDEX_DANGLING",
            "E_SOURCE_POLICY_VIOLATION",
            "E_REPORT_LENGTH_POLICY_INVALID",
            "E_REPORT_BODY_PACKAGE_MISMATCH",
            "E_GATE_CALCULATOR_MISSING",
            "E_REPORT_IDENTITY_BINDING_INVALID",
            "E_REPORT_PACKAGE_INTEGRITY_INVALID",
        )
        for error_code in hard_codes:
            with self.subTest(error_code=error_code):
                body = result_body(
                    quality_passed=False,
                    delivery_state="reference_candidate",
                    human_review_required=True,
                    artifacts=[report_artifact("docx")],
                )
                apply_v3_reference_candidate_contract(body, [error_code])

                result = _bridge.project_result(body)

                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])

    def test_v3_ignores_non_gate_warning_for_excluded_material(self):
        future_code = "E_FUTURE_PROVIDER_QUALITY_LIMITED"
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        body["errors"] = [future_code, "E_NO_FULLTEXT_EXCLUDED_MATERIAL_WARNING"]
        apply_v3_reference_candidate_contract(body, [future_code])

        result = _bridge.project_result(body)

        self.assertTrue(result["可交付"])
        self.assertTrue(result["可供讨论参考"])
        self.assertTrue(result["报告正文"].strip())

        legacy = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        legacy["errors"] = ["E_NO_FULLTEXT_EXCLUDED_MATERIAL_WARNING"]
        legacy_result = _bridge.project_result(legacy)
        self.assertTrue(legacy_result["可交付"])
        self.assertTrue(legacy_result["可供讨论参考"])

    def test_v3_shape_and_unknown_errors_cannot_fall_back_to_legacy(self):
        future_code = "E_FUTURE_PROVIDER_QUALITY_LIMITED"
        baseline = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        apply_v3_reference_candidate_contract(baseline, [future_code])
        cases = {}

        stripped_outer_schema = deepcopy(baseline)
        for container in (
            stripped_outer_schema,
            stripped_outer_schema["delivery"],
        ):
            container.pop("reference_candidate_schema", None)
            container.pop("reference_candidate_reason", None)
        cases["outer schema and reason stripped"] = stripped_outer_schema

        stripped_gates = deepcopy(baseline)
        stripped_gates["delivery"].pop("gate_receipt", None)
        stripped_gates["delivery"].pop("orchestrator_gate_receipt", None)
        cases["both gates stripped"] = stripped_gates

        inconsistent_schema = deepcopy(baseline)
        inconsistent_schema["delivery"][
            "reference_candidate_schema"
        ] = "reference-candidate.v2"
        cases["outer schema mismatch"] = inconsistent_schema

        inconsistent_reason = deepcopy(baseline)
        inconsistent_reason["delivery"][
            "reference_candidate_reason"
        ] = "source_assurance_limited"
        cases["outer reason mismatch"] = inconsistent_reason

        stripped_to_unknown_legacy = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        stripped_to_unknown_legacy["errors"] = [future_code]
        cases["unknown content code with all v3 proof stripped"] = (
            stripped_to_unknown_legacy
        )

        stripped_to_degradation_shape = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        stripped_to_degradation_shape["delivery"][
            "content_degradation_applied"
        ] = True
        cases["degradation trace with schema and gates stripped"] = (
            stripped_to_degradation_shape
        )

        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])

    def test_v3_recomputes_semantic_package_digest_and_rejects_tampering(self):
        future_code = "E_FUTURE_PROVIDER_QUALITY_LIMITED"
        baseline = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        apply_v3_reference_candidate_contract(baseline, [future_code])
        package = baseline["delivery"]["research_report_package"]
        signed_digest = baseline["delivery"]["gate_receipt"]["gate_metrics"][
            "reference_candidate_package_sha256"
        ]
        self.assertEqual(
            _bridge._report_package_semantic_sha256(package), signed_digest
        )

        cases = {}
        forged_ledger = deepcopy(baseline)
        forged_ledger["delivery"]["research_report_package"][
            "material_usage_ledger"
        ] = [{"material_ref": "forged", "adoption": "adopted"}]
        cases["forged material ledger"] = forged_ledger

        forged_evidence = deepcopy(baseline)
        forged_evidence["delivery"]["research_report_package"][
            "evidence_ledger"
        ] = [{"evidence_id": "E-DOC-FORGED", "excerpt": "伪造证据"}]
        cases["forged evidence ledger"] = forged_evidence

        forged_cover = deepcopy(baseline)
        forged_cover["delivery"]["research_report_package"]["cover_info"] = {
            "title": "被替换的报告"
        }
        cases["package cover changed"] = forged_cover

        forged_style = deepcopy(baseline)
        forged_style["delivery"]["research_report_package"][
            "document_style_spec"
        ] = {
            "word_pdf_body_font": "仿宋_GB2312",
            "word_pdf_body_size_pt": 15,
            "word_pdf_line_spacing_rule": "exact",
            "word_pdf_line_spacing_pt": 30,
            "title_styles_enabled": True,
            "auto_toc": True,
            "clickable_links": True,
            "ppt_font": "微软雅黑",
            "ppt_body_min_size_pt": 14,
            "ppt_slide_count_min": 15,
            "ppt_slide_count_max": 25,
        }
        cases["package style changed"] = forged_style

        forged_digest = deepcopy(baseline)
        for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
            forged_digest["delivery"][gate_name]["gate_metrics"][
                "reference_candidate_package_sha256"
            ] = "a" * 64
        cases["unbound 64 hex digest"] = forged_digest

        extra_package_field = deepcopy(baseline)
        extra_package_field["delivery"]["research_report_package"][
            "reference_candidate_schema"
        ] = "reference-candidate.v3"
        cases["extra package field"] = extra_package_field

        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])

    def test_v3_safe_reference_metrics_exactly_bind_package_evidence_ids(self):
        future_code = "E_FUTURE_PROVIDER_QUALITY_LIMITED"
        baseline = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[],
        )
        apply_v3_reference_candidate_contract(baseline, [future_code])
        self.assertTrue(_bridge.project_result(baseline)["可交付"])

        def mutate_both_metrics(payload, callback):
            for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
                callback(payload["delivery"][gate_name]["gate_metrics"])

        def resign_package(payload):
            digest = _bridge._report_package_semantic_sha256(
                payload["delivery"]["research_report_package"]
            )
            for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
                payload["delivery"][gate_name]["gate_metrics"][
                    "reference_candidate_package_sha256"
                ] = digest

        cases = {}
        for key in (
            "content_degradation_safe_evidence_ids",
            "content_degradation_safe_evidence_count",
            "content_degradation_unsafe_reference_excluded_count",
        ):
            missing = deepcopy(baseline)
            mutate_both_metrics(missing, lambda metrics, key=key: metrics.pop(key))
            cases[f"missing {key}"] = missing

        fake_ids = deepcopy(baseline)
        mutate_both_metrics(fake_ids, lambda metrics: metrics.update({
            "content_degradation_safe_evidence_ids": ["ev-v3-forged"],
            "content_degradation_safe_evidence_count": 1,
        }))
        cases["safe ids do not match package"] = fake_ids

        wrong_count = deepcopy(baseline)
        mutate_both_metrics(wrong_count, lambda metrics: metrics.update({
            "content_degradation_safe_evidence_count": 2,
        }))
        cases["safe count mismatch"] = wrong_count

        unsorted_or_duplicate = deepcopy(baseline)
        mutate_both_metrics(unsorted_or_duplicate, lambda metrics: metrics.update({
            "content_degradation_safe_evidence_ids": [
                "ev-v3-safe-1", "ev-v3-safe-1",
            ],
            "content_degradation_safe_evidence_count": 2,
        }))
        cases["safe ids are not unique"] = unsorted_or_duplicate

        negative_excluded = deepcopy(baseline)
        mutate_both_metrics(negative_excluded, lambda metrics: metrics.update({
            "content_degradation_unsafe_reference_excluded_count": -1,
        }))
        cases["negative excluded count"] = negative_excluded

        boolean_excluded = deepcopy(baseline)
        mutate_both_metrics(boolean_excluded, lambda metrics: metrics.update({
            "content_degradation_unsafe_reference_excluded_count": True,
        }))
        cases["boolean excluded count"] = boolean_excluded

        package_has_more = deepcopy(baseline)
        package_has_more["delivery"]["research_report_package"][
            "evidence_ledger"
        ].append({
            "evidence_id": "ev-v3-extra",
            "title": "未绑定的额外来源",
            "url": "https://example.org/extra-reference",
        })
        resign_package(package_has_more)
        cases["package has an extra evidence id"] = package_has_more

        package_has_less = deepcopy(baseline)
        package_has_less["delivery"]["research_report_package"][
            "evidence_ledger"
        ] = []
        resign_package(package_has_less)
        cases["package is missing the safe evidence id"] = package_has_less

        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])

    def test_v3_rejects_same_length_public_projection_tampering(self):
        future_code = "E_FUTURE_PROVIDER_QUALITY_LIMITED"
        baseline = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        apply_v3_reference_candidate_contract(baseline, [future_code])
        public_body = baseline["delivery"]["final_report"]
        cases = {}

        snapshot_tampered = deepcopy(baseline)
        snapshot_tampered["snapshot"]["report_body"] = "篡" * len(public_body)
        cases["snapshot changed without final binding"] = snapshot_tampered

        machine_tampered = deepcopy(baseline)
        machine_prefix = "report_id: rpt_forged_public_projection "
        machine_body = (machine_prefix + "甲" * len(public_body))[:len(public_body)]
        machine_tampered["delivery"]["final_report"] = machine_body
        machine_tampered["snapshot"]["report_body"] = machine_body
        cases["machine field retained at same length"] = machine_tampered

        sensitive_tampered = deepcopy(baseline)
        sensitive_prefix = "api_key: forged-sensitive-value "
        sensitive_body = (
            sensitive_prefix + "乙" * len(public_body)
        )[:len(public_body)]
        sensitive_tampered["delivery"]["final_report"] = sensitive_body
        sensitive_tampered["snapshot"]["report_body"] = sensitive_body
        cases["sensitive field retained at same length"] = sensitive_tampered

        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])

    def test_forged_or_incomplete_v3_rebuild_metrics_remain_hard_blocked(self):
        cases = {}

        hard_scan_failed = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx")],
        )
        hard_scan_failed["errors"] = ["E_MACHINE_FIELD_LEAK"]
        apply_v3_reference_candidate_contract(
            hard_scan_failed, ["E_MACHINE_FIELD_LEAK"]
        )
        for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
            hard_scan_failed["delivery"][gate_name]["gate_metrics"][
                "content_degradation_hard_scan_pass"
            ] = False
        cases["hard scan failed"] = hard_scan_failed

        hard_scan_has_errors = deepcopy(hard_scan_failed)
        for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
            metrics = hard_scan_has_errors["delivery"][gate_name]["gate_metrics"]
            metrics["content_degradation_hard_scan_pass"] = True
            metrics["content_degradation_hard_scan_error_codes"] = [
                "E_MACHINE_FIELD_LEAK"
            ]
        cases["hard scan retained an error"] = hard_scan_has_errors

        human_projection_failed = deepcopy(hard_scan_failed)
        for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
            metrics = human_projection_failed["delivery"][gate_name]["gate_metrics"]
            metrics["content_degradation_hard_scan_pass"] = True
            metrics["content_degradation_human_projection_pass"] = False
        cases["human projection failed"] = human_projection_failed

        dual_gate_mismatch = deepcopy(hard_scan_failed)
        gate_metrics = dual_gate_mismatch["delivery"]["gate_receipt"]["gate_metrics"]
        gate_metrics["content_degradation_hard_scan_pass"] = True
        orchestrator_metrics = dual_gate_mismatch["delivery"][
            "orchestrator_gate_receipt"
        ]["gate_metrics"]
        orchestrator_metrics["content_degradation_hard_scan_pass"] = True
        orchestrator_metrics["content_degradation_mode"] = "review_limited"
        cases["dual gate mismatch"] = dual_gate_mismatch

        public_body_mismatch = deepcopy(hard_scan_failed)
        for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
            public_body_mismatch["delivery"][gate_name]["gate_metrics"][
                "content_degradation_hard_scan_pass"
            ] = True
        public_body_mismatch["snapshot"]["report_body"] = "非绑定的公开正文" * 200
        cases["public body mismatch"] = public_body_mismatch

        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertFalse(result["可交付"])
                self.assertFalse(result["可供讨论参考"])
                self.assertEqual(result["报告正文"], "")
                self.assertEqual(result["报告文件"], [])
                self.assertIn(
                    "可公开阅读稿的机器字段清理检查",
                    result["交付说明"],
                )

    def test_non_v3_reference_integrity_remains_hard_blocked(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["errors"] = ["E_REFERENCE_INTEGRITY"]

        result = _bridge.project_result(body)

        self.assertFalse(result["可交付"])
        self.assertFalse(result["可供讨论参考"])
        self.assertEqual(result["报告文件"], [])
        self.assertTrue(result["交付说明"].strip())
        self.assertNotIn("E_REFERENCE_INTEGRITY", json.dumps(result, ensure_ascii=False))

    def test_candidate_gate_receipt_cannot_hide_cross_run_block(self):
        body = result_body(
            quality_passed=False,
            delivery_state="reference_candidate",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["delivery"]["gate_receipt"] = {
            "error_codes": ["E_CROSS_RUN_BINDING_MISMATCH"],
        }

        result = _bridge.project_result(body)

        self.assertFalse(result["可交付"])
        self.assertFalse(result["可供讨论参考"])
        self.assertEqual(result["报告文件"], [])
        self.assertIn("任务与报告身份绑定一致性检查", result["交付说明"])
        self.assertTrue(result["用户无需操作"])
        self.assertNotIn(
            "E_CROSS_RUN_BINDING_MISMATCH",
            json.dumps(result, ensure_ascii=False),
        )

    def test_explicit_output_prohibition_still_fails_closed_with_natural_notice(self):
        body = result_body(
            quality_passed=False,
            delivery_state="preliminary_dossier",
            human_review_required=True,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        body["error_code"] = "E_EVIDENCE_OUTPUT_PROHIBITED"
        body["output_permitted"] = False

        result = _bridge.project_result(body)

        self.assertFalse(result["可交付"])
        self.assertEqual(result["报告文件"], [])
        self.assertTrue(result["交付说明"].strip())
        self.assertIn("材料输出许可检查", result["交付说明"])
        self.assertTrue(result["用户无需操作"])
        self.assertNotIn("E_EVIDENCE_OUTPUT_PROHIBITED", json.dumps(result, ensure_ascii=False))

    def test_verified_report_delivery_behavior_is_preserved(self):
        body = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )

        result = _bridge.project_result(body)

        self.assertTrue(result["质量检查通过"])
        self.assertTrue(result["可交付"])
        self.assertFalse(result["可供讨论参考"])
        self.assertEqual(result["交付状态"], "正式研究报告")
        self.assertEqual(
            result["交付说明"],
            "正式研究报告已完成，DOCX 和 PDF 文件均已准备好",
        )
        self.assertFalse(result["需人工复核"])
        self.assertEqual(
            result["_artifact_ids"], [ARTIFACT_ID, RENDER_ARTIFACT_ID]
        )
        self.assertEqual(
            [item["文件名"] for item in result["报告文件"]],
            ["消费金融政策研究报告.docx", "消费金融政策研究报告.pdf"],
        )
        self.assertNotIn(
            "reference_candidate", json.dumps(result, ensure_ascii=False)
        )

    def test_nonstandard_state_does_not_hide_verified_readable_output(self):
        valid = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        wrong_state = deepcopy(valid)
        wrong_state["delivery_state"] = "preliminary_dossier"

        result = _bridge.project_result(wrong_state)

        self.assertTrue(result["质量检查通过"])
        self.assertTrue(result["可交付"])
        self.assertFalse(result["可供讨论参考"])
        self.assertEqual(len(result["报告文件"]), 2)
        self.assertEqual(result["报告正文"], "正文内容")
        self.assertEqual(result["交付状态"], "正式研究报告")

    def test_report_delivery_keeps_body_and_each_unambiguous_ready_format(self):
        valid = result_body(
            quality_passed=True,
            delivery_state="verified_report",
            human_review_required=False,
            artifacts=[report_artifact("docx"), report_artifact("pdf")],
        )
        cases = {}

        single_format = deepcopy(valid)
        single_format["artifacts"] = [report_artifact("docx")]
        cases["only docx"] = single_format

        pending_pdf = deepcopy(valid)
        pending_pdf["artifacts"][1]["status"] = "pending"
        cases["pending pdf"] = pending_pdf

        duplicate_format = deepcopy(valid)
        duplicate_format["artifacts"][1] = report_artifact("docx")
        cases["duplicate format"] = duplicate_format

        expected_artifact_counts = {
            "only docx": 1,
            "pending pdf": 1,
            "duplicate format": 0,
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                result = _bridge.project_result(body)
                self.assertTrue(result["质量检查通过"])
                self.assertTrue(result["可交付"])
                self.assertEqual(result["交付状态"], "正式研究报告")
                self.assertEqual(result["报告正文字符数"], 4)
                self.assertEqual(result["报告正文"], "正文内容")
                self.assertEqual(
                    len(result["报告文件"]), expected_artifact_counts[label]
                )
                self.assertEqual(
                    len(result["_artifact_ids"]), expected_artifact_counts[label]
                )
                self.assertTrue(any(
                    "部分报告文件格式未生成" in item
                    or "报告文件未生成" in item
                    for item in result["报告局限"]
                ))

    def test_question_preserves_evidence_and_read_only_flags(self):
        body = {
            "answer_status": "answered",
            "answer": "直接回答",
            "citations": [{
                "evidence_id": "hidden",
                "source_title": "政策原文",
                "source_level": "P0",
                "locator": "第十条",
                "url": "https://www.nfra.gov.cn/policy",
                "quote": "摘录",
                "verification_status": "verified",
            }],
            "uncertainty": [],
            "limitations": [],
            "answer_basis": "snapshot",
            "report_modified": False,
            "revision_created": False,
        }
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)) as request:
            result = ask_research_job.ask_research_job(job_id=JOB_ID, question="依据是什么？")
        self.assertEqual(request.call_args.kwargs["json_body"]["mode"], "question")
        self.assertEqual(result["依据"][0]["位置"], "第十条")
        self.assertFalse(result["报告已修改"])
        self.assertNotIn("evidence_id", json.dumps(result))

    def test_download_grant_uses_url_and_ignores_response_job_id(self):
        body = {
            "artifact_id": ARTIFACT_ID,
            "job_id": "internal-access-context",
            "url": "https://download.example/report.docx?signature=short-lived",
            "expires_at": "2099-01-01T20:00:00Z",
            "cache_control": "no-store",
        }
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)) as request:
            result = create_artifact_download_grant.create_artifact_download_grant(
                artifact_id=ARTIFACT_ID, job_id=JOB_ID
            )
        self.assertEqual(request.call_args.kwargs["json_body"], {"job_id": JOB_ID})
        self.assertEqual(result["下载地址"], body["url"])
        self.assertNotIn("internal-access-context", json.dumps(result))

    def test_download_grant_without_https_fails_closed(self):
        body = {"artifact_id": ARTIFACT_ID, "url": "http://unsafe", "expires_at": "later"}
        with patch.object(_bridge, "do_request", return_value=FakeResponse(200, body)):
            result = create_artifact_download_grant.create_artifact_download_grant(
                artifact_id=ARTIFACT_ID, job_id=JOB_ID
            )
        self.assertEqual(result["错误码"], "BACKEND_CONTRACT_ERROR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
