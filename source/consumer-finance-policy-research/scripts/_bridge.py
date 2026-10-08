"""Shared, deterministic adapter for the policy-research backend.

This module deliberately contains no research logic.  It owns credentials,
HTTP safety, exact backend-contract validation and small model-facing
projections used by the twelve Skill entry points.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import stat
import sys
import time
import unicodedata
from urllib.parse import urlparse, urlsplit, urlunsplit

from coze_workload_identity import requests

# No bundled deployment endpoint or local credential module.
BASE_URL = os.environ.get("RESEARCH_API_BASE_URL", "").rstrip("/")
NATIVE_CREDENTIAL_MODE = "native_proxy"
PILOT_CREDENTIAL_MODE = "pilot_direct_env"
VALID_CREDENTIAL_MODES = frozenset({
    NATIVE_CREDENTIAL_MODE,
    PILOT_CREDENTIAL_MODE,
})
# Direct-mode credentials are sent only to an explicitly configured origin.
PILOT_API_ORIGIN = os.environ.get("RESEARCH_PILOT_API_ORIGIN", "").rstrip("/")
_PILOT_TOKEN_ENV = "POLICY_RESEARCH_PILOT_ACCESS_TOKEN"
_PILOT_SUBJECT_ENV = "POLICY_RESEARCH_PILOT_SUBJECT_ID"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
MAX_FILE_SIZE = 16 * 1024 * 1024
MAX_MATERIALS_PER_DRAFT = 10
MAX_POLICY_INPUT_CHARS = 20_000
ATTACHMENT_DISCOVERY_TIMEOUT_SECONDS = 12.0
ATTACHMENT_DISCOVERY_INITIAL_POLL_SECONDS = 0.25
ATTACHMENT_DISCOVERY_MAX_POLL_SECONDS = 1.0
ALLOWED_FILE_EXTENSIONS = frozenset({
    ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv",
})
_DEFAULT_ATTACHMENT_ROOTS = (
    "/tmp,/workspace/uploads,/workspace/input,/workspace/files"
)
_configured_attachment_roots = os.environ.get("RESEARCH_ATTACHMENT_ROOTS", "")


def _runtime_agent_attachment_root():
    """Return the current Coze Agent drive root when installed under ``.skills``."""
    skill_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    skills_dir = os.path.dirname(skill_dir)
    if os.path.basename(skills_dir) != ".skills":
        return ""
    return os.path.dirname(skills_dir)


def _build_attachment_roots(*, runtime_root, configured_roots):
    """Choose one attachment trust surface without silently widening it."""
    if runtime_root:
        candidates = [runtime_root]
    else:
        raw = str(configured_roots or "").strip() or _DEFAULT_ATTACHMENT_ROOTS
        candidates = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(dict.fromkeys(os.path.realpath(item) for item in candidates))


ATTACHMENT_ROOTS = _build_attachment_roots(
    runtime_root=_runtime_agent_attachment_root(),
    configured_roots=_configured_attachment_roots,
)

logger = logging.getLogger("research_bridge")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.WARNING)

_REDACTED = "***"
_SENSITIVE_KEYS = frozenset({
    "x-research-bridge-key",
    "x-research-subject",
    "authorization",
    "cookie",
    "set-cookie",
    "bridge_key",
    "subject",
    "token",
    "password",
    "secret",
})
_ERROR_CODE_RE = re.compile(r"\b(E_[A-Z0-9_]+)\b")
_DRAFT_ID_RE = re.compile(r"rdraft_[a-f0-9]{24}")
_MATERIAL_REF_RE = re.compile(r"dmat_[a-f0-9]{24}")
_JOB_ID_RE = re.compile(r"rjob_[a-f0-9]{24}")
_REFERENCE_AUTHORIZATION_ID_RE = re.compile(r"rauth_[A-Za-z0-9_-]{32}")
# 旧存储句柄使用 12 位摘要，渲染阶段句柄使用 20 位摘要，当前对外
# 下载句柄绑定服务端归属后使用 24 位摘要。只接受这三种已发布格式，
# 避免用不受限长度的十六进制串掩盖合同漂移。
_ARTIFACT_ID_RE = re.compile(
    r"art_(?:docx|pdf)_(?:[a-f0-9]{12}|[a-f0-9]{20}|[a-f0-9]{24})"
)

# CLI-facing tokens deliberately contain no slash.  Older published Agents may
# still call the composite values, so keep them as hidden compatibility aliases
# in ``build_permission`` rather than exposing them through argparse choices.
PERMISSION_PROFILES = {
    "public_web": ("public", "public_web"),
    "user_owned": ("licensed_external", "user_owned"),
    "licensed_enterprise": ("licensed_external", "licensed_enterprise"),
}
PERMISSION_PROFILE_ALIASES = {
    "public/public_web": "public_web",
    "licensed_external/user_owned": "user_owned",
    "licensed_external/licensed_enterprise": "licensed_enterprise",
}
VALID_PERMISSIONS = frozenset(PERMISSION_PROFILES)
VALID_AUTHORIZERS = frozenset({"user_explicit", "channel_verified"})
VALID_RETENTION_MODES = frozenset({"task_only", "session_only"})
REFERENCE_KINDS = frozenset({"url", "pasted_text"})
REFERENCE_DECISIONS = frozenset({
    "public_web", "user_owned", "licensed_enterprise", "exclude",
})
REFERENCE_CHOICE_LABELS = {
    "公开网页": "public_web",
    "本人合法取得": "user_owned",
    "企业已授权": "licensed_enterprise",
    "不使用该材料": "exclude",
}
REFERENCE_CHOICE_DESCRIPTIONS = (
    {"选择": "公开网页", "说明": "任何人可公开访问，且可用于本次研究"},
    {"选择": "本人合法取得", "说明": "由本人合法取得或购买，并可用于本次研究"},
    {"选择": "企业已授权", "说明": "单位已有订阅、采购或其他授权，且覆盖本次用途"},
    {"选择": "不使用该材料", "说明": "不把这项材料纳入本次研究"},
)
MAX_REFERENCE_URL_BYTES = 8 * 1024
# Pasted text must remain in the Agent's current tool context until the job is
# submitted.  Keep the Skill-side limit intentionally far below the backend's
# transport ceiling; longer material belongs in the file-draft path.
MAX_PASTED_TEXT_BYTES = 20_000
# Several individually valid pasted items still share the Agent's current
# tool context until submission.  Bound the aggregate to avoid a 10 x 20 KB
# cross-turn payload; larger corpora belong in the file-draft path.
MAX_TOTAL_PASTED_TEXT_BYTES = 40_000


def _sanitize(obj):
    """Return a bounded log-safe copy; this is never used as a response."""
    if isinstance(obj, dict):
        return {
            key: (_REDACTED if str(key).lower() in _SENSITIVE_KEYS else _sanitize(value))
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_sanitize(item) for item in obj]
    if isinstance(obj, str):
        if obj.lower().startswith(("postgresql://", "postgres://")):
            return _REDACTED
        if len(obj) > 500:
            return obj[:80] + "...[truncated]"
    return obj


def error_resp(code, message, **extra):
    result = {"status": "error", "错误码": str(code), "错误信息": str(message)}
    result.update(extra)
    return result


def success_resp(**fields):
    result = {"status": "success"}
    result.update(fields)
    return result


def output(data):
    print(json.dumps(data, ensure_ascii=False))
    raise SystemExit(0 if data.get("status") == "success" else 1)


def is_error(obj):
    return isinstance(obj, dict) and obj.get("status") == "error"


def parse_json(resp):
    try:
        value = resp.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def require_contract(body, required, *, operation):
    """Fail closed when a nominal 2xx response does not match the API contract."""
    if not isinstance(body, dict):
        return error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回了无法识别的数据")
    missing = [name for name in required if body.get(name) in (None, "")]
    if missing:
        logger.warning("backend_contract_error operation=%s missing=%s", operation, ",".join(missing))
        return error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回缺少必要字段，未确认操作成功")
    return None


def require_handle_match(body, field, expected, kind, *, operation):
    """Reject malformed or cross-resource handles in nominal 2xx responses."""
    actual = str(body.get(field) or "").strip() if isinstance(body, dict) else ""
    try:
        validate_handle(actual, kind)
    except ValueError:
        return error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回了无效操作句柄")
    if expected is not None and actual != expected:
        return error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回的资源与当前任务不一致")
    return None


def _load_coze_credential(name):
    """Load one Coze credential without binding an imported package to its old id."""
    explicit_skill_id = str(os.environ.get("RESEARCH_CREDENTIAL_SKILL_ID") or "").strip()
    if explicit_skill_id:
        return os.environ.get(f"COZE_{name}_{explicit_skill_id}", "")

    # coze-workload-identity exposes the id of the Skill that is actually
    # running.  Prefer it over the source-project id so copied/imported Skills
    # cannot accidentally select a stale credential placeholder.
    runtime_skill_id = str(os.environ.get("skill_id") or "").strip()
    if runtime_skill_id:
        runtime_value = os.environ.get(f"COZE_{name}_{runtime_skill_id}", "")
        if runtime_value:
            return runtime_value

    prefix = f"COZE_{name}_"
    matches = [
        value for key, value in os.environ.items()
        if key.startswith(prefix) and str(value or "")
    ]
    return matches[0] if len(matches) == 1 else ""


def credential_mode():
    configured = str(
        os.environ.get("RESEARCH_CREDENTIAL_MODE") or NATIVE_CREDENTIAL_MODE
    ).strip().lower()
    return configured or NATIVE_CREDENTIAL_MODE


def load_bridge_key():
    if credential_mode() == PILOT_CREDENTIAL_MODE:
        # Do not inspect COZE_* placeholders
        # in explicit pilot mode: the direct variables are the sole authority.
        return str(os.environ.get(_PILOT_TOKEN_ENV) or "").strip()
    if credential_mode() != NATIVE_CREDENTIAL_MODE:
        return ""
    configured = _load_coze_credential("RESEARCH_BRIDGE_KEY")
    if configured:
        return configured
    return ""


def load_subject():
    if credential_mode() == PILOT_CREDENTIAL_MODE:
        return str(os.environ.get(_PILOT_SUBJECT_ENV) or "").strip()
    if credential_mode() != NATIVE_CREDENTIAL_MODE:
        return ""
    configured = _load_coze_credential("RESEARCH_SUBJECT")
    if configured:
        return configured
    return ""


def uses_outbound_auth_proxy():
    """Return whether Coze will inject registered credentials at its proxy.

    New Coze Agent sandboxes expose the outbound-auth proxy and identity ticket,
    but deliberately do not materialize Skill credential values in ``os.environ``.
    The wrapped ``coze_workload_identity.requests`` client routes the request
    through that proxy, which injects the registered headers for the allowed
    destination domain.  Older/local runtimes keep using the explicit
    environment fallback below.
    """
    # Keep this in lock-step with coze-workload-identity's HttpsProxy(): SDK
    # 0.1.14 prefers COZE_SKILL_PROXY_DOMAIN and retains the historical
    # COZE_OUTBOUND_AUTH_PROXY fallback.  The proxy itself, rather than the
    # Skill process, owns the registered credential values.
    proxy_domain = (
        os.environ.get("COZE_SKILL_PROXY_DOMAIN")
        or os.environ.get("COZE_OUTBOUND_AUTH_PROXY")
        or ""
    )
    return bool(
        str(proxy_domain).strip()
        and str(os.environ.get("identity_ticket") or "").strip()
    )


def do_request(method, path, *, json_body=None, data=None, files=None,
               read_timeout=READ_TIMEOUT, require_subject=True):
    """Execute one authenticated request and normalize all failure shapes."""
    mode = credential_mode()
    if mode not in VALID_CREDENTIAL_MODES:
        return error_resp("CONFIG_ERROR", "RESEARCH_CREDENTIAL_MODE 配置无效")
    if not BASE_URL:
        return error_resp("CONFIG_ERROR", "RESEARCH_API_BASE_URL 环境变量未配置")
    if not is_https_url(BASE_URL):
        return error_resp("CONFIG_ERROR", "RESEARCH_API_BASE_URL 必须是 HTTPS 地址")
    if mode == PILOT_CREDENTIAL_MODE and (not PILOT_API_ORIGIN or BASE_URL != PILOT_API_ORIGIN):
        return error_resp("CONFIG_ERROR", "受控试用模式需要明确配置且匹配的研究后端")
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-store",
    }
    bridge_key = load_bridge_key()
    subject = load_subject()
    if not bridge_key:
        return error_resp("CONFIG_ERROR", "RESEARCH_BRIDGE_KEY 凭证未配置")
    headers["X-Research-Bridge-Key"] = bridge_key
    if subject:
        headers["X-Research-Subject"] = subject
    if require_subject and not subject:
        return error_resp("SUBJECT_MISSING", "缺少可信用户身份标识，已拒绝请求")

    kwargs = {}
    if json_body is not None:
        kwargs["json"] = json_body
    if data is not None:
        kwargs["data"] = data
    if files is not None:
        kwargs["files"] = files
    if mode == PILOT_CREDENTIAL_MODE:
        # Direct pilot credentials are visible inside the Skill runtime.  Never
        # allow an HTTP redirect to carry them away from the reviewed origin.
        kwargs["allow_redirects"] = False

    try:
        response = requests.request(
            method,
            f"{BASE_URL}{path}",
            headers=headers,
            timeout=(CONNECT_TIMEOUT, read_timeout),
            **kwargs,
        )
    except requests.exceptions.ConnectTimeout:
        return error_resp("CONNECT_TIMEOUT", "连接研究后端超时", 可重试=True)
    except requests.exceptions.ReadTimeout:
        return error_resp("READ_TIMEOUT", "读取研究后端响应超时", 可重试=True)
    except requests.exceptions.ConnectionError:
        return error_resp("CONNECTION_ERROR", "无法连接研究后端", 可重试=True)
    except requests.exceptions.Timeout:
        return error_resp("TIMEOUT", "请求超时", 可重试=True)
    except Exception as exc:
        logger.warning("request_failed type=%s", type(exc).__name__)
        return error_resp("REQUEST_ERROR", "请求异常", 可重试=False)

    if mode == PILOT_CREDENTIAL_MODE and 300 <= response.status_code < 400:
        return error_resp(
            "REDIRECT_BLOCKED",
            "受控试用模式禁止研究后端重定向",
            HTTP状态码=int(response.status_code),
            可重试=False,
        )
    if response.status_code >= 400:
        return _map_http_error(response)
    return response


def _map_http_error(response):
    """Handle FastAPI detail objects/strings and top-level JSON errors."""
    body = parse_json(response)
    envelope = body.get("detail", body)
    code = ""
    message = ""
    if isinstance(envelope, dict):
        code = str(envelope.get("error_code") or envelope.get("code") or "").strip()
        message = str(envelope.get("message") or envelope.get("error_message") or "").strip()
    elif isinstance(envelope, str):
        message = envelope.strip()
        match = _ERROR_CODE_RE.search(message)
        code = match.group(1) if match else ""

    code = code or f"HTTP_{response.status_code}"
    friendly = {
        "E_RESEARCH_JOB_NOT_COMPLETE": "研究任务尚未完成，请稍后再查询",
        "E_RESEARCH_REVISION_NOT_IMPLEMENTED": "当前试用版尚不支持自动生成修订版报告",
        "E_RESEARCH_SUBJECT_REQUIRED": "缺少可信用户身份，已拒绝请求",
        "E_DRAFT_EMPTY": "当前材料草稿为空，不能开始研究",
        "E_REFERENCE_AUTHORIZATION_PENDING": "这项材料仍需确认使用依据，请先完成材料授权选择",
        "E_REFERENCE_AUTHORIZATION_EXPIRED": "材料授权已过期，请重新添加这项链接或文字",
        "E_REFERENCE_CONTENT_MISMATCH": "当前材料内容与确认时不一致，请重新添加材料",
        "E_REFERENCE_AUTHORIZATION_ALREADY_USED": "这项临时材料授权已用于另一研究任务，请重新添加材料",
        "E_REFERENCE_AUTHORIZATION_NOT_FOUND": "当前材料授权已失效或不属于本次会话，请重新添加材料",
        "E_IDEMPOTENCY_KEY_REUSED": "同一研究请求的临时材料授权已变化；为避免重复付费，系统已拒绝再次创建任务。请继续查询原研究任务；只有明确需要重跑时才按显式重跑流程操作",
        "E_JOB_MATERIAL_LIMIT": "文件、网页链接和粘贴文字合计最多 10 项；请删除不必要材料后再启动研究",
        "HTTP_401": "Skill 开发者凭证未配置、已失效或未被认证代理注入",
        "HTTP_403": "Skill 凭证没有访问当前研究后端域名或资源的权限",
        "HTTP_407": "扣子出站认证代理未能完成凭证认证",
    }.get(code)
    message = friendly or message or f"研究后端返回 HTTP {response.status_code}"

    retry_after_raw = str(response.headers.get("Retry-After") or "").strip()
    retry_after = int(retry_after_raw) if retry_after_raw.isdigit() else None
    retryable = (
        response.status_code == 429
        or response.status_code >= 500
        or code in {"E_RESEARCH_JOB_NOT_COMPLETE", "E_RESEARCH_ALREADY_RUNNING"}
    )
    extra = {"HTTP状态码": int(response.status_code), "可重试": retryable}
    if retry_after is not None:
        extra["重试等待秒"] = retry_after
    if isinstance(envelope, dict):
        try:
            # Some conflict responses return the already-existing research
            # job.  Preserve it only after the same strict handle validation
            # used by successful responses; malformed server data is ignored.
            extra["_job_id"] = validate_handle(envelope.get("job_id"), "job")
        except ValueError:
            pass
    return error_resp(code, message, **extra)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_idempotency_key(request_body):
    """Create a stable key from the complete canonical request, before adding the key."""
    digest = hashlib.sha256(canonical_json(request_body).encode("utf-8")).hexdigest()[:40]
    return f"idp-{digest}"


def parse_json_list(raw, *, label):
    if raw in (None, ""):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"{label} 不是有效 JSON")
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是 JSON 数组")
    if len(value) > 10:
        raise ValueError(f"{label} 最多 10 项")
    return value


def parse_output_spec(raw):
    if raw in (None, ""):
        return {
            "target_format": "deep_dive_report",
            "report_profile": "standard_report",
            "formats": ["docx", "pdf"],
        }
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("output_spec 必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("output_spec 必须是 JSON 对象")
    formats = value.get("formats", [])
    if not isinstance(formats, list) or any(str(item).lower() not in {"docx", "pdf"} for item in formats):
        raise ValueError("output_spec.formats 只能包含 docx、pdf")
    return value


def build_permission(profile, *, authorized_by, retention_mode="task_only"):
    profile = PERMISSION_PROFILE_ALIASES.get(str(profile or "").strip(), str(profile or "").strip())
    if profile not in PERMISSION_PROFILES:
        raise ValueError("不支持的材料授权类型")
    if authorized_by not in VALID_AUTHORIZERS:
        raise ValueError("材料授权确认方式无效")
    if retention_mode not in VALID_RETENTION_MODES:
        raise ValueError("V1 仅支持 task_only 或 session_only 保留方式")
    scope, license_basis = PERMISSION_PROFILES[profile]
    return {
        "scope": scope,
        "confidentiality": scope,
        "license_basis": license_basis,
        "content_use": "full_analysis",
        "authorized_by": authorized_by,
        "retention_mode": retention_mode,
    }


def validate_reference_authorization_id(value):
    text = str(value or "").strip()
    if _REFERENCE_AUTHORIZATION_ID_RE.fullmatch(text) is None:
        raise ValueError("材料授权上下文无效")
    return text


def validate_reference_content(kind, content):
    """Validate reference input without deriving any permission metadata."""
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in REFERENCE_KINDS:
        raise ValueError("材料类型只能是 url 或 pasted_text")
    if not isinstance(content, str):
        raise ValueError("材料内容必须是文字")
    # URL spelling is normalized for a stable transport identity.  Pasted
    # policy text is evidence, so compatibility normalization (NFKC) must not
    # rewrite circled numbers, full-width punctuation or Roman numerals.
    value = content.strip()
    if not value:
        raise ValueError("材料内容不能为空")
    if normalized_kind == "url":
        value = unicodedata.normalize("NFKC", value)
        if len(value.encode("utf-8")) > MAX_REFERENCE_URL_BYTES:
            raise ValueError("网页链接过长")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise ValueError("网页链接格式无效") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("网页链接必须是未嵌入账号密码的 HTTP(S) 地址")
        try:
            host = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("网页链接域名无效") from exc
        rendered_host = f"[{host}]" if ":" in host else host
        is_default_port = (
            (parsed.scheme.lower() == "http" and port == 80)
            or (parsed.scheme.lower() == "https" and port == 443)
        )
        netloc = rendered_host if port is None or is_default_port else f"{rendered_host}:{port}"
        value = urlunsplit((
            parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, "",
        ))
    else:
        value = unicodedata.normalize("NFC", value)
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if (
            len(value.encode("utf-8")) > MAX_PASTED_TEXT_BYTES
            or not value
            or any(
                unicodedata.category(char).startswith("C") and char not in {"\n", "\t"}
                for char in value
            )
        ):
            raise ValueError("粘贴文字不能超过 20000 个 UTF-8 字节且不能包含不支持的控制字符；更长材料请作为 TXT、DOCX 或 PDF 文件上传")
    return normalized_kind, value


def parse_reference_choice(value):
    choice = str(value or "").strip()
    decision = REFERENCE_CHOICE_LABELS.get(choice)
    if decision is None:
        raise ValueError("授权选择只能是：公开网页、本人合法取得、企业已授权、不使用该材料")
    return decision


def _kind_label(kind):
    return "网页链接" if kind == "url" else "粘贴文字"


def _reference_item(authorization_id, kind, content):
    return {
        "authorization_id": authorization_id,
        "kind": kind,
        "content": content,
    }


def parse_reference_authorization_context(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError("内部材料授权上下文不是有效 JSON") from exc
    expected = {"authorization_id", "kind", "content"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("内部材料授权上下文字段无效")
    authorization_id = validate_reference_authorization_id(value.get("authorization_id"))
    kind, content = validate_reference_content(value.get("kind"), value.get("content"))
    return {
        "authorization_id": authorization_id,
        "kind": kind,
        "content": content,
    }


def parse_reference_items(raw):
    if raw in (None, ""):
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError("reference_items 不是有效 JSON") from exc
    if not isinstance(value, list):
        raise ValueError("reference_items 必须是 JSON 数组")
    if len(value) > 10:
        raise ValueError("reference_items 最多 10 项")
    result = []
    seen = set()
    seen_materials = set()
    total_pasted_text_bytes = 0
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"authorization_id", "kind", "content"}:
            raise ValueError(
                f"reference_items[{index}] 只能包含 authorization_id、kind、content"
            )
        authorization_id = validate_reference_authorization_id(item.get("authorization_id"))
        if authorization_id in seen:
            raise ValueError("同一个材料授权不能重复提交")
        seen.add(authorization_id)
        kind, content = validate_reference_content(item.get("kind"), item.get("content"))
        if kind == "pasted_text":
            total_pasted_text_bytes += len(content.encode("utf-8"))
            if total_pasted_text_bytes > MAX_TOTAL_PASTED_TEXT_BYTES:
                raise ValueError(
                    "reference_items 中粘贴文字合计不能超过 40000 个 UTF-8 字节；"
                    "更多内容请作为 TXT、DOCX 或 PDF 文件上传"
                )
        material_key = (kind, content)
        if material_key in seen_materials:
            raise ValueError("同一项链接或文字不能重复提交")
        seen_materials.add(material_key)
        result.append(_reference_item(authorization_id, kind, content))
    return result


def _validate_reference_response_base(body, *, expected_kind, operation):
    error = require_contract(
        body,
        (
            "authorization_id", "kind", "state", "material_label",
            "expires_at", "retention", "reuse_policy",
        ),
        operation=operation,
    )
    if error:
        return None, error
    try:
        authorization_id = validate_reference_authorization_id(body.get("authorization_id"))
    except ValueError:
        return None, error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回了无效操作句柄")
    kind = str(body.get("kind") or "").strip().lower()
    state = str(body.get("state") or "").strip().lower()
    if kind != expected_kind or state not in {"pending", "confirmed", "excluded"}:
        return None, error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回的材料状态无效")
    if body.get("retention") != "task_only":
        return None, error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回的材料使用范围无效")
    if body.get("reuse_policy") != "same_idempotent_job_only":
        return None, error_resp("BACKEND_CONTRACT_ERROR", f"{operation}返回的材料复用范围无效")
    return {
        "authorization_id": authorization_id,
        "kind": kind,
        "state": state,
        "material_label": str(body.get("material_label") or "")[:200],
        "expires_at": str(body.get("expires_at") or "")[:100],
    }, None


def project_prepared_reference(body, *, kind, content):
    base, error = _validate_reference_response_base(
        body, expected_kind=kind, operation="准备参考材料",
    )
    if error:
        return error
    state = base["state"]
    confirmation_required = body.get("confirmation_required")
    if state == "pending":
        allowed = body.get("allowed_choices")
        if (
            confirmation_required is not True
            or not isinstance(allowed, list)
            or len(allowed) != len(REFERENCE_DECISIONS)
            or set(map(str, allowed)) != REFERENCE_DECISIONS
        ):
            return error_resp("BACKEND_CONTRACT_ERROR", "准备参考材料返回缺少确认信息")
        return success_resp(
            用户可见信息={
                "材料标签": base["material_label"],
                "材料类型": _kind_label(kind),
                "授权状态": "待确认",
                "需要用户确认": True,
                "用户提示": "请确认这项材料用于本次研究的合法依据；无法确认时请选择“不使用该材料”。",
                "授权选项": [dict(item) for item in REFERENCE_CHOICE_DESCRIPTIONS],
            },
            _authorization_context={
                "authorization_id": base["authorization_id"],
                "kind": kind,
                "content": content,
            },
        )
    if (
        state != "confirmed"
        or confirmation_required is not False
        or str(body.get("decision") or "") != "public_web"
    ):
        return error_resp("BACKEND_CONTRACT_ERROR", "准备参考材料返回了意外授权状态")
    return success_resp(
        用户可见信息={
            "材料标签": base["material_label"],
            "材料类型": _kind_label(kind),
            "授权状态": "政府官网已自动确认",
            "需要用户确认": False,
            "材料使用状态": "可提交",
        },
        _reference_item=_reference_item(base["authorization_id"], kind, content),
    )


def project_confirmed_reference(body, *, context, expected_decision):
    base, error = _validate_reference_response_base(
        body, expected_kind=context["kind"], operation="确认参考材料",
    )
    if error:
        return error
    if base["authorization_id"] != context["authorization_id"]:
        return error_resp("BACKEND_CONTRACT_ERROR", "确认参考材料返回的资源不一致")
    state = base["state"]
    if (
        body.get("confirmation_required") is not False
        or str(body.get("decision") or "") != expected_decision
        or (expected_decision == "exclude" and state != "excluded")
        or (expected_decision != "exclude" and state != "confirmed")
    ):
        return error_resp("BACKEND_CONTRACT_ERROR", "确认参考材料返回了意外授权状态")
    if expected_decision == "exclude":
        return success_resp(用户可见信息={
            "材料标签": base["material_label"],
            "材料类型": _kind_label(context["kind"]),
            "授权状态": "已排除",
            "材料使用状态": "不用于本次研究",
        })
    return success_resp(
        用户可见信息={
            "材料标签": base["material_label"],
            "材料类型": _kind_label(context["kind"]),
            "授权状态": "已确认",
            "材料使用状态": "可提交",
        },
        _reference_item=_reference_item(
            base["authorization_id"], context["kind"], context["content"],
        ),
    )


def validate_handle(value, kind):
    patterns = {
        "draft": _DRAFT_ID_RE,
        "material": _MATERIAL_REF_RE,
        "job": _JOB_ID_RE,
        "artifact": _ARTIFACT_ID_RE,
    }
    text = str(value or "").strip()
    pattern = patterns.get(kind)
    if pattern is None or pattern.fullmatch(text) is None:
        raise ValueError(f"{kind} 操作句柄无效")
    return text


def validate_attachment_path(file_path, *, roots=None):
    """Allow only a regular, non-symlink file below an attachment mount."""
    candidate = str(file_path or "").strip()
    if not candidate or candidate.lower().startswith(("http://", "https://")):
        raise ValueError("附件路径无效")
    if os.path.islink(candidate):
        raise ValueError("不接受符号链接附件")
    resolved = os.path.realpath(candidate)
    root_source = ATTACHMENT_ROOTS if roots is None else roots
    allowed_roots = tuple(os.path.realpath(item) for item in root_source)
    allowed = False
    for root in allowed_roots:
        try:
            allowed = os.path.commonpath((root, resolved)) == root
        except ValueError:
            allowed = False
        if allowed:
            break
    if not allowed:
        raise ValueError("文件不在已配置的附件目录中")
    if not os.path.isfile(resolved):
        raise ValueError("选择的文件不存在或已失效")
    extension = os.path.splitext(resolved)[1].lower()
    if extension not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError("不支持该文件类型")
    return resolved


_COZE_MATERIALIZED_SUFFIX_RE = re.compile(
    r"_(?:\d{10,})_(?:\d+)_([A-Za-z0-9]{2,32})$"
)
_MAX_MATCHING_ATTACHMENT_CANDIDATES = 16


def _validate_attachment_filename(filename):
    requested = str(filename or "").strip()
    if (
        not requested
        or requested in {".", ".."}
        or os.path.basename(requested) != requested
        or "/" in requested
        or "\\" in requested
        or "\x00" in requested
    ):
        raise ValueError("附件文件名无效")
    extension = os.path.splitext(requested)[1].lower()
    if extension not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError("不支持该文件类型")
    return requested


def _attachment_name_matches(actual_name, requested_name):
    """Match an original filename to its exact or Coze-materialized basename."""
    actual = unicodedata.normalize("NFC", str(actual_name or ""))
    requested = unicodedata.normalize("NFC", str(requested_name or ""))
    if actual == requested:
        return True
    requested_stem, requested_ext = os.path.splitext(requested)
    actual_stem, actual_ext = os.path.splitext(actual)
    if actual_ext.lower() != requested_ext.lower():
        return False
    if not actual_stem.startswith(requested_stem + "_"):
        return False
    suffix = actual_stem[len(requested_stem):]
    return _COZE_MATERIALIZED_SUFFIX_RE.fullmatch(suffix) is not None


def _attachment_stat_signature(file_stat):
    """Return the stable fields used to detect path replacement or mutation."""
    return (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        int(file_stat.st_size),
        int(getattr(file_stat, "st_mtime_ns", int(file_stat.st_mtime * 1_000_000_000))),
    )


def _attachment_content_snapshot(file_path):
    """Hash one regular file without trusting mutable filesystem metadata.

    Coze Drive is backed by a FUSE-like filesystem whose first read may refresh
    inode/mtime metadata even when the bytes did not change.  Security therefore
    rests on ``O_NOFOLLOW``, regular-file checks, the size ceiling and the full
    content digest.  The later upload read verifies that digest again before any
    network request.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(file_path, flags)
        opened_before = os.fstat(descriptor)
        current_before = os.stat(file_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or not stat.S_ISREG(current_before.st_mode)
        ):
            raise ValueError("附件在识别时发生变化，请重新上传")

        # Files above the upload ceiling can still be returned as a single
        # candidate so the caller emits its established FILE_TOO_LARGE result.
        # They cannot be used to disambiguate duplicate names without hashing
        # unbounded input, so their digest remains deliberately absent.
        digest = None
        if opened_before.st_size <= MAX_FILE_SIZE:
            hasher = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, MAX_FILE_SIZE + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise ValueError("附件在识别时超过大小限制，请重新上传")
                hasher.update(chunk)
            digest = hasher.hexdigest()

        opened_after = os.fstat(descriptor)
        current_after = os.stat(file_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_after.st_mode)
            or not stat.S_ISREG(current_after.st_mode)
            or int(opened_before.st_size) != int(opened_after.st_size)
        ):
            raise ValueError("附件在识别时发生变化，请重新上传")
        return {
            # Keep the legacy signature for diagnostics and old callers, but
            # consumers must bind security to size + SHA-256, not inode/mtime.
            "signature": _attachment_stat_signature(opened_after),
            "size_bytes": int(opened_after.st_size),
            "sha256": digest,
        }
    except OSError as exc:
        raise ValueError("附件当前无法安全识别，请重新上传") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _scan_attachment_matches(requested, allowed_roots):
    """Return direct-child matches or fail immediately on unsafe ambiguity."""
    matches = []
    saw_symlink = False
    inspected = 0
    for root in allowed_roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = os.scandir(root)
        except OSError:
            continue
        with entries:
            for entry in entries:
                inspected += 1
                if inspected > 1024:
                    raise ValueError("附件目录内容过多，无法安全确定唯一文件")
                if not _attachment_name_matches(entry.name, requested):
                    continue
                if entry.is_symlink():
                    saw_symlink = True
                    continue
                try:
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    is_file = False
                if not is_file:
                    continue
                matches.append(validate_attachment_path(entry.path, roots=allowed_roots))
                if len(matches) > _MAX_MATCHING_ATTACHMENT_CANDIDATES:
                    raise ValueError("同名附件过多，无法安全确定要使用哪一个")
    matches = list(dict.fromkeys(matches))
    if saw_symlink:
        raise ValueError("不接受符号链接附件")
    return matches


def resolve_attachment_file(
        filename, *, roots=None, include_proof=False, wait_seconds=0.0,
        monotonic_fn=None, sleep_fn=None):
    """Resolve one uploaded filename inside explicit attachment roots.

    Only the root directory itself is inspected.  The function never performs
    an unbounded recursive scan, never follows symlinks and requires exactly one
    matching payload.  Coze's deterministic materialization suffix is accepted
    while the caller-provided original filename remains the upload display
    name.  Multiple aliases/copies are collapsed only when every candidate has
    byte-identical content; otherwise the resolver fails closed.
    """
    requested = _validate_attachment_filename(filename)
    root_source = ATTACHMENT_ROOTS if roots is None else roots
    allowed_roots = tuple(dict.fromkeys(os.path.realpath(item) for item in root_source))
    monotonic_fn = monotonic_fn or time.monotonic
    sleep_fn = sleep_fn or time.sleep
    timeout = max(0.0, float(wait_seconds or 0.0))
    deadline = monotonic_fn() + timeout
    poll_seconds = ATTACHMENT_DISCOVERY_INITIAL_POLL_SECONDS
    while True:
        matches = _scan_attachment_matches(requested, allowed_roots)
        if matches:
            break
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            raise ValueError("未找到刚上传的文件，请重新上传后再试")
        sleep_fn(min(poll_seconds, remaining))
        poll_seconds = min(
            poll_seconds * 2,
            ATTACHMENT_DISCOVERY_MAX_POLL_SECONDS,
        )
    proof_by_path = {}
    if len(matches) != 1:
        for path in matches:
            proof_by_path[path] = _attachment_content_snapshot(path)
        payload_fingerprints = {
            (proof["signature"][2], proof["sha256"])
            for proof in proof_by_path.values()
        }
        if None in {proof["sha256"] for proof in proof_by_path.values()}:
            raise ValueError("发现多个同名附件，无法安全确定要使用哪一个")
        if len(payload_fingerprints) != 1:
            raise ValueError("发现多个同名但内容不同的附件，无法安全确定要使用哪一个")

    requested_nfc = unicodedata.normalize("NFC", requested)
    matches.sort(key=lambda path: (
        unicodedata.normalize("NFC", os.path.basename(path)) != requested_nfc,
        os.path.normcase(path),
    ))
    selected = matches[0]
    if include_proof:
        proof = proof_by_path.get(selected) or _attachment_content_snapshot(selected)
        return selected, proof
    return selected


def safe_int(value, *, default=0, minimum=None, maximum=None):
    """Convert backend numeric fields without letting malformed data crash a tool."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = int(default)
    if minimum is not None:
        number = max(int(minimum), number)
    if maximum is not None:
        number = min(int(maximum), number)
    return number


def project_material(material):
    permission = material.get("permission") if isinstance(material.get("permission"), dict) else {}
    return {
        "文件名": str(material.get("filename") or ""),
        "文件类型": str(material.get("mime_type") or ""),
        "文件大小": safe_int(material.get("size_bytes"), minimum=0),
        "保留方式": str(permission.get("retention_mode") or "task_only"),
        "_material_ref": str(material.get("material_ref") or ""),
    }


def project_draft(body):
    materials = body.get("materials") if isinstance(body.get("materials"), list) else []
    return success_resp(
        草稿状态=str(body.get("status") or ""),
        材料数量=safe_int(body.get("material_count"), minimum=0, maximum=MAX_MATERIALS_PER_DRAFT),
        材料列表=[project_material(item) for item in materials if isinstance(item, dict)],
        _draft_id=str(body.get("draft_id") or ""),
    )


def project_job_status(body, *, retry_after=None):
    progress = body.get("progress") if isinstance(body.get("progress"), dict) else {}
    status = str(body.get("status") or "")
    if status in {"completed", "completed_with_gaps"}:
        delivery_notice = "任务已结束，请查询一次最终结果并直接交付可用报告或终态说明"
    elif status == "cancelled":
        delivery_notice = "任务已取消，未生成可交付结果"
    elif status == "failed":
        delivery_notice = "任务已结束，未生成可交付结果；不会自动重跑"
    else:
        delivery_notice = _project_delivery_notice(body.get("delivery_notice"))
    result = success_resp(
        任务状态=status,
        当前阶段=str(progress.get("stage") or ""),
        完成比例=safe_int(progress.get("percent"), minimum=0, maximum=100),
        降级交付=bool(body.get("limited_delivery", False)),
        交付说明=delivery_notice,
        后台恢复处理中=bool(body.get("recovery_required", False)),
        用户无需操作=True,
        _job_id=str(body.get("job_id") or ""),
    )
    if retry_after is not None:
        result["建议等待秒"] = safe_int(retry_after, minimum=1, maximum=3600)
    return result


def _first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _bounded_list(value, limit):
    return list(value[:limit]) if isinstance(value, list) else []


_WEB_SOURCE_TYPES = frozenset({
    "web", "url", "public_web", "official_web", "autonomous_web", "网页",
})
_USER_FILE_SOURCE_TYPES = frozenset({
    "user_file", "uploaded_file", "local_file", "attachment", "file",
    "user_upload", "用户文件", "用户提供文件",
})
_UNUSED_SOURCE_DECISIONS = frozenset({
    "not_used", "unused", "rejected", "excluded", "exclude", "not_adopted",
})
_USER_FILE_NO_LINK_NOTICE = "用户提供文件（无公开链接）"
_MARKDOWN_ASCII_PUNCTUATION_RE = re.compile(
    r"[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]"
)


def _escape_markdown_plain_text(value):
    """Return inert Markdown that renders the original visible text.

    Numeric entities are used instead of backslash escaping because a real
    filename may itself contain backticks, brackets, a Markdown link or a
    URL-looking substring.  Encoding every ASCII punctuation character keeps
    the rendered label intact while making it impossible for that label to
    create a second link target.
    """
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return _MARKDOWN_ASCII_PUNCTUATION_RE.sub(
        lambda match: "&#{};".format(ord(match.group(0))),
        text,
    )


def _safe_public_source_url(value):
    """Keep the backend URL spelling while rejecting non-HTTP(S) link targets."""
    text = str(value or "").strip()
    if not text or "\\" in text:
        return ""
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in text):
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    scheme = parsed.scheme.lower()
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        return ""
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject alternate numeric host spellings such as 127.1,
        # 2130706433, 0177.0.0.1 or 0x7f000001.
        numeric_labels = host.split(".")
        if numeric_labels and all(
            re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label, re.IGNORECASE)
            for label in numeric_labels
        ):
            return ""
    else:
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_private
        ):
            return ""
    return text


def _source_type(item, public_url):
    raw_type = _first_text(
        item.get("source_type"),
        item.get("material_kind"),
        item.get("source_kind"),
        item.get("kind"),
    ).lower()
    if raw_type in _WEB_SOURCE_TYPES:
        return "web"
    if raw_type in _USER_FILE_SOURCE_TYPES:
        return "user_file"
    if public_url:
        return "web"
    locator = _first_text(item.get("url_or_locator"), item.get("locator"))
    if locator == _USER_FILE_NO_LINK_NOTICE:
        return "user_file"
    filename = _first_text(
        item.get("original_filename"),
        item.get("intake_filename"),
        item.get("filename"),
        item.get("source_filename"),
    )
    if os.path.splitext(filename)[1].lower() in ALLOWED_FILE_EXTENSIONS:
        return "user_file"
    return ""


def _source_was_used_or_presented(item):
    decision = str(item.get("use_decision") or "").strip().lower()
    if decision in _UNUSED_SOURCE_DECISIONS:
        return False
    explicit_markers = [
        item.get(key)
        for key in (
            "used_in_report", "presented_in_report", "actually_used",
            "actually_presented", "included_in_report",
        )
        if key in item
    ]
    if explicit_markers and not any(value is True for value in explicit_markers):
        return False
    return True


def _project_source_locator(item, public_url):
    locators = item.get("locators") if isinstance(item.get("locators"), list) else []
    projected = []
    for locator in locators[:20]:
        if isinstance(locator, dict):
            text = _first_text(
                locator.get("locator"), locator.get("position"),
                locator.get("page"), locator.get("section"),
            )
        elif isinstance(locator, (str, int, float)) and not isinstance(locator, bool):
            text = str(locator).strip()
        else:
            text = ""
        if text and text != public_url and text not in projected:
            projected.append(text)
    fallback = _first_text(item.get("locator"), item.get("position"))
    if not fallback:
        combined = _first_text(item.get("url_or_locator"))
        fallback = "" if combined == public_url else combined
    if fallback and fallback != public_url and fallback not in projected:
        projected.append(fallback)
    return "；".join(projected)


def _project_sources(value):
    """Project the backend's canonical snapshot source rows only."""
    rows = []
    seen = set()
    for item in _bounded_list(value, 100):
        if not isinstance(item, dict) or not _source_was_used_or_presented(item):
            continue
        source_type = str(item.get("source_type") or "").strip().lower()
        if source_type == "web":
            public_url = _safe_public_source_url(item.get("url"))
            if not public_url:
                continue
            title = _first_text(
                item.get("source_title"), item.get("title"), public_url,
            )
            filename = ""
            locator = _project_source_locator(item, public_url)
            display_type = "网页"
        elif source_type == "user_file":
            filename = _first_text(
                item.get("source_title"), item.get("title"),
            )
            if not filename:
                continue
            title = filename
            public_url = ""
            locator = _USER_FILE_NO_LINK_NOTICE
            display_type = "用户文件"
        else:
            continue
        dedupe_key = (display_type, title, public_url, locator)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        safe_display = _escape_markdown_plain_text(title)
        markdown_line = (
            "[{}]({})".format(safe_display, public_url)
            if display_type == "网页"
            else "{}（{}）".format(safe_display, _USER_FILE_NO_LINK_NOTICE)
        )
        rows.append({
            "来源类型": display_type,
            "标题": title,
            "文件名": filename,
            "安全显示文本": safe_display,
            "Markdown显示行": markdown_line,
            "来源等级": str(item.get("source_level") or ""),
            "链接": public_url,
            "位置或说明": locator,
        })
    return rows


def _project_artifact(item):
    if not isinstance(item, dict):
        return None
    return {
        "格式": str(item.get("format") or ""),
        "文件名": str(item.get("filename") or ""),
        "文件类型": str(item.get("mime_type") or ""),
        "文件大小": safe_int(item.get("byte_size"), minimum=0),
        "制品状态": str(item.get("status") or ""),
        "保留至": str(item.get("retention_expires_at") or ""),
        "错误": item.get("error"),
        "_artifact_id": str(item.get("artifact_id") or ""),
    }


_REPORT_ARTIFACT_CONTRACTS = {
    "docx": {
        "suffix": ".docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    "pdf": {
        "suffix": ".pdf",
        "mime_type": "application/pdf",
    },
}


def _is_ready_report_artifact(item):
    """Accept only a complete, internally consistent downloadable report artifact."""
    if not isinstance(item, dict):
        return False
    report_format = str(item.get("格式") or "").strip().lower()
    contract = _REPORT_ARTIFACT_CONTRACTS.get(report_format)
    if contract is None:
        return False
    artifact_id = str(item.get("_artifact_id") or "").strip()
    try:
        validate_handle(artifact_id, "artifact")
    except ValueError:
        return False
    filename = str(item.get("文件名") or "").strip()
    return all((
        artifact_id.startswith(f"art_{report_format}_"),
        item.get("制品状态") == "ready",
        bool(filename),
        os.path.basename(filename) == filename,
        "\\" not in filename,
        filename.lower().endswith(contract["suffix"]),
        item.get("文件类型") == contract["mime_type"],
        safe_int(item.get("文件大小"), minimum=0) > 0,
        item.get("错误") in (None, ""),
    ))


def _safe_ready_report_artifacts(projected_artifacts):
    """Keep every unambiguous readable report file instead of all-or-nothing loss.

    A failed PDF must not hide a valid DOCX (or vice versa).  Ambiguous duplicate
    handles or duplicate formats are still excluded deterministically.
    """
    ready = [
        item for item in projected_artifacts
        if _is_ready_report_artifact(item)
    ]
    id_counts = {}
    format_counts = {}
    for item in ready:
        artifact_id = item["_artifact_id"]
        report_format = str(item["格式"]).lower()
        id_counts[artifact_id] = id_counts.get(artifact_id, 0) + 1
        format_counts[report_format] = format_counts.get(report_format, 0) + 1
    return [
        item for item in ready
        if id_counts.get(item["_artifact_id"]) == 1
        and format_counts.get(str(item["格式"]).lower()) == 1
    ]


def _humanize_report_artifacts(artifacts, *, reference_candidate):
    if reference_candidate:
        basename = "消费金融政策研究报告（供讨论参考，需人工复核）"
    else:
        basename = "消费金融政策研究报告"
    projected = []
    for item in artifacts:
        report = dict(item)
        report_format = str(report.get("格式") or "").strip().lower()
        report["文件名"] = (
            f"{basename}.{report_format}" if report_format else f"{basename}文件"
        )
        projected.append(report)
    return projected


def _project_delivery_notice(value):
    """Keep bounded progress copy without projecting mid-task user requests."""
    if isinstance(value, dict):
        message = _first_text(value.get("message"), value.get("detail"), value.get("notice"))
    else:
        message = _first_text(value)
    message = _ERROR_CODE_RE.sub("", message).strip(
        " \t\r\n,，;；:：|/\\-—_()（）[]【】"
    )
    if re.search(
        r"(?:请|需要|麻烦|需|可以|可|如有|若有|如果有).{0,32}(?:确认|授权|补充|上传|重传|重新提交|重提|重跑|提供|给出|提交|选择|同意)"
        r"|(?:确认|补充|上传|重传).{0,16}(?:后|再).{0,12}(?:提交|重提|重跑)",
        message,
    ) or re.search(
        r"(?:要我|是否需要我|需要我|能否|可否).{0,32}(?:搜|检索|查找|补充|重试|继续).{0,12}(?:吗|？|\?)"
        r"|(?:先暂停|暂停处理|等你|等待你|等你回复|等你确认)",
        message,
    ):
        return "任务由系统继续处理，不需要你额外确认、补充材料或重新提交"
    return message if any(char.isalnum() for char in message) else None


def _project_limitations(value, limit=30):
    lane_labels = {
        "official_policy": "政策原文",
        "official_interpretation": "官方解读",
        "industry_research": "行业研究",
        "mainstream_media": "主流媒体",
        "peer_disclosure": "同业公开披露",
        "company_disclosure": "机构公开披露",
        "academic_research": "学术研究",
    }

    def naturalize(text):
        text = _first_text(text)
        if not text:
            return ""
        for lane, label in lane_labels.items():
            if lane in text:
                return f"{label}来源覆盖仍有限"
        if re.search(r"\b[A-Za-z][A-Za-z0-9-]*_[A-Za-z0-9_-]+\b", text):
            return "相关来源覆盖仍有限"
        text = re.sub(r"资料线\s*", "相关来源", text).strip()
        text = re.sub(r"无已验证证据覆盖", "覆盖仍有限", text).strip()
        return text

    projected = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            lane_label = lane_labels.get(str(item.get("lane") or "").strip())
            text = (
                f"{lane_label}来源覆盖仍有限"
                if lane_label
                else naturalize(_first_text(
                    item.get("description"), item.get("message"), item.get("title")
                ))
            )
        else:
            text = naturalize(str(item) if item is not None else "")
        if text and text not in projected:
            projected.append(text[:240])
        if len(projected) >= limit:
            break
    return projected


def _delivery_error_codes(body, delivery, snapshot):
    """Collect bounded backend error codes without exposing their raw messages."""
    values = [
        body.get("error_code"),
        body.get("errors"),
        body.get("delivery_notice"),
        delivery.get("block_error_code"),
        delivery.get("errors"),
        delivery.get("gate_receipt"),
        delivery.get("orchestrator_gate_receipt"),
        snapshot.get("block_error_code"),
        snapshot.get("errors"),
    ]
    codes = []

    def collect(value, depth=0):
        if depth > 2:
            return
        if isinstance(value, str):
            for code in _ERROR_CODE_RE.findall(value):
                if code not in codes:
                    codes.append(code)
            return
        if isinstance(value, dict):
            for key in (
                "error_code", "error_codes", "errors", "block_error_code",
                "code", "message", "detail",
            ):
                if key in value:
                    collect(value.get(key), depth + 1)
            return
        if isinstance(value, list):
            for item in value[:40]:
                collect(item, depth + 1)

    for value in values:
        collect(value)
    return codes


def _candidate_contract_error_codes(body, delivery, snapshot):
    """Collect structured candidate errors without trusting free-form notices."""
    values = [
        body.get("error_code"),
        body.get("errors"),
        delivery.get("block_error_code"),
        delivery.get("errors"),
        delivery.get("gate_receipt"),
        delivery.get("orchestrator_gate_receipt"),
        snapshot.get("block_error_code"),
        snapshot.get("errors"),
    ]
    codes = []

    def collect(value, depth=0):
        if depth > 2:
            return
        if isinstance(value, str):
            for code in _ERROR_CODE_RE.findall(value):
                if code not in codes:
                    codes.append(code)
        elif isinstance(value, dict):
            for key in (
                "error_code", "error_codes", "errors", "block_error_code",
                "code",
            ):
                if key in value:
                    collect(value.get(key), depth + 1)
        elif isinstance(value, list):
            for item in value[:40]:
                collect(item, depth + 1)

    for value in values:
        collect(value)
    return codes


_REPORT_PACKAGE_FIELDS = frozenset({
    "report_id", "version", "gate_receipt_id", "request_id", "run_id",
    "deep_dive_report", "body_char_count", "appendix_char_count",
    "report_profile", "target_length", "min_length", "max_length",
    "length_policy", "cover_info", "executive_summary", "toc",
    "report_body", "appendix_a_source_list", "appendix_b_evidence_matrix",
    "appendix_c_material_coverage", "material_usage_ledger",
    "material_input_count", "material_usage_status", "evidence_ledger",
    "key_conclusions", "impacts", "actions", "requirement_ledger",
    "usefulness_review", "report_quality_diagnostics",
    "human_review_required", "human_review_notes", "docx_artifact",
    "pdf_artifact", "pptx_artifact", "artifacts", "document_style_spec",
    "template_version", "export_status", "delivery_status",
})
_KEY_CONCLUSION_FIELDS = frozenset({
    "conclusion_id", "text", "kind", "criticality",
    "supporting_policy_fact_ids", "supporting_company_fact_ids",
    "supporting_evidence_ids", "counter_evidence_ids", "assumptions",
    "confidence", "report_locations", "verification_status",
    "inference_chain",
})
_DOCUMENT_STYLE_FIELDS = frozenset({
    "word_pdf_body_font", "word_pdf_body_size_pt",
    "word_pdf_line_spacing_rule", "word_pdf_line_spacing_pt",
    "title_styles_enabled", "auto_toc", "clickable_links", "ppt_font",
    "ppt_body_min_size_pt", "ppt_slide_count_min", "ppt_slide_count_max",
})
_REPORT_PACKAGE_DIGEST_EXCLUDED_FIELDS = frozenset({
    "docx_artifact", "pdf_artifact", "pptx_artifact", "artifacts",
    "export_status",
})
_LEGACY_REFERENCE_CANDIDATE_ERRORS = frozenset({
    "E_REPORT_USEFULNESS_AUDIT_INVALID",
    "E_REPORT_USEFULNESS_INSUFFICIENT",
    "E_SOURCE_ASSURANCE_LIMITED",
    "E_THEMATIC_EVIDENCE_ASSURANCE_LIMITED",
})
_V3_ONLY_CANDIDATE_KEYS = frozenset({
    "candidate_authorization_origin",
    "content_degradation_applied",
    "content_degradation_error_codes",
    "content_degradation_mode",
    "content_degradation_safe_evidence_ids",
    "content_degradation_safe_evidence_count",
    "content_degradation_unsafe_reference_excluded_count",
    "content_degradation_report_sha256",
    "content_degradation_body_char_count",
    "content_degradation_length_policy",
    "content_degradation_action_matrix_mode",
    "content_degradation_hard_scan_pass",
    "content_degradation_hard_scan_error_codes",
    "content_degradation_human_projection_pass",
    "content_degradation_human_projection_body_char_count",
    "content_degradation_human_projection_error",
})

# Keep the v3 length proof aligned with the backend's substantive-body
# projection.  Raw URLs and generated source appendices do not count toward
# report length; Markdown link labels do.  A byte-for-byte copy of these
# deterministic patterns is preferable to comparing raw strings and rejecting
# a valid report merely because it contains source links.
_QUALITY_FRONT_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|第?[一二三四五六七八九十百0-9]+[章节、.．]\s*)?"
    r"(?:内容摘要|执行摘要|摘要|目录)\s*[:：]?\s*$"
)
_QUALITY_TAIL_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|第?[一二三四五六七八九十百0-9]+[章节、.．]\s*)?"
    r"(?:附录(?:\s*[A-Za-z一二三四五六七八九十0-9]+)?\s*[:：\-—]?\s*)?"
    r"(?:参考资料(?:清单)?|主要来源(?:清单)?|来源(?:与链接)?清单|"
    r"证据(?:与参考资料)?清单|证据索引|"
    r"材料(?:解析)?覆盖(?:清单)?|材料使用(?:清单|台账))\s*$"
)
_QUALITY_ANY_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+\S|第[一二三四五六七八九十百0-9]+[章节]\s*\S|"
    r"[一二三四五六七八九十百]+、\s*\S|\d+(?:\.\d+)*[.、．]\s*\S)"
)
_QUALITY_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]\n]{1,200})\]\(https?://[^\s<>\[\]{}()（）]+\)",
    re.IGNORECASE,
)
_QUALITY_URL_RE = re.compile(
    r"https?://[^\s<>\[\]{}()（）「」『』【】“”‘’，。；！？、：]+",
    re.IGNORECASE,
)


def _project_report_quality_body(report_text):
    """Mirror the backend's quality-body projection for v3 proof checks."""
    lines = str(report_text or "").splitlines()
    tail_index = next(
        (
            index for index, line in enumerate(lines)
            if _QUALITY_TAIL_HEADING_RE.fullmatch(line)
        ),
        len(lines),
    )
    lines = lines[:tail_index]
    body_start = None
    first_front = next(
        (
            index for index, line in enumerate(lines)
            if _QUALITY_FRONT_HEADING_RE.fullmatch(line)
        ),
        None,
    )
    if first_front is not None:
        prior_nonempty = [line for line in lines[:first_front] if line.strip()]
        front_is_leading = (
            not prior_nonempty
            or (
                len(prior_nonempty) == 1
                and re.match(r"^\s*#{1,2}\s+\S", prior_nonempty[0])
            )
        )
        if front_is_leading:
            body_start = next(
                (
                    index for index in range(first_front + 1, len(lines))
                    if _QUALITY_ANY_HEADING_RE.match(lines[index])
                    and not _QUALITY_FRONT_HEADING_RE.fullmatch(lines[index])
                ),
                None,
            )
    if body_start is not None:
        lines = lines[body_start:]
    projected = "\n".join(lines)
    projected = _QUALITY_MARKDOWN_LINK_RE.sub(
        lambda match: match.group(1), projected
    )
    projected = _QUALITY_URL_RE.sub("", projected)
    return projected.strip()


def _managed_safe_reference_metrics_are_valid(metrics, package):
    """Bind the service-owned v3 safe-source subset to the canonical package."""
    if not isinstance(metrics, dict) or not isinstance(package, dict):
        return False
    safe_ids = metrics.get("content_degradation_safe_evidence_ids")
    safe_count = metrics.get("content_degradation_safe_evidence_count")
    excluded_count = metrics.get(
        "content_degradation_unsafe_reference_excluded_count"
    )
    evidence_ledger = package.get("evidence_ledger")
    if not isinstance(evidence_ledger, list) or not evidence_ledger:
        return False
    package_evidence_ids = []
    for item in evidence_ledger:
        if not isinstance(item, dict):
            return False
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            return False
        package_evidence_ids.append(evidence_id)
    return bool(
        isinstance(safe_ids, list)
        and safe_ids
        and all(isinstance(value, str) and value.strip() for value in safe_ids)
        and safe_ids == sorted(set(safe_ids))
        and isinstance(safe_count, int)
        and not isinstance(safe_count, bool)
        and safe_count == len(safe_ids)
        and isinstance(excluded_count, int)
        and not isinstance(excluded_count, bool)
        and excluded_count >= 0
        and len(package_evidence_ids) == len(set(package_evidence_ids))
        and set(safe_ids) == set(package_evidence_ids)
    )


def _report_package_semantic_sha256(package):
    """Recompute the backend's exact artifact-free semantic package digest."""
    if not isinstance(package, dict) or set(package) != _REPORT_PACKAGE_FIELDS:
        return ""
    conclusions = package.get("key_conclusions")
    if not isinstance(conclusions, list) or any(
        not isinstance(item, dict)
        or set(item) != _KEY_CONCLUSION_FIELDS
        or not str(item.get("conclusion_id") or "").strip()
        for item in conclusions
    ):
        return ""
    style = package.get("document_style_spec")
    if style is not None and (
        not isinstance(style, dict) or set(style) != _DOCUMENT_STYLE_FIELDS
    ):
        return ""
    if not str(package.get("report_id") or "").strip():
        return ""
    payload = {
        key: value for key, value in package.items()
        if key not in _REPORT_PACKAGE_DIGEST_EXCLUDED_FIELDS
    }
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _candidate_declarations(body, delivery, package):
    containers = (body, delivery, package)
    schemas = {
        str(container.get("reference_candidate_schema") or "").strip()
        for container in containers
        if str(container.get("reference_candidate_schema") or "").strip()
    }
    reasons = {
        str(container.get("reference_candidate_reason") or "").strip()
        for container in containers
        if str(container.get("reference_candidate_reason") or "").strip()
    }
    return schemas, reasons


def _candidate_requires_v3(body, delivery, package, error_codes):
    """Prevent stripped or unknown v3 candidates from falling back to legacy."""
    if not any((
        delivery.get("reference_candidate") is True,
        delivery.get("reference_candidate_eligible") is True,
        delivery.get("has_reference_candidate") is True,
        delivery.get("reference_candidate_ready") is True,
        package.get("delivery_status") == "reference_candidate",
    )):
        return False
    schemas, reasons = _candidate_declarations(body, delivery, package)
    gates = (
        delivery.get("gate_receipt"),
        delivery.get("orchestrator_gate_receipt"),
    )
    metrics = [
        gate.get("gate_metrics")
        for gate in gates
        if isinstance(gate, dict) and isinstance(gate.get("gate_metrics"), dict)
    ]
    v3_shape = any(
        any(key in container for key in _V3_ONLY_CANDIDATE_KEYS)
        for container in (*metrics, body, delivery, package)
        if isinstance(container, dict)
    )
    metric_schemas = {
        str(item.get("reference_candidate_schema") or "").strip()
        for item in metrics
        if str(item.get("reference_candidate_schema") or "").strip()
    }
    metric_reasons = {
        str(item.get("reference_candidate_reason") or "").strip()
        for item in metrics
        if str(item.get("reference_candidate_reason") or "").strip()
    }
    unknown_candidate_error = any(
        code not in _LEGACY_REFERENCE_CANDIDATE_ERRORS
        and not code.endswith("_WARNING")
        for code in error_codes
    )
    return bool(
        "reference-candidate.v3" in schemas
        or "reference-candidate.v3" in metric_schemas
        or "content_quality_limited" in reasons
        or "content_quality_limited" in metric_reasons
        or v3_shape
        or unknown_candidate_error
    )


def _public_projection_is_locally_safe(text):
    """Reject obvious machine/sensitive residue when no public-body hash exists."""
    value = str(text or "")
    if not value.strip():
        return False
    forbidden_patterns = (
        r"\bE_[A-Z0-9_]{3,}\b",
        r"\bE-(?:DOC|WEB|TXT|FILE|POLICY)-[A-Za-z0-9_-]+\b",
        r"\b(?:request_id|run_id|report_id|corpus_id|artifact_id|material_ref|"
        r"evidence_id|fact_id|bundle_sha256|report_sha256)\b\s*[:=：]",
        r"\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=：]",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}",
        r"X-Research-(?:Bridge-Key|Subject)\s*[:=：]",
    )
    return not any(re.search(pattern, value, re.IGNORECASE) for pattern in forbidden_patterns)


def _is_validated_v3_rebuilt_candidate(body, delivery, report_body):
    """Recognize a service-authorized v3 rebuild after every public hard scan.

    Some findings describe the discarded writer surface, not the rebuilt public
    digest.  They may be downgraded only when the backend binds the canonical
    package body and its distinct human projection to identical dual-gate
    metrics, records a service-owned authorization origin, and proves that the
    post-rewrite hard scan and human projection are clean.  This is deliberately
    stricter than a generic ``reference_candidate`` flag.
    """
    gate = delivery.get("gate_receipt")
    orchestrator_gate = delivery.get("orchestrator_gate_receipt")
    package = delivery.get("research_report_package")
    if not all(isinstance(value, dict) for value in (gate, orchestrator_gate, package)):
        return False
    declared_schemas, declared_reasons = _candidate_declarations(
        body, delivery, package
    )
    if (
        declared_schemas != {"reference-candidate.v3"}
        or declared_reasons != {"content_quality_limited"}
    ):
        return False
    metrics = gate.get("gate_metrics")
    orchestrator_metrics = orchestrator_gate.get("gate_metrics")
    if not isinstance(metrics, dict) or metrics != orchestrator_metrics:
        return False
    error_codes = sorted({
        str(code).strip() for code in (gate.get("error_codes") or [])
        if str(code).strip()
    })
    orchestrator_error_codes = sorted({
        str(code).strip() for code in (orchestrator_gate.get("error_codes") or [])
        if str(code).strip()
    })
    package_report_body = str(package.get("report_body") or "")
    canonical_report_body = package_report_body
    canonical_alias = str(package.get("deep_dive_report") or "")
    public_report_body = str(delivery.get("final_report") or "")
    snapshot = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else {}
    snapshot_report_body = str(snapshot.get("report_body") or "")
    if (
        not public_report_body.strip()
        or str(report_body or "") != public_report_body
        or (
            snapshot_report_body.strip()
            and snapshot_report_body != public_report_body
        )
        or not _public_projection_is_locally_safe(public_report_body)
    ):
        return False
    report_sha256 = hashlib.sha256(
        canonical_report_body.encode("utf-8")
    ).hexdigest() if canonical_report_body else ""
    package_sha256 = str(metrics.get("reference_candidate_package_sha256") or "")
    recomputed_package_sha256 = _report_package_semantic_sha256(package)
    degradation_body_char_count = metrics.get(
        "content_degradation_body_char_count"
    )
    human_projection_body_char_count = metrics.get(
        "content_degradation_human_projection_body_char_count"
    )
    package_body_char_count = package.get("body_char_count")
    degradation_length_policy = metrics.get("content_degradation_length_policy")
    degradation_body_char_count_valid = bool(
        isinstance(degradation_body_char_count, int)
        and not isinstance(degradation_body_char_count, bool)
        and degradation_body_char_count >= 1000
        and degradation_body_char_count
        == len(_project_report_quality_body(canonical_report_body))
        and degradation_body_char_count == package_body_char_count
    )
    human_projection_body_char_count_valid = bool(
        isinstance(human_projection_body_char_count, int)
        and not isinstance(human_projection_body_char_count, bool)
        and human_projection_body_char_count >= 1000
        and human_projection_body_char_count
        == len(_project_report_quality_body(public_report_body))
    )
    degradation_length_policy_valid = bool(
        isinstance(degradation_length_policy, dict)
        and degradation_length_policy.get("strict") is False
        and degradation_length_policy == package.get("length_policy")
    )
    allowed_modes = {
        "source_attributed_digest",
        "action_clauses_removed",
        "reference_tail_removed",
        "review_limited",
        "research_only_sections_removed",
        "research_only_conditioned",
        "material_attribution_reference",
    }
    return all((
        delivery.get("reference_candidate") is True,
        delivery.get("reference_candidate_eligible") is True,
        delivery.get("has_reference_candidate") is True,
        delivery.get("reference_candidate_ready") is True,
        delivery.get("verified") is False,
        delivery.get("blocked") is False,
        delivery.get("has_final_report") is True,
        delivery.get("has_report_package") is True,
        delivery.get("human_review_required") is True,
        package.get("delivery_status") == "reference_candidate",
        package.get("human_review_required") is True,
        gate.get("gate_pass") is False,
        orchestrator_gate.get("gate_pass") is False,
        error_codes == orchestrator_error_codes,
        bool(error_codes),
        metrics.get("reference_candidate_schema") == "reference-candidate.v3",
        metrics.get("reference_candidate_reason") == "content_quality_limited",
        metrics.get("reference_candidate_server_recomputed") is True,
        metrics.get("reference_candidate_integrity_gate_passed") is True,
        metrics.get("reference_candidate_hard_gate_passed")
        == (metrics.get("hard_gate_pass") is True),
        metrics.get("reference_candidate_eligible") is True,
        metrics.get("candidate_authorization_origin") in {
            "service_default", "managed_post_corpus",
        },
        metrics.get("eligible_evidence_nonempty") is True,
        _managed_safe_reference_metrics_are_valid(metrics, package),
        metrics.get("content_degradation_applied") is True,
        metrics.get("content_degradation_error_codes") == error_codes,
        metrics.get("content_degradation_mode") in allowed_modes,
        degradation_body_char_count_valid,
        human_projection_body_char_count_valid,
        degradation_length_policy_valid,
        metrics.get("content_degradation_action_matrix_mode") == "research_only",
        metrics.get("content_degradation_hard_scan_pass") is True,
        metrics.get("content_degradation_hard_scan_error_codes") == [],
        metrics.get("content_degradation_human_projection_pass") is True,
        metrics.get("content_degradation_human_projection_error") == "",
        metrics.get("reference_candidate_semantic_gate_passed")
        == metrics.get("semantic_review_pass"),
        not (package.get("actions") or []),
        not (package.get("impacts") or []),
        bool(public_report_body.strip()),
        bool(canonical_report_body.strip()),
        canonical_report_body == canonical_alias,
        bool(re.fullmatch(r"[0-9a-f]{64}", report_sha256)),
        gate.get("report_sha256") == report_sha256,
        orchestrator_gate.get("report_sha256") == report_sha256,
        metrics.get("content_degradation_report_sha256") == report_sha256,
        bool(re.fullmatch(r"[0-9a-f]{64}", package_sha256)),
        package_sha256 == recomputed_package_sha256,
    ))


def _canonical_hard_error_codes(body, delivery, snapshot, fallback_error_codes):
    """Read hard-stop codes from canonical gate/single-code fields only.

    A rebuilt candidate may retain warnings about discarded or excluded input
    surfaces in broad ``errors`` arrays.  Those warnings are not a gate receipt
    and must not override a service-authorized clean human projection.
    """
    values = [
        body.get("error_code"),
        delivery.get("block_error_code"),
        snapshot.get("block_error_code"),
    ]
    for gate_name in ("gate_receipt", "orchestrator_gate_receipt"):
        gate = delivery.get(gate_name)
        if isinstance(gate, dict):
            values.extend((
                gate.get("error_code"),
                gate.get("error_codes"),
                gate.get("block_error_code"),
            ))
    codes = []

    def collect(value):
        if isinstance(value, str):
            for code in _ERROR_CODE_RE.findall(value):
                if code not in codes:
                    codes.append(code)
        elif isinstance(value, list):
            for item in value[:40]:
                collect(item)

    for value in values:
        collect(value)
    package = delivery.get("research_report_package")
    package = package if isinstance(package, dict) else {}
    candidate_error_codes = _candidate_contract_error_codes(
        body, delivery, snapshot
    )
    if not codes and not _candidate_requires_v3(
        body, delivery, package, candidate_error_codes
    ):
        return [
            code for code in fallback_error_codes
            if not str(code).endswith("_WARNING")
        ]
    return codes


def _has_explicit_output_block(body, delivery, snapshot, error_codes, report_body):
    """Keep true safety/binding failures closed even when stale artifacts exist."""
    containers = (body, delivery, snapshot)
    if any(
        container.get("output_permitted") is False
        or container.get("evidence_output_permitted") is False
        or container.get("output_prohibited") is True
        for container in containers
    ):
        return True
    hard_markers = (
        "OUTPUT_PROHIBITED",
        "OUTPUT_NOT_ALLOWED",
        "NO_OUTPUT",
        "UNAUTHORIZED_OUTPUT",
        "EVIDENCE_PROHIBITED",
        "EVIDENCE_OUTPUT_PROHIBITED",
        "NO_ELIGIBLE",
        "ELIGIBLE_EVIDENCE_EMPTY",
        "NO_FULLTEXT",
        "NO_READABLE_CORE",
        "PROHIBITED_EVIDENCE",
        "PERMISSION_UNKNOWN",
        "PERMISSION_REQUIRED",
        "PERMISSION_MIXED",
        "CROSS_RUN",
        "CROSS_TASK",
        "RUN_MISMATCH",
        "TASK_MISMATCH",
        "IDENTITY_BINDING",
        "CORPUS_IDENTITY_MISMATCH",
        "RUN_CONTEXT_CONFLICT",
        "ALL_MATERIALS_UNREADABLE",
        "NO_READABLE_MATERIALS",
        "MACHINE_FIELD",
        "INTERNAL_FIELD",
        "SENSITIVE",
        "SECRET_LEAK",
        "SCHEMA_INVALID",
        "TYPE_CONTRACT",
        "DANGLING_EVIDENCE_REF",
        "REFERENCE_DANGLING",
        "CITATION_INTEGRITY",
        "REFERENCE_INTEGRITY",
        "CORPUS_NOT_SEALED",
        "CORPUS_STATUS_MISSING",
        "CORPUS_STATUS_UNKNOWN",
        "CORPUS_MODIFIED_AFTER_SEAL",
        "CORPUS_CRITICAL_GAP_COUNT_MISMATCH",
        "DUPLICATE_REPORT_WRITE",
        "DUPLICATE_CORPUS_BUILD",
        "DUPLICATE_FINAL_ASSEMBLY",
        "DEDUP_CONFLICT",
        "LIVE_ADAPTER_CONTRACT",
        "MATERIAL_USAGE_CONTRACT_INVALID",
        "EVIDENCE_INDEX_DANGLING",
        "SOURCE_POLICY_VIOLATION",
        "REPORT_LENGTH_POLICY_INVALID",
        "REPORT_BODY_PACKAGE_MISMATCH",
        "GATE_CALCULATOR_MISSING",
        "PACKAGE_INTEGRITY",
        "PACKAGE_CORRUPT",
        "PACKAGE_INVALID",
        "ARTIFACT_CORRUPT",
        "ARTIFACT_INVALID",
        "HASH_MISMATCH",
    )
    rebuilt_candidate = _is_validated_v3_rebuilt_candidate(
        body, delivery, report_body
    )
    rebuilt_markers = {
        "REFERENCE_INTEGRITY",
        "MACHINE_FIELD",
        "SENSITIVE",
        "SCHEMA_INVALID",
        "TYPE_CONTRACT",
        "DANGLING_EVIDENCE_REF",
    }
    hard_error_codes = _canonical_hard_error_codes(
        body, delivery, snapshot, error_codes
    )
    for code in hard_error_codes:
        for marker in hard_markers:
            if marker not in code:
                continue
            if marker in rebuilt_markers and rebuilt_candidate:
                continue
            return True
    return False


def _project_quality_limitations(error_codes):
    """Turn content-quality gates into transparent post-delivery review notes."""
    rules = (
        (
            ("IMPACT_ANALYSIS", "IMPACT_INCOMPLETE"),
            "部分影响分析未通过完整性复核，交付时已降级为待人工复核内容。",
        ),
        (
            ("ACTION_FEASIBILITY", "ACTIONABILITY", "ACTION_"),
            "部分行动建议未通过可行性复核，交付时已降级为待人工复核内容，不应直接执行。",
        ),
        (
            (
                "TEMPORAL",
                "TIMELINE",
                "EFFECTIVE_DATE",
                "UNRESOLVED_POLICY_STATUS",
            ),
            "部分日期、效力状态或先后关系仍需人工复核，未核验内容不应作为确定时序使用。",
        ),
        (
            (
                "FACT_GATE",
                "FACTUAL",
                "FACT_",
                "EVIDENCE_GAP",
                "REFERENCE_INTEGRITY",
                "PRIMARY_EVIDENCE_EMPTY",
                "UNRESOLVED_CONFLICT",
                "UNGROUNDED_NUMBER",
                "POLICY_IDENTITY_CONFLICT",
            ),
            "部分事实、数字或政策效力依据仍需人工复核，未充分核验内容不应作为确定事实使用。",
        ),
        (
            ("SEMANTIC", "CONSISTENCY"),
            "部分表述的语义或前后一致性仍需人工复核。",
        ),
        (
            ("REPORT_USEFULNESS", "REQUIREMENT_COVERAGE", "REPORT_INCOMPLETE"),
            "报告的需求覆盖或实用性仍有不足，建议结合具体使用场景人工复核后采用。",
        ),
        (
            (
                "SOURCE_ASSURANCE",
                "THEMATIC_EVIDENCE_ASSURANCE",
                "SOURCE_COVERAGE",
                "EVIDENCE_COVERAGE",
            ),
            "部分来源类别或交叉核验覆盖仍有限，相关结论应结合报告中的引用链接人工复核。",
        ),
    )
    projected = []
    for markers, notice in rules:
        if any(any(marker in code for marker in markers) for code in error_codes):
            projected.append(notice)
    return projected


def _corpus_passed(body, delivery, snapshot):
    """Read the canonical corpus result without trusting free-form messages."""
    manifest = delivery.get("corpus_manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    values = (
        delivery.get("corpus_status"),
        manifest.get("status"),
        body.get("corpus_status"),
        snapshot.get("corpus_status"),
    )
    return any(str(value or "").strip().upper() == "PASS" for value in values)


def _legacy_content_gate_stage(error_codes):
    """Name old non-delivery content gates without exposing machine codes."""
    impact = any("IMPACT_ANALYSIS" in code for code in error_codes)
    action = any("ACTION_FEASIBILITY" in code for code in error_codes)
    if impact and action:
        return "影响分析与行动建议校验"
    if impact:
        return "影响分析完整性校验"
    if action:
        return "行动建议可行性校验"
    return ""


def _hard_output_block_stage(error_codes):
    """Project the canonical hard-stop family as bounded natural Chinese."""
    rules = (
        (
            (
                "CROSS_RUN", "CROSS_TASK", "RUN_MISMATCH", "TASK_MISMATCH",
                "IDENTITY_BINDING", "CORPUS_IDENTITY_MISMATCH",
                "RUN_CONTEXT_CONFLICT",
            ),
            "任务与报告身份绑定一致性检查",
        ),
        (("MACHINE_FIELD", "INTERNAL_FIELD"), "可公开阅读稿的机器字段清理检查"),
        (("SENSITIVE", "SECRET_LEAK"), "敏感信息安全检查"),
        (
            (
                "OUTPUT_PROHIBITED", "OUTPUT_NOT_ALLOWED", "NO_OUTPUT",
                "UNAUTHORIZED_OUTPUT", "EVIDENCE_PROHIBITED",
                "PERMISSION_UNKNOWN", "PERMISSION_REQUIRED", "PERMISSION_MIXED",
            ),
            "材料输出许可检查",
        ),
        (
            (
                "ALL_MATERIALS_UNREADABLE", "NO_READABLE_MATERIALS",
                "NO_ELIGIBLE", "ELIGIBLE_EVIDENCE_EMPTY", "NO_FULLTEXT",
                "NO_READABLE_CORE", "PROHIBITED_EVIDENCE",
            ),
            "材料可用性检查",
        ),
        (("SCHEMA_INVALID", "TYPE_CONTRACT"), "公开报告结构检查"),
        (
            ("CITATION_INTEGRITY", "REFERENCE_INTEGRITY", "REFERENCE_DANGLING"),
            "引用与报告绑定完整性检查",
        ),
        (
            (
                "CORPUS_NOT_SEALED", "CORPUS_STATUS_", "CORPUS_MODIFIED_AFTER_SEAL",
                "CORPUS_CRITICAL_GAP_COUNT_MISMATCH", "DUPLICATE_", "DEDUP_CONFLICT",
                "LIVE_ADAPTER_CONTRACT", "MATERIAL_USAGE_CONTRACT_INVALID",
                "EVIDENCE_INDEX_DANGLING", "SOURCE_POLICY_VIOLATION",
                "REPORT_LENGTH_POLICY_INVALID", "REPORT_BODY_PACKAGE_MISMATCH",
                "GATE_CALCULATOR_MISSING", "PACKAGE_", "ARTIFACT_", "HASH_MISMATCH",
            ),
            "报告包或任务完整性检查",
        ),
    )
    for markers, stage in rules:
        if any(any(marker in code for marker in markers) for code in error_codes):
            return stage
    return "安全输出检查"


def project_result(body):
    snapshot = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else {}
    delivery = body.get("delivery") if isinstance(body.get("delivery"), dict) else {}
    package = delivery.get("research_report_package") if isinstance(delivery.get("research_report_package"), dict) else {}
    raw_artifacts = body.get("artifacts") if isinstance(body.get("artifacts"), list) else []
    projected_artifacts = [
        projected for projected in (_project_artifact(item) for item in raw_artifacts)
        if projected is not None
    ]
    quality_passed = body.get("quality_passed") is True
    human_review_required = snapshot.get("human_review_required")
    body_char_count = body.get("body_char_count")
    if body_char_count in (None, ""):
        body_char_count = package.get("body_char_count")
    authoritative_body_char_count = safe_int(body_char_count, minimum=0)
    error_codes = _delivery_error_codes(body, delivery, snapshot)
    candidate_error_codes = _candidate_contract_error_codes(
        body, delivery, snapshot
    )
    requires_v3_contract = _candidate_requires_v3(
        body, delivery, package, candidate_error_codes
    )
    report_body = (
        _first_text(delivery.get("final_report"))
        if requires_v3_contract
        else _first_text(
            delivery.get("final_report"),
            snapshot.get("report_body"),
            package.get("report_body"),
            package.get("deep_dive_report"),
        )
    )
    if authoritative_body_char_count == 0 and report_body:
        authoritative_body_char_count = len(report_body)
    safe_ready_artifacts = _safe_ready_report_artifacts(projected_artifacts)
    has_readable_output = bool(report_body or safe_ready_artifacts)
    output_blocked = _has_explicit_output_block(
        body, delivery, snapshot, error_codes, report_body
    )
    projected_gaps = _project_limitations(snapshot.get("coverage_gaps"), 30)
    quality_limitations = _project_quality_limitations(error_codes)
    corpus_passed = _corpus_passed(body, delivery, snapshot)
    verified_report = all((
        quality_passed,
        delivery.get("verified") is True,
        delivery.get("has_final_report") is True,
        delivery.get("has_report_package") is True,
        has_readable_output,
        not output_blocked,
    ))
    validated_reference_candidate = all((
        not quality_passed,
        not verified_report,
        delivery.get("reference_candidate") is True,
        delivery.get("reference_candidate_eligible") is True,
        delivery.get("has_reference_candidate") is True,
        delivery.get("reference_candidate_ready") is True,
        delivery.get("verified") is False,
        delivery.get("blocked") is False,
        delivery.get("has_final_report") is True,
        delivery.get("has_report_package") is True,
        delivery.get("human_review_required") is True,
        package.get("delivery_status") == "reference_candidate",
        package.get("human_review_required") is True,
        (
            not requires_v3_contract
            or _is_validated_v3_rebuilt_candidate(body, delivery, report_body)
        ),
        has_readable_output,
        not output_blocked,
    ))
    reference_candidate = validated_reference_candidate
    deliverable = verified_report or reference_candidate
    if not deliverable:
        # A nominally completed backend response must not leak download handles
        # unless it is either verified or a complete reference candidate.
        artifacts = []
    else:
        artifacts = _humanize_report_artifacts(
            safe_ready_artifacts, reference_candidate=reference_candidate
        )
    if verified_report:
        delivery_label = "正式研究报告"
    elif reference_candidate:
        delivery_label = "可供讨论参考稿"
    else:
        delivery_label = "本次未形成报告文件"
    report_limitations = list(projected_gaps)
    for limitation in quality_limitations:
        if limitation not in report_limitations:
            report_limitations.append(limitation)
    if reference_candidate and not report_limitations:
        report_limitations.append(
            "本报告未通过全部内容质量复核，已按可供讨论参考稿交付，使用前建议人工复核。"
        )
    ready_formats = {
        str(item.get("格式") or "").strip().upper() for item in artifacts
    }
    missing_formats = [
        value.upper() for value in _REPORT_ARTIFACT_CONTRACTS
        if value.upper() not in ready_formats
    ]
    if deliverable and missing_formats:
        if artifacts:
            report_limitations.append(
                "部分报告文件格式未生成；现有可用文件或正文已直接交付，不影响先行阅读。"
            )
        elif report_body:
            report_limitations.append(
                "报告文件未生成；本次已直接交付可阅读正文，无需等待文件补齐。"
            )
    if verified_report and ready_formats == {"DOCX", "PDF"}:
        delivery_notice = "正式研究报告已完成，DOCX 和 PDF 文件均已准备好"
    elif verified_report and artifacts:
        delivery_notice = (
            "正式研究报告已完成，当前已提供"
            + "、".join(sorted(ready_formats))
            + "文件；其他格式未生成不影响先行使用。"
        )
    elif verified_report:
        delivery_notice = "正式研究报告已完成，已直接提供可阅读正文。"
    elif reference_candidate and projected_gaps:
        delivery_notice = (
            "本报告已基于现有材料完成，可供研究和讨论参考。部分结论受权威来源覆盖"
            "限制，使用前建议人工复核。"
        )
    elif reference_candidate:
        delivery_notice = (
            "本报告已基于现有材料完成，可供研究和讨论参考，使用前建议人工复核。"
        )
    elif output_blocked:
        prefix = (
            "公开资料检索与研究语料构建已经通过。"
            if corpus_passed else "本次任务已经结束。"
        )
        delivery_notice = (
            prefix
            + "结果在"
            + _hard_output_block_stage(error_codes)
            + "阶段被阻断，未交付报告文件。你无需补充材料、链接或授权，"
            "也无需缩小范围或改走其他搜索方式；系统不会自动重跑。"
        )
    elif corpus_passed and _legacy_content_gate_stage(error_codes):
        delivery_notice = (
            "公开资料检索与研究语料构建已经通过。本次任务在"
            + _legacy_content_gate_stage(error_codes)
            + "阶段结束，后端未形成可交付的报告文件；并存的来源覆盖提示"
            "不是本次停止原因。你无需补充材料或链接、补做授权、缩小范围，"
            "也无需改走通用搜索；系统不会自动重跑。"
        )
    elif not has_readable_output:
        delivery_notice = (
            "本次任务已经结束，但后端没有返回经过安全校验的可读报告正文或文件。"
            "你无需补充材料、链接或授权，系统不会自动重跑。"
        )
    else:
        delivery_notice = (
            "本次任务已经结束。结果中虽包含正文或文件，但后端未返回显式可信的"
            "正式报告或参考稿合同，因此当前不能安全交付；你无需操作，系统不会自动重跑。"
        )
    projected_improvements = []
    if deliverable and quality_limitations:
        projected_improvements.append(
            "可围绕上述待核验项做人工复核后再形成正式版本；当前参考稿无需等待复核即可阅读。"
        )
    action_review_required = any(
        any(marker in code for marker in ("ACTION_FEASIBILITY", "ACTIONABILITY", "ACTION_"))
        for code in error_codes
    )
    projected_sources = _project_sources(snapshot.get("source_list")) if deliverable else []
    return success_resp(
        完成类型=str(body.get("status") or ""),
        降级交付=bool(body.get("limited_delivery", False)),
        交付说明=delivery_notice,
        用户无需操作=True,
        质量检查通过=quality_passed,
        可交付=deliverable,
        可供讨论参考=reference_candidate,
        交付状态=delivery_label,
        内容摘要=(
            _first_text(snapshot.get("executive_summary"), snapshot.get("summary"))
            if deliverable else ""
        ),
        报告正文=(report_body if deliverable else ""),
        关键结论=_bounded_list(snapshot.get("key_conclusions"), 30) if deliverable else [],
        影响分析=_bounded_list(snapshot.get("impacts"), 30) if deliverable else [],
        对策建议=(
            [] if action_review_required else _bounded_list(snapshot.get("actions"), 40)
        ) if deliverable else [],
        建议处理说明=(
            "行动建议未通过可行性检查，已从对话摘要中删除；报告文件中的相关建议按待人工复核内容处理，不应直接执行。"
            if deliverable and action_review_required else ""
        ),
        材料采用情况=(
            _bounded_list(
                snapshot.get("material_usage_ledger")
                or package.get("material_usage_ledger"),
                40,
            )
            if deliverable else []
        ),
        报告局限=report_limitations if deliverable else [],
        可选完善方向=projected_improvements,
        需人工复核=True if reference_candidate else human_review_required,
        报告正文字符数=(authoritative_body_char_count if deliverable else 0),
        参考来源=projected_sources,
        报告文件=artifacts,
        _job_id=str(body.get("job_id") or ""),
        _artifact_ids=[item["_artifact_id"] for item in artifacts if item.get("_artifact_id")],
    )


def project_question(body):
    citations = []
    for item in body.get("citations", []) if isinstance(body.get("citations"), list) else []:
        if not isinstance(item, dict):
            continue
        citations.append({
            "来源标题": str(item.get("source_title") or ""),
            "来源等级": str(item.get("source_level") or ""),
            "位置": str(item.get("locator") or ""),
            "链接": str(item.get("url") or ""),
            "原文摘录": str(item.get("quote") or ""),
            "核验状态": str(item.get("verification_status") or ""),
        })
    return success_resp(
        回答状态=str(body.get("answer_status") or ""),
        回答内容=str(body.get("answer") or ""),
        依据=citations,
        不确定性=body.get("uncertainty"),
        限制=body.get("limitations"),
        回答基础=body.get("answer_basis"),
        报告已修改=bool(body.get("report_modified", False)),
        已创建修订版=bool(body.get("revision_created", False)),
    )


def is_https_url(value):
    try:
        parsed = urlparse(str(value))
    except Exception:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)
