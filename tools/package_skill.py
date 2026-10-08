#!/usr/bin/env python3
"""Build and verify a deterministic, least-content Coze Skill archive.

The release archive is intentionally driven by an explicit manifest rather
than by ``git`` or a recursive glob.  Local credentials and test caches may
exist beside the source files, but they can never become archive members.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping


SKILL_NAME = "consumer-finance-policy-research"
ARCHIVE_ROOT = f"{SKILL_NAME}/"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = REPOSITORY_ROOT / "source" / SKILL_NAME

# This is the release contract.  Adding a source file is not enough to ship
# it: the manifest and its tests must be reviewed at the same time.
RELEASE_FILES = (
    "LICENSE",
    "SKILL.md",
    "references/api-spec.md",
    "references/error-model.md",
    "references/permission-model.md",
    "references/readme.md",
    "scripts/_bridge.py",
    "scripts/add_material_file.py",
    "scripts/ask_research_job.py",
    "scripts/confirm_reference_material.py",
    "scripts/create_artifact_download_grant.py",
    "scripts/create_material_draft.py",
    "scripts/get_material_draft.py",
    "scripts/get_research_job_result.py",
    "scripts/get_research_job_status.py",
    "scripts/prepare_reference_material.py",
    "scripts/remove_material_file.py",
    "scripts/seal_material_draft.py",
    "scripts/submit_research_job.py",
    "scripts/test_bridge.py",
)

FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
TEXT_SUFFIXES = frozenset({".md", ".py", ".txt", ".json", ".yaml", ".yml"})
FORBIDDEN_BASENAMES = frozenset(
    {
        "_dev_credentials.py",
        ".env",
        "id_rsa",
        "id_ed25519",
    }
)
FORBIDDEN_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tmp",
        "tmp",
        "cache",
        "logs",
    }
)
FORBIDDEN_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".log",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)

_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
_KNOWN_TOKEN_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
)
_SENSITIVE_LITERAL_RE = re.compile(
    r"(?i)(?:['\"])?"
    r"(?P<name>"
    r"COZE_RESEARCH_BRIDGE_KEY(?:[-_][A-Za-z0-9-]+)?|"
    r"COZE_RESEARCH_SUBJECT(?:[-_][A-Za-z0-9-]+)?|"
    r"X-Research-Bridge-Key|X-Research-Subject|"
    r"(?:api|access|auth|bridge|research)[_-]?(?:key|token|subject)|"
    r"password|private[_-]?key"
    r")"
    r"(?:['\"])?\s*(?:=|:)\s*"
    r"(?P<quote>['\"])(?P<value>[^'\"\r\n]{1,500})(?P=quote)"
)
_PLACEHOLDER_MARKERS = (
    "placeholder",
    "replace-me",
    "replace_me",
    "changeme",
    "change-me",
    "your-",
    "your_",
    "example",
    "dummy",
    "sample",
    "not-a-real",
    "redacted",
    "masked",
    "opaque-user-token",
    "runtime-ticket",
    "subject-token",
)
_EXACT_PLACEHOLDERS = frozenset(
    {
        "bridge",
        "subject",
        "token",
        "secret",
        "password",
        "first",
        "second",
        "test",
        "demo",
        "xxx",
    }
)


class PackagingError(RuntimeError):
    """Raised when the source or archive violates the release contract."""


def _posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_forbidden_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    lowered = tuple(part.lower() for part in parts)
    basename = lowered[-1] if lowered else ""
    if any(part in FORBIDDEN_DIR_NAMES for part in lowered[:-1]):
        return True
    if basename in FORBIDDEN_BASENAMES or basename.startswith(".env."):
        return True
    return basename.endswith(FORBIDDEN_SUFFIXES)


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    if normalized in _EXACT_PLACEHOLDERS:
        return True
    if normalized.startswith(("$", "%", "${", "{{", "<")):
        return True
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


def scan_text_for_secrets(text: str, *, label: str) -> None:
    """Reject likely credential values without echoing their contents."""
    findings: list[str] = []
    if _PEM_PRIVATE_KEY_RE.search(text):
        findings.append("私钥正文")
    for token_re in _KNOWN_TOKEN_RES:
        match = token_re.search(text)
        if match is not None and not _looks_like_placeholder(match.group(0)):
            findings.append("疑似访问令牌")
            break
    for match in _SENSITIVE_LITERAL_RE.finditer(text):
        value = match.group("value")
        if _looks_like_placeholder(value):
            continue
        line = text.count("\n", 0, match.start()) + 1
        findings.append(f"第 {line} 行硬编码 {match.group('name')}")
    if findings:
        raise PackagingError(f"{label} 检出潜在敏感值：" + "；".join(findings))


def _read_release_files(source_root: Path) -> dict[str, bytes]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise PackagingError(f"源码目录不存在：{source_root}")
    expected = set(RELEASE_FILES)
    if len(expected) != 20 or len(expected) != len(RELEASE_FILES):
        raise PackagingError("发布清单必须恰好包含 20 个不重复文件")
    for relative in expected:
        if _is_forbidden_path(relative):
            raise PackagingError(f"发布清单包含禁止项：{relative}")

    unknown_regular_files: list[str] = []
    for directory, directory_names, file_names in os.walk(source_root, followlinks=False):
        directory_path = Path(directory)
        for name in tuple(directory_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise PackagingError(
                    f"源码树不接受符号链接：{_posix_relative(candidate, source_root)}"
                )
        for name in file_names:
            candidate = directory_path / name
            relative = _posix_relative(candidate, source_root)
            if candidate.is_symlink():
                raise PackagingError(f"源码树不接受符号链接：{relative}")
            if not candidate.is_file():
                raise PackagingError(f"源码树只接受普通文件：{relative}")
            if relative in expected or _is_forbidden_path(relative):
                continue
            unknown_regular_files.append(relative)
    if unknown_regular_files:
        rendered = "、".join(sorted(unknown_regular_files))
        raise PackagingError(f"发现未评审且不在 20 项发布清单内的源码文件：{rendered}")

    payloads: dict[str, bytes] = {}
    for relative in RELEASE_FILES:
        path = source_root / Path(*PurePosixPath(relative).parts)
        if path.is_symlink() or not path.is_file():
            raise PackagingError(f"发布清单文件缺失、不是普通文件或为符号链接：{relative}")
        data = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PackagingError(f"发布文本不是有效 UTF-8：{relative}") from exc
            scan_text_for_secrets(text, label=f"打包前 {relative}")
        payloads[relative] = data
    return payloads


def _zip_info(archive_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.flag_bits |= 0x800
    return info


def _write_archive(payloads: Mapping[str, bytes], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for relative in sorted(payloads):
                archive.writestr(_zip_info(ARCHIVE_ROOT + relative), payloads[relative])
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def verify_archive(archive_path: Path, expected_payloads: Mapping[str, bytes]) -> None:
    expected_names = {ARCHIVE_ROOT + relative for relative in RELEASE_FILES}
    if not zipfile.is_zipfile(archive_path):
        raise PackagingError("输出文件不是有效的 ZIP/.skill 归档")
    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise PackagingError("归档包含重复路径")
        if set(names) != expected_names or len(names) != 20:
            raise PackagingError("归档内容与 20 项发布清单不一致")
        for info in infos:
            if info.is_dir() or not info.filename.startswith(ARCHIVE_ROOT):
                raise PackagingError(f"归档根目录或成员类型无效：{info.filename}")
            relative = info.filename[len(ARCHIVE_ROOT) :]
            if _is_forbidden_path(relative):
                raise PackagingError(f"归档包含禁止项：{relative}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PackagingError(f"归档不接受符号链接：{relative}")
            if info.flag_bits & 0x1:
                raise PackagingError(f"归档不接受加密成员：{relative}")
            data = archive.read(info)
            if data != expected_payloads.get(relative):
                raise PackagingError(f"归档成员与源码逐字节不一致：{relative}")
            if PurePosixPath(relative).suffix.lower() in TEXT_SUFFIXES:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise PackagingError(f"包内文本不是有效 UTF-8：{relative}") from exc
                scan_text_for_secrets(text, label=f"打包后 {relative}")


def run_packaged_contract_tests(archive_path: Path) -> None:
    """Extract only verified members and execute the packaged offline suite."""
    with tempfile.TemporaryDirectory(prefix="coze-skill-verify-") as folder:
        extraction_root = Path(folder)
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                target = extraction_root.joinpath(*PurePosixPath(info.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        scripts = extraction_root / SKILL_NAME / "scripts"
        test_script = scripts / "test_bridge.py"
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(test_script)],
            cwd=scripts,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            combined = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
            tail = "\n".join(combined.splitlines()[-30:])
            raise PackagingError(f"包内 test_bridge.py 未通过（退出码 {result.returncode}）：\n{tail}")


def build_skill(
    *,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_path: Path,
    run_tests: bool = True,
) -> Path:
    payloads = _read_release_files(Path(source_root))
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".candidate",
        dir=destination.parent,
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    candidate.unlink(missing_ok=True)
    try:
        _write_archive(payloads, candidate)
        verify_archive(candidate, payloads)
        if run_tests:
            run_packaged_contract_tests(candidate)
        # Preserve the previous known-good release until the new candidate has
        # passed every source, archive and packaged-contract gate.
        os.replace(candidate, destination)
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    return destination


def _temporary_output_path() -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f"{SKILL_NAME}-", suffix=".skill")
    os.close(descriptor)
    Path(name).unlink(missing_ok=True)
    return Path(name)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help="Skill 源码根目录（默认使用仓库 source 目录）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="临时 .skill 输出路径；省略时由系统生成临时路径",
    )
    parser.add_argument(
        "--skip-packaged-tests",
        action="store_true",
        help="仅供打包器单元测试使用；正式候选包不应跳过包内契约测试",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output or _temporary_output_path()
    try:
        result = build_skill(
            source_root=args.source,
            output_path=output,
            run_tests=not args.skip_packaged_tests,
        )
    except (OSError, PackagingError, subprocess.SubprocessError) as exc:
        print(f"打包失败：{exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
