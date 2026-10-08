# 错误模型与重试规则

## 统一错误输出

脚本失败时输出：

```json
{
  "status": "error",
  "错误码": "E_OR_CLIENT_CODE",
  "错误信息": "可向用户解释的消息",
  "HTTP状态码": 409,
  "可重试": true,
  "重试等待秒": 5
}
```

`HTTP状态码`、`可重试`、`重试等待秒` 按场景出现。后端可能把错误放在 FastAPI `detail` 对象、`detail` 字符串或顶层 JSON；共享桥接层会统一提取。

## Skill 侧错误

| 错误码 | 含义 | 建议动作 |
|---|---|---|
| `CONFIG_ERROR` | 基础地址或 Bridge Key 未配置 | 检查环境变量及开发者凭证 |
| `SUBJECT_MISSING` | 缺少 subject | 当前请求失败关闭；检查消费者凭证 |
| `INVALID_HANDLE` | 草稿、材料、任务或制品句柄格式无效 | 使用当前工具上下文中的原句柄 |
| `INVALID_ATTACHMENT` | 文件名、受控目录、扩展名、唯一性或符号链接校验失败 | 停止路径猜测和目录排查；只请用户重新上传文件后再试 |
| `FILE_EMPTY` | 文件为空 | 重新选择文件 |
| `FILE_TOO_LARGE` | 单文件超过 16 MiB | 合法压缩或拆分 |
| `INVALID_PERMISSION` | 权限档案、确认方式或保留方式不合法 | 重新完成授权确认 |
| `INVALID_REFERENCE` | 链接或粘贴文字为空、过长或格式不合法 | 核对原始材料后重新准备 |
| `INVALID_REFERENCE_CONFIRMATION` | 内部授权上下文或用户选择无效 | 复用准备工具的原始上下文，并重新取得明确选择 |
| `EMPTY_DRAFT` | 尝试封存空草稿 | 有文件才封存；无文件直接提交研究 |
| `DRAFT_NOT_COLLECTING` | 草稿状态不允许封存 | 查询草稿；必要时新建草稿 |
| `INVALID_POLICY_INPUT` | 研究要求为空或超过 20000 字符 | 调整为 1—20000 字符 |
| `INVALID_OPERATION_ID` | 启动操作标识不符合规则 | 使用 8—100 位字母、数字、下划线或连字符 |
| `RERUN_SOURCE_NOT_TERMINAL` | 显式重跑引用的上一任务尚未结束 | 继续查询原任务；到终态后再按用户明确要求重跑 |
| `INVALID_JSON` | `output_spec` 不是对象，或数组参数不是合法 JSON | 修正 JSON；不要传自然语言代替对象 |
| `INVALID_QUESTION` | 问题为空或超过 6000 字符 | 缩短问题 |
| `BACKEND_CONTRACT_ERROR` | 2xx 响应缺字段、状态或 HTTPS 地址校验失败 | 不宣称成功；记录操作名并人工检查后端契约 |

## 网络错误

| 错误码 | `可重试` | 说明 |
|---|---:|---|
| `CONNECT_TIMEOUT` | 是 | 5 秒内未连上后端 |
| `READ_TIMEOUT` | 是 | 30 秒内未读到响应 |
| `CONNECTION_ERROR` | 是 | 后端不可达 |
| `TIMEOUT` | 是 | 其他超时 |
| `REQUEST_ERROR` | 否 | 未知请求异常；日志只记异常类型 |

脚本本身只执行一次 HTTP 请求，不内置自动重试。

## 常见后端错误

以下为需专门处理的代表性代码，并非穷举：

| 错误码 | 建议动作 |
|---|---|
| `E_RESEARCH_SUBJECT_REQUIRED` | 缺少可信身份，失败关闭 |
| `E_DRAFT_NOT_FOUND` | 当前草稿不可用，核对上下文或新建草稿 |
| `E_DRAFT_SEALED` | 封存后不能增删；新材料使用新草稿 |
| `E_DRAFT_EMPTY` | 不能提交空草稿；无附件时不要传 `draft_id` |
| `E_IDEMPOTENCY_KEY_REUSED` | 同一业务请求换用了新的临时材料授权；为避免重复付费，后端拒绝创建第二个任务；若错误结果含经格式校验的 `_job_id`，继续查询该原任务，只有用户明确要求重跑时才走显式重跑流程 |
| `E_REFERENCE_AUTHORIZATION_PENDING` | 链接/文字仍需用户确认；调用确认工具，不要自行补权限 |
| `E_REFERENCE_AUTHORIZATION_EXPIRED` | 授权上下文已过期；重新准备材料并请用户确认 |
| `E_REFERENCE_CONTENT_MISMATCH` | 提交内容与准备时不同；停止提交，原样复用 `_reference_item` |
| `E_REFERENCE_AUTHORIZATION_ALREADY_USED` | task-only 授权已绑定其他任务；普通重试应复用原幂等请求，否则重新准备 |
| `E_RESEARCH_JOB_NOT_COMPLETE` | 等待 `Retry-After` 后再查询 |
| `E_RESEARCH_ALREADY_RUNNING` | 任务仍在执行；继续查询，不另起付费任务 |
| `E_RESEARCH_REVISION_NOT_IMPLEMENTED` | V1 只支持问答，不支持生成修订版 |
| `E_ARTIFACT_FORBIDDEN` | 当前 subject 不是制品所有者 |
| `E_ARTIFACT_NOT_READY` | 稍后查询任务或制品状态 |
| `E_ARTIFACT_EXPIRED` | 制品保留期已过，不能继续使用旧地址 |

## HTTP 映射

| HTTP | 处理 |
|---:|---|
| 400 / 401 / 403 / 404 / 409 / 413 / 415 / 422 | 默认不盲目重试；先按业务错误修正 |
| 429 | 按 `重试等待秒` 重试 |
| 500 及以上 | 桥接层标记可重试；有限次重试后转人工检查 |

共享桥接层还会把 `E_RESEARCH_JOB_NOT_COMPLETE` 和 `E_RESEARCH_ALREADY_RUNNING` 标记为可重试。

## 调度规则

1. 查询状态时优先使用返回的 `建议等待秒`；没有时至少等待 10 秒。
2. 不高频轮询，不用新任务替代仍在运行的任务。
3. 提交请求发生可重试错误或用户重复确认时，保持完整业务请求不变；`operation_id` 的变化不会创建第二个任务。
4. 普通提交不得传 `rerun_after_job_id`。只有已有任务到终态且用户明确要求重新研究完全相同的请求时，才传入上一任务句柄；脚本核验归属与终态，该次重试始终复用同一句柄。
5. `BACKEND_CONTRACT_ERROR` 不重试为“成功”，应检查契约或后端状态。

## 日志与展示脱敏

普通用户回复不得展示本页错误码、内部状态、命令、环境变量、本地路径或重试诊断。把错误转换成简短自然说明；例如附件无法唯一识别时只说“暂时没有识别到刚上传的文件，请重新上传一次”，不要把排查过程贴给用户。

禁止写入日志或长期记忆：

- 文件正文和完整用户长文本；
- `X-Research-Bridge-Key`、`X-Research-Subject`、Authorization、Cookie；
- PostgreSQL / Postgres 完整 DSN；
- 临时附件 URL、签名下载 URL；
- 完整异常栈。

短时下载 URL 在所有者校验成功后可以展示给当前用户，但仍不得记录或持久化。操作句柄可在当前工具上下文中流转，不向用户显示。
