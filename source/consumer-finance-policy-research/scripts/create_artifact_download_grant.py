#!/usr/bin/env python3
"""Issue one owner-scoped, short-lived artifact download URL."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def create_artifact_download_grant(*, artifact_id, job_id):
    try:
        artifact_id = _bridge.validate_handle(artifact_id, "artifact")
        job_id = _bridge.validate_handle(job_id, "job")
    except ValueError as exc:
        return _bridge.error_resp("INVALID_HANDLE", str(exc))
    response = _bridge.do_request(
        "POST",
        f"/integrations/research/artifacts/{artifact_id}/download-grant",
        json_body={"job_id": job_id},
    )
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(body, ("artifact_id", "url", "expires_at"), operation="创建下载授权")
    if error:
        return error
    if body.get("artifact_id") != artifact_id or not _bridge.is_https_url(body.get("url")):
        return _bridge.error_resp("BACKEND_CONTRACT_ERROR", "下载授权响应无效，未提供下载入口")
    # Do not trust or persist response.job_id: the backend may return an
    # internal access context rather than the supplied rjob handle.
    return _bridge.success_resp(
        下载就绪=True,
        下载地址=body["url"],
        有效期至=body["expires_at"],
        缓存策略=str(body.get("cache_control") or "no-store"),
        _artifact_id=artifact_id,
    )


def main():
    parser = argparse.ArgumentParser(description="创建短时报告下载授权")
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    _bridge.output(create_artifact_download_grant(
        artifact_id=args.artifact_id,
        job_id=args.job_id,
    ))


if __name__ == "__main__":
    main()
