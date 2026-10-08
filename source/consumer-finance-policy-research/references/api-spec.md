# API 与工具契约

本文记录当前脚本实际使用的请求和返回字段。Skill 是薄适配层：脚本校验后端响应，再投影为中文业务字段；不要把 raw JSON 直接交给 Agent。

## 通用约定

- 基础地址：由部署者通过 `RESEARCH_API_BASE_URL` 显式配置；没有默认服务地址，未配置或非 HTTPS 配置失败关闭。
- 请求头：`X-Research-Bridge-Key`、`X-Research-Subject`、`Cache-Control: no-store`。
- 操作句柄可存在于当前工具上下文，但不向用户展示，也不承担认证作用。
- V1 不注册报告修订工具。

## 1. 创建材料草稿

```http
POST /integrations/research/drafts
```

后端成功体至少包含：

```json
{
  "draft_id": "rdraft_<24hex>",
  "status": "collecting",
  "material_count": 0,
  "materials": []
}
```

脚本返回：`status`、`草稿状态`、`材料数量`、`材料列表`、`_draft_id`。草稿状态只有 `collecting` 和 `sealed`。

## 2. 添加一个材料文件

```http
POST /integrations/research/drafts/{draft_id}/materials
Content-Type: multipart/form-data
```

multipart 必须恰好包含两个字段：

| 字段 | 内容 |
|---|---|
| `file` | 单个文件字节及原始文件名 |
| `permission` | UTF-8 JSON 字符串，结构见下方 |

禁止再发送独立的 `content_use`、`retention_mode` 等 multipart 字段。

```json
{
  "scope": "public",
  "confidentiality": "public",
  "license_basis": "public_web",
  "content_use": "full_analysis",
  "authorized_by": "channel_verified",
  "retention_mode": "task_only"
}
```

约束：单文件不超过 16 MiB；单草稿不超过 10 项；仅 `collecting` 草稿可添加；文件必须位于 `RESEARCH_ATTACHMENT_ROOTS` 允许的根目录内，且不是符号链接。

后端成功体至少包含：

```json
{
  "action": "created",
  "material": {
    "material_ref": "dmat_<24hex>",
    "filename": "policy.pdf",
    "mime_type": "application/pdf",
    "size_bytes": 1048576,
    "permission": {"retention_mode": "task_only"}
  }
}
```

`action` 也可能为 `existing`。`created` 回执的文件名、MIME、精确字节数及核心 permission 字段必须与本次上传一致；`existing` 表示后端已按内容去重，脚本以精确字节数和 SHA-256 绑定本次字节，并要求首次 permission 仍满足已发布的授权/保留合同。此时接受并展示首次保存材料的规范文件名、MIME 与保留方式，不要求它们与本次换名上传一致。脚本返回：`status`、`接收结果`、`文件名`、`文件类型`、`文件大小`、`保留方式`、`_material_ref`。

## 3. 删除材料

```http
DELETE /integrations/research/drafts/{draft_id}/materials/{material_ref}
```

仅 `collecting` 草稿可删除。后端必须返回 `{"deleted": true}`，脚本才返回 `status=success`、`操作结果=已删除`。

## 4. 查询草稿

```http
GET /integrations/research/drafts/{draft_id}
```

后端成功体必须包含 `draft_id`、`status`、`material_count`、`materials`。脚本返回：`status`、`草稿状态`、`材料数量`、`材料列表`、`_draft_id`；材料列表只保留文件名、类型、大小、保留方式和 `_material_ref`。

## 5. 封存草稿

脚本先查询草稿；空草稿返回 `EMPTY_DRAFT`，已封存草稿直接恢复为可提交状态。仅对非空 `collecting` 草稿调用：

```http
POST /integrations/research/drafts/{draft_id}/seal
```

后端必须返回完整草稿且 `status=sealed`。脚本返回草稿业务字段及 `封存结果`。

## 6. 准备用户主动提供的链接或粘贴文字

```http
POST /integrations/research/reference-authorizations/prepare
Content-Type: application/json

{"kind":"url|pasted_text","content":"完整链接或文字"}
```

请求体只能包含 `kind` 和 `content`。本接口只接受用户主动发送并要求作为指定材料使用的 URL 或粘贴文字。政府部门官网 URL 可由服务端自动确认；其他 URL 及粘贴文字返回待确认状态。脚本把随机、已按身份范围绑定的 `authorization_id` 放入内部 `_authorization_context`，并把普通用户可读内容单独放在 `用户可见信息`。下划线字段不会被平台自动隐藏；Agent 不得逐字回显工具结果。

Agent、通用搜索工具或研究后端在公开检索中自行发现的 HTTP(S) 网页不调用本接口或下方确认接口，不创建 `_reference_item`，也不进入 `reference_items`。用户对“搜索权威公开资料”的同意属于研究范围确认；完整任务直接交给研究后端。付费、登录、内网、临时签名或其他受限页面不得由 Agent 自行确认成公开材料。

粘贴文字在 Skill 侧单项最多 20000 个 UTF-8 字节，同一次提交的粘贴文字合计最多 40000 个 UTF-8 字节。超过时失败关闭，并要求改用文件上传；V1 不提供大段粘贴正文的独立持久状态。

## 7. 确认链接或粘贴文字

```http
POST /integrations/research/reference-authorizations/{authorization_id}/confirm
Content-Type: application/json

{"choice":"public_web|user_owned|licensed_enterprise|exclude"}
```

请求参数只能来自前一步内部上下文和用户的明确选择。除 `exclude` 外，成功后脚本返回内部 `_reference_item`：

```json
{"authorization_id":"rauth_<opaque>","kind":"url|pasted_text","content":"原材料"}
```

该结构不含权限字段；服务端在任务提交时重新核验内容哈希、所有者、时效和使用次数，再生成真实权限对象。

## 8. 提交研究任务

```http
POST /integrations/research/jobs
Content-Type: application/json
```

请求体示例：

```json
{
  "policy_input": "分析示例消费金融政策对消费金融公司的影响",
  "output_spec": {
    "target_format": "deep_dive_report",
    "report_profile": "standard_report",
    "formats": ["docx", "pdf"]
  },
  "draft_id": "rdraft_<24hex>",
  "reference_items": [
    {
      "authorization_id": "rauth_<opaque>",
      "kind": "url",
      "content": "https://www.nfra.gov.cn/..."
    }
  ],
  "idempotency_key": "idp-<40hex>"
}
```

字段规则：

- `policy_input`：必填，去除首尾空白后 1—20000 个字符。月报、周报及其他周期报告必须保留用户原话中的报告类型、明确日期区间或相对周期和关注范围；零上传是合法请求，脚本不要求 `draft_id` 或 `reference_items`，研究后端负责自主检索公开证据。
- `output_spec`：必填对象；脚本允许调用方省略，并生成不固定字数的 `standard_report` 默认对象。`hotspot_brief`、`standard_report`、`deep_dive_report` 是任务复杂度对应的建议篇幅档位，不是凑字数门槛；对应的自然预估区间分别为 2500—5000、5000—9000、8000—15000 个正文字符。计划可向用户展示所选区间，但默认不传 `target_length`。只有用户明确接受具体目标或另行提出目标篇幅时才传 `target_length`，明确提出硬性上下限时才成对传 `min_length` 与 `max_length`。`formats` 只能包含 `docx`、`pdf`。
- `draft_id`：可选；有附件时必须是已封存且非空的稳定草稿句柄。
- `reference_items`：可选数组；只能原样聚合准备/确认工具返回的 `_reference_item`。文件、链接和粘贴文字合计最多 10 项，粘贴文字正文合计最多 40000 个 UTF-8 字节，同一链接/文字不可重复。Skill 不接受 Agent 自行拼装 `source_urls`、`text_blocks`、`material_seeds` 或权限字典。提交脚本在发送前按 `(kind, content)` 稳定排序，因此同一材料集合不会因收集顺序不同而改变 POST 请求体。
- `idempotency_key`：必填，16—200 字符，位于 JSON 请求体。脚本根据完整、规范化且已稳定排序的业务请求生成；随机 `authorization_id` 不进入业务指纹，`operation_id` 变化不会产生新键。复用原 `_reference_item` 可恢复原任务；同一内容重新 prepare 后若换成新授权句柄，后端以 `E_IDEMPOTENCY_KEY_REUSED` 安全拒绝，不创建第二个任务，调用方应使用错误结果中经校验后返回的 `_job_id` 继续查询原任务（后端未返回合法句柄时不投影该字段）。
- `operation_id` 不直接发送给后端，仅作为兼容性调用标识并保持格式校验；它不参与幂等键。
- `rerun_after_job_id` 不进入创建任务的 POST 请求体。仅当已有任务到终态、且用户明确要求重新研究完全相同的请求时才传入上一任务句柄；脚本先通过 GET 核验其归属与终态，再把该句柄加入幂等指纹。同一次显式重跑的所有重试必须复用同一上一任务句柄。
- 禁止把 raw attachment 或临时附件引用直接放入研究任务。

后端返回公开任务状态，真实字段包括：

```json
{
  "job_id": "rjob_<24hex>",
  "status": "queued",
  "progress": {"stage": "queued", "percent": 0},
  "error": null,
  "limited_delivery": false,
  "delivery_notice": null,
  "created_at": 0,
  "started_at": 0,
  "completed_at": 0,
  "updated_at": 0,
  "recovery_required": false,
  "recovery_note": "",
  "status_endpoint": "/integrations/research/jobs/...",
  "result_endpoint": "/integrations/research/jobs/.../result"
}
```

脚本返回：`status`、`提交结果`、`任务状态`、`当前阶段`、`完成比例`、`降级交付`、`交付说明`、`后台恢复处理中`、`用户无需操作`、`立即回复用户`、`本轮应结束=true`、`自动跨轮推送=false`、可选 `建议等待秒`、`_job_id`、`_idempotency_key`。提交成功后 Agent 必须立即回执并结束当前回合，不得在同一回合持续轮询。宿主未提供可验证的自动跨轮推送能力时；同一会话后续任意消息或“查看进度/上个任务”应从最近一次成功提交工具结果恢复 `_job_id` 并继续查询，不得重复提交或虚构后台通知。

## 9. 查询任务状态

```http
GET /integrations/research/jobs/{job_id}
```

状态枚举：`queued`、`running`、`completed`、`completed_with_gaps`、`failed`。后端字段与提交响应中的公开状态相同；`queued` / `running` 可能附 `Retry-After`。脚本投影字段与提交工具一致，但不含 `提交结果` 和 `_idempotency_key`。

`Retry-After` 只供后续用户回合参考，不授权 Agent 在同一回合 sleep 或循环查询。每个用户回合最多查询一次；运行中则回复进度并结束，终态则立即读取一次最终结果。

## 10. 获取任务结果

```http
GET /integrations/research/jobs/{job_id}/result
```

只有 `completed` 或 `completed_with_gaps` 可取结果；未完成时后端返回 409，并可能附 `Retry-After`。

后端成功体的真实顶层字段为：

```json
{
  "job_id": "rjob_<24hex>",
  "status": "completed_with_gaps",
  "limited_delivery": true,
  "delivery_notice": {"error_code": "E_RESEARCH_EVIDENCE_GAPS", "message": "..."},
  "run_id": "...",
  "delivery_state": "limited",
  "quality_passed": false,
  "delivery": {
    "research_report_package": {
      "body_char_count": 0
    }
  },
  "errors": [],
  "artifacts": [],
  "snapshot": {}
}
```

脚本不会透传 `delivery`、`snapshot`、后台交付枚举或交付错误代码，只返回：

- `完成类型`、`降级交付`、`交付说明`、`用户无需操作`、`质量检查通过`、`可交付`、`可供讨论参考`、自然中文 `交付状态`；
- 安全投影后的 `报告正文`、`内容摘要`、`关键结论`、`影响分析`、`对策建议`、`报告正文字符数`；字符数优先采用后端 `delivery.research_report_package.body_char_count`，脚本仅为兼容旧响应而额外接受同名顶层字段，计数缺失或异常但正文存在时退回正文实际字符数；
- `材料采用情况`、`报告局限`、`可选完善方向`、`需人工复核`、`建议处理说明`、`参考来源`；来源覆盖及行动可行性、事实保障、时序、语义、需求覆盖、有用性等内容质量问题只作为已交付参考稿的透明局限，不投影成用户必须执行的补证动作；行动建议未通过可行性检查时，从对话摘要删除并明确降级为待人工复核内容；
- `报告文件`、`_job_id`、`_artifact_ids`。

`参考来源` 只投影报告实际引用或采用、且允许输出的来源；仅呈现给模型但未进入报告的背景候选不投影。桥接层只消费后端 canonical `snapshot.source_list`。每项的用户侧结构固定为：

```json
{
  "来源类型": "网页|用户文件",
  "标题": "来源标题或完整原始文件名",
  "文件名": "用户文件的完整原始文件名；网页为空",
  "安全显示文本": "标题或文件名经过 HTML 数字字符实体编码后的 Markdown 安全显示文本",
  "Markdown显示行": "Agent 必须逐字照抄的完整 Markdown 行；网页仅含 canonical 原始页面链接，用户文件不可点击",
  "来源等级": "P0|P1|...或空",
  "链接": "网页的完整原始 HTTP(S) URL；用户文件为空",
  "位置或说明": "网页定位信息，或用户提供文件（无公开链接）"
}
```

canonical `source_list` 的后端字段为 `sequence`、`title`、`source_title`、`publisher`、`published_date`、`url`、`url_or_locator`、`source_type`、`source_material_ids`、`locators`；`source_type` 只取 `web` 或 `user_file`。网页缺少合法原始 HTTP(S) URL 时不把 locator 或标题拼成链接；用户文件以 intake ledger 的完整原始文件名为准，永远不生成 URL。桥接层把标题/文件名中的 ASCII Markdown 标点编码为 HTML 数字字符实体，放入 `安全显示文本`，并生成唯一可信的 `Markdown显示行`；Agent 必须逐字照抄该行，不得自行拼接 Markdown。渲染后的文字与完整原名一致。`snapshot.source_list` 是唯一权威的用户参考资料清单；缺失时不回退到 raw evidence ledger 猜链接或文件名，也不把未采用搜索候选投影给用户。网页 `链接` 是后端核验并规范化后的完整原始页面 URL，Agent 不得再次改写；URL 投影额外拒绝 localhost、私网或其他非全局 IP、歧义数字主机、嵌入账号密码和非默认端口。

每个报告文件只保留：`格式`、`文件名`、`文件类型`、`文件大小`、`制品状态`、`保留至`、`错误`、`_artifact_id`。DOCX 与 PDF 各自独立校验；只保留句柄、格式、类型和状态均一致且无歧义的 ready 文件。一个格式失败、等待中或缺失不会隐藏另一个可用文件，也不会清空已通过后端安全投影的正文。

正式报告只接受后端显式可信状态：`quality_passed=true`、`delivery.verified=true`、`has_final_report=true`、`has_report_package=true`，并且安全 `报告正文` 或至少一个 ready 报告文件存在。参考稿只接受后端显式候选合同：内部 `reference_candidate`、`reference_candidate_eligible`、`has_reference_candidate`、`reference_candidate_ready`、`has_final_report`、`has_report_package` 和 `human_review_required` 均为真，`verified=false`、`blocked=false`，报告包自身也标记为需人工复核的参考稿；安全正文或至少一个 ready 报告文件存在即可。桥接层不会从旧状态、聊天话术、正文或文件自行重建可信候选状态。进入任一可信交付状态后，正文是最低交付面，0、1 或多个 ready 文件都不改变已交付结论；缺少某一格式只进入 `报告局限`。

原始 gate 中的引用完整性、机器字段、敏感片段、结构或类型问题默认仍是硬阻断。唯一例外是后端显式 `reference-candidate.v3/content_quality_limited` 合同证明：原 writer 正文已经丢弃或清洗并重建为来源归因摘要；候选全套标志成立；body 顶层、delivery、report package 中出现的 schema/reason 声明必须彼此一致，双层 gate 的错误集和 metrics 也必须完全一致；`candidate_authorization_origin` 必须由服务端填写为 `service_default` 或 `managed_post_corpus`；`content_degradation_safe_evidence_ids` 必须是非空、排序且唯一的字符串列表，声明数量与列表长度一致，不安全引用排除数量必须是非负整数，并且安全证据 ID 集合必须与规范报告包 `evidence_ledger` 的 ID 集合精确相等；重建模式、非严格长度策略、规范报告包正文 SHA-256、报告包语义摘要和字符数绑定一致；`content_degradation_hard_scan_pass=true` 且错误集为空；human projection 也通过且无错误。任何 v3-only 字段、内容降级痕迹或非旧版候选错误码都会强制进入 v3 校验；删除 schema/reason 或双 gate 不能回退为宽松旧候选。桥接层按后端当前 40 字段 `ResearchReportPackage` 和嵌套结论/样式精确重建报告包，排除后挂载的制品引用与 export 状态后，使用同一 canonical JSON 规则本地重算 `report_package_semantic_sha256`；材料账本、证据账本、封面、正文、附录、样式、结论或其他包字段被篡改，都会因摘要不一致而拒绝，不能只凭“看起来像 64 位十六进制”放行。

规范报告包正文与公开阅读投影允许不同：前者保留 E-DOC 等规范证据编号并绑定双 gate 和 degradation 摘要，后者可将编号投影为具名引用。v3 只投影后端 `delivery.final_report`，不把 `snapshot.report_body` 当作正文来源；若 snapshot 同时提供公开正文，则必须与 final report 完全一致。桥接层分别校验规范正文哈希/字符数、公开投影字符数与 clean 凭证，并对公开投影再次执行有界机器字段、旧错误码和敏感凭证扫描；没有公开正文 SHA 时不接受同长度替换来绕过。两份 canonical/public 文本本身不要求相等。v3 放行不是内容质量错误代码白名单；只要来源证明与完整重建合同成立，后端未来新增的内容或模型质量码也可交付安全摘要。来源证明缺失、为未知值或注入值，任一指标缺失、伪造、不一致或扫描失败，均失败关闭；禁止输出、无合格全文、权限不明、跨任务/跨运行绑定和报告包完整性等明确硬码仍由精确硬门拒绝。行动可行性、影响分析、事实保障、时序、语义、需求覆盖、有用性、来源类别、权威层级、来源数量、交叉核验或覆盖度等内容质量问题在后端显式参考稿合同内只进入 `报告局限`；有问题的行动建议从对话摘要删除并通过 `建议处理说明` 标记为待人工复核，不得造成普通零交付。脚本把可交付文件名确定性投影为中文显示名，只保留格式和 `_artifact_id` 供后续下载调用，不暴露后端文件名中的内部英文标记。

禁止输出、全部材料不可读、机器字段或敏感信息、跨任务/跨运行身份绑定、未经批准的引用完整性、报告包或制品完整性等真实硬错误继续失败关闭，并清空正文与下载句柄。其余 `可交付=false` 终态也返回确定性的非空 `交付说明` 和 `用户无需操作=true`。若语料状态已为 `PASS`、旧任务却只以影响分析或行动建议内容门结束，说明会明确“公开资料检索与研究语料构建已经通过”“来源覆盖提示不是本次停止原因”，并明确无需用户补材料、授权、缩小范围或改走通用搜索；桥接层不会自动重跑。Agent 必须逐字转述该说明，不得自行诊断或列路线选择。

## 11. 围绕结果问答

```http
POST /integrations/research/jobs/{job_id}/questions
Content-Type: application/json

{"question": "这项结论的原文依据是什么？", "mode": "question"}
```

`question` 为 1—6000 个字符；仅支持只读问答。后端成功体包含 `job_id`、`job_delivery_status`、`limited_delivery`、`question_id`、`question`，以及：

```json
{
  "answer_status": "answered",
  "answer": "...",
  "citations": [],
  "uncertainty": {"level": "low", "reason": ""},
  "limitations": [],
  "answer_basis": "snapshot_evidence_excerpts",
  "report_modified": false,
  "revision_created": false
}
```

`answer_status` 也可能是 `insufficient_evidence`。脚本返回：`回答状态`、`回答内容`、`依据`、`不确定性`、`限制`、`回答基础`、`报告已修改`、`已创建修订版`。引用只保留来源标题、等级、位置、链接、原文摘录和核验状态。

## 12. 签发制品下载地址

```http
POST /integrations/research/artifacts/{artifact_id}/download-grant
Content-Type: application/json

{"job_id": "rjob_<24hex>"}
```

后端按当前 subject 校验任务与制品归属。成功体真实字段包括：`schema_version`、`grant_id`、`job_id`、`artifact_id`、`url`、`expires_at`、`acl_checked_at`、`cache_control`。

脚本只信任匹配的 `artifact_id` 和 HTTPS `url`，返回：`status`、`下载就绪`、`下载地址`、`有效期至`、`缓存策略`、`_artifact_id`。短时地址可以展示给当前所有者，但不得持久化；不要使用响应中的 `job_id` 作为后续操作句柄。
