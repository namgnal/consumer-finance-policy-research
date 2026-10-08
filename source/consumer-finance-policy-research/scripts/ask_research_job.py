#!/usr/bin/env python3
"""Ask a read-only question against the immutable completed-job snapshot."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def ask_research_job(*, job_id, question):
    try:
        job_id = _bridge.validate_handle(job_id, "job")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    question = str(question or "").strip()
    if not question or len(question) > 6000:
        return _bridge.error_resp("INVALID_QUESTION", "问题须为 1—6000 个字符")
    response = _bridge.do_request(
        "POST",
        f"/integrations/research/jobs/{job_id}/questions",
        json_body={"question": question, "mode": "question"},
    )
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(body, ("answer_status", "report_modified", "revision_created"), operation="报告问答")
    return error or _bridge.project_question(body)


def main():
    parser = argparse.ArgumentParser(description="围绕已完成报告进行只读问答")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    _bridge.output(ask_research_job(job_id=args.job_id, question=args.question))


if __name__ == "__main__":
    main()
