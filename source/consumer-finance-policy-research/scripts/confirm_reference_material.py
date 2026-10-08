#!/usr/bin/env python3
"""Confirm one pending reference using a fixed, user-facing choice."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def confirm_reference_material(*, authorization_context_raw, choice):
    try:
        context = _bridge.parse_reference_authorization_context(
            authorization_context_raw,
        )
        decision = _bridge.parse_reference_choice(choice)
    except ValueError as exc:
        return _bridge.error_resp("INVALID_REFERENCE_CONFIRMATION", str(exc))

    response = _bridge.do_request(
        "POST",
        (
            "/integrations/research/reference-authorizations/"
            f"{context['authorization_id']}/confirm"
        ),
        json_body={"choice": decision},
    )
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    return _bridge.project_confirmed_reference(
        body,
        context=context,
        expected_decision=decision,
    )


def main():
    parser = argparse.ArgumentParser(description="确认一个网页链接或粘贴文字材料的使用依据")
    parser.add_argument(
        "--authorization-context",
        required=True,
        help="准备工具返回的内部 _authorization_context；不得向用户展示或改写",
    )
    parser.add_argument(
        "--choice",
        required=True,
        help="用户选择：公开网页、本人合法取得、企业已授权、不使用该材料",
    )
    args = parser.parse_args()
    _bridge.output(confirm_reference_material(
        authorization_context_raw=args.authorization_context,
        choice=args.choice,
    ))


if __name__ == "__main__":
    main()
