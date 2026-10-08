#!/usr/bin/env python3
"""Prepare one URL or pasted-text reference for server-owned authorization."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def prepare_reference_material(*, kind, content):
    try:
        kind, content = _bridge.validate_reference_content(kind, content)
    except ValueError as exc:
        return _bridge.error_resp("INVALID_REFERENCE", str(exc))

    response = _bridge.do_request(
        "POST",
        "/integrations/research/reference-authorizations/prepare",
        json_body={"kind": kind, "content": content},
    )
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    return _bridge.project_prepared_reference(body, kind=kind, content=content)


def main():
    parser = argparse.ArgumentParser(description="准备一个网页链接或粘贴文字材料")
    parser.add_argument("--kind", required=True, choices=sorted(_bridge.REFERENCE_KINDS))
    parser.add_argument("--content", required=True, help="网页链接或用户粘贴的原文")
    args = parser.parse_args()
    _bridge.output(prepare_reference_material(kind=args.kind, content=args.content))


if __name__ == "__main__":
    main()
