# Consumer Finance Policy Research

面向消费金融政策研究的 Agent Skill：把需求对齐、材料授权、异步任务、证据追溯和报告交付组织成可验证的工具流程。

本仓库提供 Skill 指令、12 个 Python 工具、响应校验、离线测试和确定性打包器。研究检索、模型推理与报告生成由独立后端完成；本仓库不包含研究后端或公共在线服务。

## 能力

| 环节 | 实现 |
|---|---|
| 需求与计划 | 合并用户要求、提出研究提纲，支持主题研究及周期报告 |
| 材料接收 | 受控目录、文件类型/大小/唯一性检查，草稿收集与封存 |
| 授权管理 | 区分公开、自有授权与企业授权材料，提交时保留服务端授权上下文 |
| 异步研究 | 基于业务请求生成幂等键；恢复原任务，避免重复提交 |
| 结果校验 | 校验任务绑定、报告包摘要、可交付状态和正文安全投影 |
| 可追溯交付 | 只展示实际采用的来源，分别交付正文、DOCX/PDF 及短时下载入口 |
| 后续问答 | 读取已完成研究的不可变快照，保留依据及不确定性 |

```text
用户需求 / 用户授权材料
          ↓
Agent：需求对齐与工具调度
          ↓
Skill：参数、权限、状态、结果校验
          ↓
自备研究后端：检索 / 分析 / 报告生成
          ↓
校验后的正文、引用与制品 → Agent → 用户
```

## 快速核验：无需账号或研究材料

以下命令在仓库根目录运行，使用 Python 3.12；离线测试只依赖标准库，并替换网络传输，不需要凭证、宿主 SDK 或真实模型。

```bash
python -B source/consumer-finance-policy-research/scripts/test_bridge.py
python -B -m unittest discover -s tests -v
python -B tools/package_skill.py --output dist/consumer-finance-policy-research.skill
```

发布验证：桥接测试 143 项中 142 通过、1 项因 Windows 符号链接权限跳过；打包测试 19 项全部通过。详细范围见 [验证说明](docs/validation.md)。测试数量不代表研究质量或生产环境验收。

## 使用与部署

1. 阅读 [SKILL.md](source/consumer-finance-policy-research/SKILL.md) 和 [配置说明](source/consumer-finance-policy-research/references/readme.md)。
2. 准备符合 [API 契约](source/consumer-finance-policy-research/references/api-spec.md) 的 HTTPS 研究后端。
3. 在提供 `coze_workload_identity.requests` 的兼容宿主中安装 Skill，配置自己的后端地址、域名许可、凭证与受控附件目录。
4. 使用自行构造的示例材料验证附件、身份隔离、任务恢复和下载链路，再接入真实材料。

可直接查看 [Skill 源码目录](source/consumer-finance-policy-research)，或下载 [安装包](dist/consumer-finance-policy-research.skill)。包内包含固定清单中的 20 个文件，含 MIT 许可证；打包器检查路径、潜在凭据、内容一致性，并在解包后执行桥接测试。

公开版没有默认部署地址，不导入本地凭证文件，也不绑定任何原安装的 Skill ID。`RESEARCH_API_BASE_URL` 未配置时会在请求发出前拒绝操作。离线测试中的域名、文件名、正文、时间戳与句柄均为测试数据；测试不会抓取这些网址。

## 项目结构

```text
source/consumer-finance-policy-research/
  SKILL.md          Agent 工作规则与调用顺序
  LICENSE           包内许可证
  references/       API、授权、错误和配置契约
  scripts/          12 个工具、公共桥接层与离线测试
tools/              确定性 .skill 打包器
tests/              指令契约及打包安全测试
docs/               验证范围与公开资料说明
dist/               可复核的安装包
```

## 范围与限制

- 本项目是研究系统的 Skill 接入层，不能在缺少兼容后端时独立生成研究报告。
- 不包含模型权重、训练流程、真实政策研究材料或业务数据库。
- 多人身份与访问控制仍须由后端实施，并在实际宿主中验收；客户端 subject 值不能自行证明用户身份。
- `task_only` / `session_only` 表示使用范围，不承诺自动物理删除。
- 报告修订与跨任务长期知识库不在当前工具范围；问答不会修改原报告。

## 隐私与许可证

仓库仅包含通用源码、契约和合成测试数据，不包含原始对话、研究材料或部署记录。详见 [公开资料与数据处理说明](docs/privacy.md)。提交问题时请使用最小复现和合成数据，不上传真实凭证、材料正文或聊天导出。

[MIT License](LICENSE)。
