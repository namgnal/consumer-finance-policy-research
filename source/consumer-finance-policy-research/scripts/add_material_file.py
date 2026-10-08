#!/usr/bin/env python3
"""Upload exactly one file plus one JSON permission part."""
import argparse
import hashlib
import json
import mimetypes
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bridge


def _permission_profile_arg(value):
    raw = str(value or "").strip()
    normalized = _bridge.PERMISSION_PROFILE_ALIASES.get(raw, raw)
    if normalized not in _bridge.VALID_PERMISSIONS:
        raise argparse.ArgumentTypeError("不支持的材料授权类型")
    return normalized


def _permission_matches_requested(receipt, requested):
    return (
        isinstance(receipt, dict)
        and all(receipt.get(key) == value for key, value in requested.items())
    )


def _is_valid_canonical_permission(receipt):
    if not isinstance(receipt, dict):
        return False
    license_basis = receipt.get("license_basis")
    expected_scope = {
        "public_web": "public",
        "user_owned": "licensed_external",
        "licensed_enterprise": "licensed_external",
    }.get(license_basis)
    return all((
        expected_scope is not None,
        receipt.get("scope") == expected_scope,
        receipt.get("confidentiality") == expected_scope,
        receipt.get("content_use") == "full_analysis",
        receipt.get("authorized_by") in _bridge.VALID_AUTHORIZERS,
        receipt.get("retention_mode") in _bridge.VALID_RETENTION_MODES,
    ))


def _read_attachment_bytes(file_path, *, expected_proof=None):
    """Open one resolved file and bind the upload to its verified bytes.

    Coze Drive may refresh inode/mtime metadata on first read.  Those fields are
    intentionally not treated as content identity.  ``O_NOFOLLOW`` plus regular
    file checks prevent symlink traversal; size and SHA-256 bind this read to the
    resolver proof before any request is sent.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(file_path, flags)
        opened = os.fstat(descriptor)
        current = os.stat(file_path, follow_symlinks=False)
        expected_size = None
        if expected_proof is not None:
            expected_size = expected_proof.get("size_bytes")
            if expected_size is None:
                signature = tuple(expected_proof.get("signature") or ())
                expected_size = signature[2] if len(signature) >= 3 else None
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or int(opened.st_size) > _bridge.MAX_FILE_SIZE
            or (
                expected_size is not None
                and int(expected_size) != int(opened.st_size)
            )
        ):
            raise ValueError("附件在读取前发生变化，请重新上传")

        chunks = []
        total = 0
        while total <= _bridge.MAX_FILE_SIZE:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, _bridge.MAX_FILE_SIZE + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        file_bytes = b"".join(chunks)

        opened_after = os.fstat(descriptor)
        current_after = os.stat(file_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_after.st_mode)
            or not stat.S_ISREG(current_after.st_mode)
            or int(opened.st_size) != int(opened_after.st_size)
            or len(file_bytes) != int(opened_after.st_size)
        ):
            raise ValueError("附件在读取时发生变化，请重新上传")
        expected_digest = (
            str(expected_proof.get("sha256") or "") if expected_proof else ""
        )
        if expected_digest and hashlib.sha256(file_bytes).hexdigest() != expected_digest:
            raise ValueError("附件在读取前发生变化，请重新上传")
        return file_bytes
    except (OSError, ValueError) as exc:
        raise ValueError("附件当前无法安全读取，请重新上传") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def add_material_file(*, draft_id, filename, permission_profile, file_path=None,
                      authorized_by, retention_mode="task_only"):
    attachment_proof = None
    try:
        draft_id = _bridge.validate_handle(draft_id, "draft")
        requested_name = _bridge._validate_attachment_filename(filename)
        if file_path:
            file_path = _bridge.validate_attachment_path(file_path)
            attachment_proof = _bridge._attachment_content_snapshot(file_path)
        else:
            file_path, attachment_proof = _bridge.resolve_attachment_file(
                requested_name,
                include_proof=True,
                wait_seconds=_bridge.ATTACHMENT_DISCOVERY_TIMEOUT_SECONDS,
            )
    except ValueError as exc:
        return _bridge.error_resp("INVALID_ATTACHMENT", str(exc))
    actual_name = os.path.basename(file_path)
    if not _bridge._attachment_name_matches(actual_name, requested_name):
        return _bridge.error_resp("INVALID_ATTACHMENT", "文件名与实际附件不一致")
    filename = requested_name

    proof_size = (
        attachment_proof.get("size_bytes")
        if isinstance(attachment_proof, dict)
        else None
    )
    if proof_size is not None and int(proof_size) > _bridge.MAX_FILE_SIZE:
        return _bridge.error_resp("FILE_TOO_LARGE", "单个文件不能超过 16 MiB")

    try:
        permission = _bridge.build_permission(
            permission_profile,
            authorized_by=authorized_by,
            retention_mode=retention_mode,
        )
    except ValueError as exc:
        return _bridge.error_resp("INVALID_PERMISSION", str(exc))

    try:
        file_bytes = _read_attachment_bytes(file_path, expected_proof=attachment_proof)
    except ValueError as exc:
        return _bridge.error_resp("INVALID_ATTACHMENT", str(exc))
    file_size = len(file_bytes)
    if file_size == 0:
        return _bridge.error_resp("FILE_EMPTY", "文件内容为空")
    if file_size > _bridge.MAX_FILE_SIZE:
        return _bridge.error_resp("FILE_TOO_LARGE", "单个文件不能超过 16 MiB")
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # The backend rejects every multipart field except these exact two names.
    files = {"file": (filename, file_bytes, mime_type)}
    data = {"permission": json.dumps(permission, ensure_ascii=False, separators=(",", ":"))}
    response = _bridge.do_request(
        "POST",
        f"/integrations/research/drafts/{draft_id}/materials",
        data=data,
        files=files,
    )
    if _bridge.is_error(response):
        return response
    body = _bridge.parse_json(response)
    error = _bridge.require_contract(body, ("action", "material"), operation="上传材料")
    if error:
        return error
    material = body.get("material")
    if not isinstance(material, dict) or not material.get("material_ref"):
        return _bridge.error_resp("BACKEND_CONTRACT_ERROR", "上传响应缺少材料引用，未确认成功")
    handle_error = _bridge.require_handle_match(
        material, "material_ref", None, "material", operation="上传材料"
    )
    if handle_error:
        return handle_error
    action = body.get("action")
    receipt_size = material.get("size_bytes")
    receipt_permission = material.get("permission")
    valid_size = (
        not isinstance(receipt_size, bool)
        and isinstance(receipt_size, int)
        and receipt_size == file_size
    )
    if action == "created":
        receipt_matches = (
            material.get("filename") == filename
            and material.get("mime_type") == mime_type
            and valid_size
            and _permission_matches_requested(receipt_permission, permission)
        )
    elif action == "existing":
        # The backend deduplicates by bytes.  An existing material legitimately
        # retains the first upload's canonical filename/MIME, so bind this
        # response to the current payload by its exact size and SHA-256 instead.
        receipt_matches = (
            valid_size
            and isinstance(material.get("filename"), str)
            and bool(material.get("filename").strip())
            and isinstance(material.get("mime_type"), str)
            and bool(material.get("mime_type").strip())
            and material.get("sha256") == hashlib.sha256(file_bytes).hexdigest()
            and _is_valid_canonical_permission(receipt_permission)
        )
    else:
        receipt_matches = False
    if not receipt_matches:
        return _bridge.error_resp(
            "BACKEND_CONTRACT_ERROR",
            "上传响应中的文件信息与实际附件不一致，未确认成功",
        )
    projected = _bridge.project_material(material)
    if action == "existing" and not _permission_matches_requested(
            receipt_permission, permission):
        receipt_label = "同一内容已存在，沿用首次授权和保留方式"
    elif action == "existing":
        receipt_label = "已存在，未重复保存"
    else:
        receipt_label = "已稳定接收"
    return _bridge.success_resp(
        接收结果=receipt_label,
        **projected,
    )


def build_parser():
    parser = argparse.ArgumentParser(description="上传材料文件到草稿")
    parser.add_argument("--draft-id", required=True, help="当前草稿操作句柄")
    parser.add_argument("--filename", required=True, help="用户上传时看到的原始文件名")
    parser.add_argument("--file-path", help=argparse.SUPPRESS)
    parser.add_argument("--permission-profile", required=True, type=_permission_profile_arg,
                        choices=sorted(_bridge.VALID_PERMISSIONS), help="已确认的材料授权类型")
    parser.add_argument("--authorized-by", required=True,
                        choices=sorted(_bridge.VALID_AUTHORIZERS), help="授权确认来源")
    parser.add_argument("--retention-mode", default="task_only",
                        choices=sorted(_bridge.VALID_RETENTION_MODES))
    return parser


def main():
    args = build_parser().parse_args()
    _bridge.output(add_material_file(
        draft_id=args.draft_id,
        file_path=args.file_path,
        filename=args.filename,
        permission_profile=args.permission_profile,
        authorized_by=args.authorized_by,
        retention_mode=args.retention_mode,
    ))


if __name__ == "__main__":
    main()
