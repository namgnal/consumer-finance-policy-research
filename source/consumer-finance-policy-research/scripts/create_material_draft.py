#!/usr/bin/env python3
"""Create an empty collecting draft."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def create_material_draft():
    response = _bridge.do_request("POST", "/integrations/research/drafts")
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(body, ("draft_id", "status"), operation="创建材料草稿")
    handle_error = _bridge.require_handle_match(
        body, "draft_id", None, "draft", operation="创建材料草稿"
    )
    return error or handle_error or _bridge.project_draft(body)


def main():
    argparse.ArgumentParser(description="创建材料草稿").parse_args()
    _bridge.output(create_material_draft())


if __name__ == "__main__":
    main()
