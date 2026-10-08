# 材料授权与权限对象

## 原则

权限由已确认的来源事实映射，不能由模型猜测。用户提供材料只代表希望参考，不自动证明公开状态、购买许可或企业授权。

文件上传支持三个确定性权限档案：

| `permission_profile` | 适用材料 |
|---|---|
| `public_web` | 可公开访问且来源已核验的政策、法规、官方解读或其他公开材料 |
| `user_owned` | 用户合法取得、可用于本次研究的自有或个人授权材料，包括合法下载的已购报告 |
| `licensed_enterprise` | 企业订阅、采购或另行授权且许可覆盖本次用途的材料 |

这些值只供脚本调用，不向普通用户展示。旧版复合值只在代码中兼容已有会话，不再作为 CLI 选项。

来源或授权不清楚时先向用户确认；无法确认则不上传。不要索取付费平台账号密码，也不要绕过付费墙。

## 确定性映射

文件上传脚本把权限档案转换为完整 `permission` JSON：

### 公开材料

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

### 用户自有或已授权材料

```json
{
  "scope": "licensed_external",
  "confidentiality": "licensed_external",
  "license_basis": "user_owned",
  "content_use": "full_analysis",
  "authorized_by": "user_explicit",
  "retention_mode": "task_only"
}
```

### 企业许可材料

```json
{
  "scope": "licensed_external",
  "confidentiality": "licensed_external",
  "license_basis": "licensed_enterprise",
  "content_use": "full_analysis",
  "authorized_by": "user_explicit",
  "retention_mode": "task_only"
}
```

## 字段约束

- `content_use`：V1 固定为 `full_analysis`。
- `authorized_by`：只能为 `user_explicit` 或 `channel_verified`。
  - `user_explicit`：用户明确确认材料来源和使用权限。
  - `channel_verified`：可信渠道元数据已经核验来源；不能仅因模型看到了文件名、域名或文字描述就使用。
- `retention_mode`：只能为 `task_only` 或 `session_only`。
  - `task_only`：默认，仅供当前研究任务使用。
  - `session_only`：仅供当前会话范围使用。

两种保留方式都是逻辑使用范围，不承诺自动物理删除，也不授权跨任务长期复用。V1 不提供长期材料库。

## 上传契约

上传一个文件时，multipart 必须恰好只有：

1. `file`：文件字节；
2. `permission`：上述完整对象序列化后的 JSON 字符串。

不要把 `scope`、`content_use`、`authorized_by` 或 `retention_mode` 拆成额外 multipart 字段，后端会拒绝非契约字段。

## 决策流程

1. 识别材料来源，但不凭模型推测授权。
2. 有可信渠道证据时使用 `channel_verified`；否则向用户做最少一次明确确认。
3. 根据公开、用户授权或企业授权选择唯一权限档案。
4. 默认 `task_only`；只有明确需要会话内复用时选择 `session_only`。
5. 上传后以真实回执为准，失败不得假称材料已接收。

## 用户主动提供的网页链接与粘贴文字

本节仅适用于用户主动发送并要求作为指定材料使用的网页链接和粘贴文字。它们不走文件上传权限参数，也不允许 Agent 或模型构造权限对象。固定流程是：

1. `prepare_reference_material` 只发送 `kind` 和 `content`；
2. 政府部门官网由服务端核验域名后自动确认为公开网页；
3. 其他网页和全部粘贴文字只向用户展示四个选择：公开网页、本人合法取得、企业已授权、不使用；
4. `confirm_reference_material` 将用户的单项或精确批次选择映射为服务端固定枚举；
5. 任务提交只携带服务端签发的 `_reference_item`，不携带权限字典；
6. 后端在任务创建时核验身份、内容哈希、有效期和使用范围，并在服务端生成 `permission`。

Agent、通用搜索工具或研究后端自行发现的 HTTP(S) 公开网页属于研究证据候选，不走上述用户材料授权链，不调用 prepare/confirm，不创建 `_reference_item`，也不进入 `reference_items`。用户同意“去搜权威公开资料”只确认当前研究的公开检索范围；Agent 应把完整要求交给研究后端，不得再逐篇询问授权。付费、登录、内网、临时签名或其他受限来源不能因搜索发现而推定为公开；只有用户主动提供并要求使用时，才按本节做最小授权确认。

这里的授权只证明材料可用于本次任务，不证明网页已成功访问或正文已读取。自主检索的页面若无法安全访问，应由研究后端换用其他可访问来源；仍不足时把来源覆盖情况写入报告局限并继续完成参考报告，不得暂停任务要求用户下载上传。只有用户主动指定某一不可替代页面时，才可在最终说明中如实标记该材料未被采用。

`authorization_id` 和 `_reference_item` 只在当前工具上下文流转，不写长期记忆。下划线字段不会被平台自动隐藏，因此 Agent 对外只能转述 `用户可见信息`，不得逐字回显工具 JSON。选择“不使用”时不返回 `_reference_item`，材料不能进入研究任务。

V1 不提供大段粘贴文字的独立持久状态。为避免模型跨轮携带接近后端传输上限的完整正文，Skill 将单项粘贴文字限制为 20000 个 UTF-8 字节，并将同一次提交的粘贴文字总量限制为 40000 个 UTF-8 字节；超过任一上限时要求改用 TXT、DOCX 或 PDF 文件上传，不允许静默截断。

## 文件与渠道边界

- 单文件不超过 16 MiB；每个草稿不超过 10 项。
- 脚本只读取当前 Agent 受控附件根目录中的普通文件，不接受 HTTP(S) URL、目录或符号链接。
- Agent 只传用户上传时看到的原始文件名。解析器只检查受控根的直接子项，要求唯一匹配，不递归扫描系统目录；兼容扣子在物化文件名后添加的固定随机后缀，但后端和用户侧仍保留原始文件名。
- 找不到、同名多项或出现符号链接时失败关闭，且不发网络请求。Agent 不自行列目录或猜路径，只请用户重新上传。
- 临时附件 URL 不持久化；付费材料由用户合法下载后再提交。
