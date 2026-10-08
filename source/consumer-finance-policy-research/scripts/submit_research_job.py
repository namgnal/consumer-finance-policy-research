#!/usr/bin/env python3
"""Submit one asynchronous research job with a stable idempotency key."""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def submit_research_job(*, policy_input, operation_id, output_spec_raw=None, draft_id=None,
                        reference_items_raw=None, rerun_after_job_id=None):
    policy_input = str(policy_input or "").strip()
    if not policy_input:
        return _bridge.error_resp("INVALID_POLICY_INPUT", "研究主题或要求不能为空")
    if len(policy_input) > _bridge.MAX_POLICY_INPUT_CHARS:
        return _bridge.error_resp("INVALID_POLICY_INPUT", "研究要求不能超过 20000 个字符")
    operation_id = str(operation_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", operation_id):
        return _bridge.error_resp("INVALID_OPERATION_ID", "本次启动操作标识须为 8—100 位字母、数字、下划线或连字符")
    if rerun_after_job_id:
        try:
            rerun_after_job_id = _bridge.validate_handle(rerun_after_job_id, "job")
        except ValueError as exc:
            return _bridge.error_resp("INVALID_HANDLE", str(exc))
    if draft_id:
        try:
            draft_id = _bridge.validate_handle(draft_id, "draft")
        except ValueError as exc:
            return _bridge.error_resp("INVALID_HANDLE", str(exc))
    try:
        output_spec = _bridge.parse_output_spec(output_spec_raw)
        reference_items = _bridge.parse_reference_items(reference_items_raw)
    except ValueError as exc:
        return _bridge.error_resp("INVALID_JSON", str(exc))

    body = {"policy_input": policy_input, "output_spec": output_spec}
    if draft_id:
        body["draft_id"] = draft_id
    if reference_items:
        # The material collection is unordered business input.  Canonicalize
        # both the wire body and the fingerprint so an Agent changing only
        # accumulation order cannot create a different request representation.
        reference_items = sorted(
            reference_items,
            key=lambda value: (value["kind"], value["content"]),
        )
        body["reference_items"] = reference_items

    if rerun_after_job_id:
        previous_response = _bridge.do_request(
            "GET",
            f"/integrations/research/jobs/{rerun_after_job_id}",
        )
        if _bridge.is_error(previous_response):
            return previous_response
        previous_body = _bridge.parse_json(previous_response)
        error = _bridge.require_contract(
            previous_body,
            ("job_id", "status"),
            operation="核验显式重跑来源任务",
        )
        if error:
            return error
        handle_error = _bridge.require_handle_match(
            previous_body,
            "job_id",
            rerun_after_job_id,
            "job",
            operation="核验显式重跑来源任务",
        )
        if handle_error:
            return handle_error
        if str(previous_body.get("status") or "") not in {
            "completed",
            "completed_with_gaps",
            "failed",
        }:
            return _bridge.error_resp(
                "RERUN_SOURCE_NOT_TERMINAL",
                "上一研究任务尚未结束，不能创建显式重跑任务；请继续查询原任务",
                可重试=True,
                重试等待秒=10,
            )

    # Opaque authorization ids are scoped transport handles, not business
    # input. Re-preparing the same normalized material may issue a fresh id;
    # that must not create a second paid job. The backend still receives and
    # binds the real handles in ``body`` below.
    fingerprint_body = dict(body)
    if reference_items:
        fingerprint_body["reference_items"] = [
            {"kind": item["kind"], "content": item["content"]}
            for item in reference_items
        ]

    # ``operation_id`` is retained as a validated compatibility argument, but
    # deliberately excluded from the fingerprint.  An Agent may regenerate
    # that label after a timeout or repeated confirmation; doing so must not
    # create a second paid job for the same canonical business request.
    fingerprint = {"request": fingerprint_body}
    if rerun_after_job_id:
        # A verified terminal predecessor is the only escape hatch for an
        # intentional re-run.  It is not sent in the POST body; it only
        # versions the idempotency fingerprint.  Retrying with the same
        # predecessor therefore remains idempotent.
        fingerprint["rerun_after_job_id"] = rerun_after_job_id
    idempotency_key = _bridge.make_idempotency_key(fingerprint)
    body["idempotency_key"] = idempotency_key
    response = _bridge.do_request("POST", "/integrations/research/jobs", json_body=body)
    if _bridge.is_error(response):
        return response
    response_body = _bridge.parse_json(response)
    error = _bridge.require_contract(response_body, ("job_id", "status"), operation="提交研究任务")
    if error:
        return error
    handle_error = _bridge.require_handle_match(
        response_body, "job_id", None, "job", operation="提交研究任务"
    )
    if handle_error:
        return handle_error
    retry_after_raw = str(getattr(response, "headers", {}).get("Retry-After") or "")
    retry_after = int(retry_after_raw) if retry_after_raw.isdigit() else None
    result = _bridge.project_job_status(response_body, retry_after=retry_after)
    result["提交结果"] = "研究任务已受理"
    result["立即回复用户"] = (
        "研究任务已受理。我已保留本次任务进度；你之后发送“查看进度”“上个任务”"
        "或任何新消息时，我会优先继续查询并在完成后交付。当前渠道不支持在你不再"
        "发送消息时自动跨轮推送。"
    )
    result["本轮应结束"] = True
    result["自动跨轮推送"] = False
    result["_idempotency_key"] = idempotency_key
    return result


def main():
    parser = argparse.ArgumentParser(description="提交异步研究任务")
    parser.add_argument("--policy-input", required=True, help="完整研究主题与要求")
    parser.add_argument("--operation-id", required=True, help="兼容性操作标识；不会改变同一业务请求的幂等键")
    parser.add_argument(
        "--rerun-after-job-id",
        default=None,
        help="仅在用户明确要求重新研究同一请求时填写已到终态的上一任务句柄",
    )
    parser.add_argument(
        "--output-spec",
        default=None,
        help="JSON 对象；省略时生成不固定字数的标准研究报告 DOCX/PDF",
    )
    parser.add_argument("--draft-id", default=None, help="已封存非空草稿操作句柄")
    parser.add_argument(
        "--reference-items",
        default=None,
        help="准备/确认工具返回的内部 _reference_item JSON 数组；不得自行添加权限字段",
    )
    args = parser.parse_args()
    _bridge.output(submit_research_job(
        policy_input=args.policy_input,
        operation_id=args.operation_id,
        output_spec_raw=args.output_spec,
        draft_id=args.draft_id,
        reference_items_raw=args.reference_items,
        rerun_after_job_id=args.rerun_after_job_id,
    ))


if __name__ == "__main__":
    main()
