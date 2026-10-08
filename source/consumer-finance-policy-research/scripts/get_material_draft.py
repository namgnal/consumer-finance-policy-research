#!/usr/bin/env python3
"""Return a safe business view of one material draft."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def get_material_draft(*, draft_id):
    try:
        draft_id = _bridge.validate_handle(draft_id, "draft")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    response = _bridge.do_request("GET", f"/integrations/research/drafts/{draft_id}")
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(
        body, ("draft_id", "status", "material_count", "materials"), operation="查询材料草稿"
    )
    handle_error = _bridge.require_handle_match(
        body, "draft_id", draft_id, "draft", operation="查询材料草稿"
    )
    return error or handle_error or _bridge.project_draft(body)


def main():
    parser = argparse.ArgumentParser(description="查询草稿状态")
    parser.add_argument("--draft-id", required=True)
    args = parser.parse_args()
    _bridge.output(get_material_draft(draft_id=args.draft_id))


if __name__ == "__main__":
    main()
