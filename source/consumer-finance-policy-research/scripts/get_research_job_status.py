#!/usr/bin/env python3
"""Read the current business status of one asynchronous job."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def get_research_job_status(*, job_id):
    try:
        job_id = _bridge.validate_handle(job_id, "job")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    response = _bridge.do_request("GET", f"/integrations/research/jobs/{job_id}")
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(body, ("job_id", "status", "progress"), operation="查询研究进度")
    if error:
        return error
    handle_error = _bridge.require_handle_match(
        body, "job_id", job_id, "job", operation="查询研究进度"
    )
    if handle_error:
        return handle_error
    retry_after_raw = str(getattr(response, "headers", {}).get("Retry-After") or "")
    retry_after = int(retry_after_raw) if retry_after_raw.isdigit() else None
    return _bridge.project_job_status(body, retry_after=retry_after)


def main():
    parser = argparse.ArgumentParser(description="查询研究任务状态")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    _bridge.output(get_research_job_status(job_id=args.job_id))


if __name__ == "__main__":
    main()
