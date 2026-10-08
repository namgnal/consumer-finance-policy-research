#!/usr/bin/env python3
"""Seal a non-empty collecting draft; safely resume an already sealed draft."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def seal_material_draft(*, draft_id):
    try:
        draft_id = _bridge.validate_handle(draft_id, "draft")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    current_response = _bridge.do_request("GET", f"/integrations/research/drafts/{draft_id}")
    if _bridge.is_error(current_response):
        return current_response
    current = _bridge.parse_json(current_response)
    error = _bridge.require_contract(
        current, ("draft_id", "status", "material_count", "materials"), operation="封存前检查"
    )
    if error:
        return error
    handle_error = _bridge.require_handle_match(
        current, "draft_id", draft_id, "draft", operation="封存前检查"
    )
    if handle_error:
        return handle_error
    if int(current.get("material_count") or 0) <= 0:
        return _bridge.error_resp("EMPTY_DRAFT", "当前草稿没有文件，不会封存")
    if current.get("status") == "sealed":
        result = _bridge.project_draft(current)
        result["封存结果"] = "草稿此前已经封存，可继续提交研究"
        return result
    if current.get("status") != "collecting":
        return _bridge.error_resp("DRAFT_NOT_COLLECTING", "当前草稿状态不允许封存")

    response = _bridge.do_request("POST", f"/integrations/research/drafts/{draft_id}/seal")
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(
        body, ("draft_id", "status", "material_count", "materials"), operation="封存材料草稿"
    )
    if error:
        return error
    handle_error = _bridge.require_handle_match(
        body, "draft_id", draft_id, "draft", operation="封存材料草稿"
    )
    if handle_error:
        return handle_error
    if body.get("status") != "sealed":
        return _bridge.error_resp("BACKEND_CONTRACT_ERROR", "后端未确认草稿已封存")
    result = _bridge.project_draft(body)
    result["封存结果"] = "已封存，可开始研究"
    return result


def main():
    parser = argparse.ArgumentParser(description="封存非空材料草稿")
    parser.add_argument("--draft-id", required=True)
    args = parser.parse_args()
    _bridge.output(seal_material_draft(draft_id=args.draft_id))


if __name__ == "__main__":
    main()
