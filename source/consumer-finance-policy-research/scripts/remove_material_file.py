#!/usr/bin/env python3
"""Remove one material from a collecting draft."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def remove_material_file(*, draft_id, material_ref):
    try:
        draft_id = _bridge.validate_handle(draft_id, "draft")
        material_ref = _bridge.validate_handle(material_ref, "material")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    response = _bridge.do_request(
        "DELETE", f"/integrations/research/drafts/{draft_id}/materials/{material_ref}"
    )
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    if body.get("deleted") is not True:
        return _bridge.error_resp("BACKEND_CONTRACT_ERROR", "后端未确认删除成功")
    return _bridge.success_resp(操作结果="已删除")


def main():
    parser = argparse.ArgumentParser(description="删除草稿中的材料文件")
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--material-ref", required=True)
    args = parser.parse_args()
    _bridge.output(remove_material_file(draft_id=args.draft_id, material_ref=args.material_ref))


if __name__ == "__main__":
    main()
