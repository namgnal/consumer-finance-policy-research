#!/usr/bin/env python3
"""Return a bounded, model-safe projection of a completed research job."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def get_research_job_result(*, job_id):
    try:
        job_id = _bridge.validate_handle(job_id, "job")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    response = _bridge.do_request("GET", f"/integrations/research/jobs/{job_id}/result")
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(
        body, ("job_id", "status", "delivery", "artifacts", "snapshot"), operation="获取研究结果"
    )
    if error:
        return error
    handle_error = _bridge.require_handle_match(
        body, "job_id", job_id, "job", operation="获取研究结果"
    )
    if handle_error:
        return handle_error
    if body.get("status") not in {"completed", "completed_with_gaps"}:
        return _bridge.error_resp("BACKEND_CONTRACT_ERROR", "任务尚未处于可交付状态")
    return _bridge.project_result(body)


def main():
    parser = argparse.ArgumentParser(description="获取研究任务结果")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    _bridge.output(get_research_job_result(job_id=args.job_id))


if __name__ == "__main__":
    main()
