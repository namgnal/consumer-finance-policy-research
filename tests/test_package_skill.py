from __future__ import annotations

import importlib.util
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "package_skill.py"
SPEC = importlib.util.spec_from_file_location("package_skill", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
package_skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_skill)


class PackageSkillTests(unittest.TestCase):
    def _copy_source(self, destination: Path) -> Path:
        root = destination / package_skill.SKILL_NAME
        for relative in package_skill.RELEASE_FILES:
            source = package_skill.DEFAULT_SOURCE_ROOT.joinpath(*Path(relative).parts)
            target = root.joinpath(*Path(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return root

    def test_manifest_is_exactly_the_current_20_release_files(self):
        self.assertEqual(len(package_skill.RELEASE_FILES), 20)
        self.assertEqual(len(set(package_skill.RELEASE_FILES)), 20)
        for relative in package_skill.RELEASE_FILES:
            path = package_skill.DEFAULT_SOURCE_ROOT.joinpath(*Path(relative).parts)
            self.assertTrue(path.is_file(), relative)
            self.assertFalse(path.is_symlink(), relative)
            path.read_text(encoding="utf-8")

    def test_skill_preserves_collaborative_planning_and_general_report_contract(self):
        skill_text = (package_skill.DEFAULT_SOURCE_ROOT / "SKILL.md").read_text(
            encoding="utf-8"
        )
        required_contracts = (
            "主动给出拟提纲",
            "1—3 个有价值",
            "不要要求用户从零设计完整报告结构",
            "同一轮最多提出 3 个",
            "接受并开始 / 只修改某一项 / 跳过确认按默认开始 / 先继续聊材料",
            "不能只传最后一次修改",
            "会议只是可能用途",
            "不机械套章",
            "预计正文字符区间",
            "用户可以在确认前直接调长或调短",
        )
        for contract in required_contracts:
            with self.subTest(contract=contract):
                self.assertIn(contract, skill_text)

    def test_skill_keeps_internal_profiles_paths_and_failures_out_of_user_copy(self):
        skill_text = (package_skill.DEFAULT_SOURCE_ROOT / "SKILL.md").read_text(
            encoding="utf-8"
        )
        required_contracts = (
            "用户输出边界（最高优先级）",
            "不得在回复中展示或解释",
            "英文报告档位、权限枚举、内部状态值或后台字段名",
            "本地目录、文件物化路径或排查过程",
            "附件无法唯一识别时只请用户重新上传一次",
            "用户后续查询研究进度、获取或下载结果、针对报告继续问答时也自动使用",
            "上传附件只通过本 Skill 的材料接收流程处理",
            "不得调用通用文件读取、文件转换、文档解析或复制命令",
            "不得使用 `pandoc`、`cp`、解压工具",
            "研究计划直接完整展示在当前对话中",
            "不另建计划文件",
            "计划阶段只登记材料名称和用户说明",
            "材料接收阶段不得执行其他命令",
            "不得使用 `python -c`、here-doc 或导入脚本内部函数绕过 CLI",
            "Agent 不得再次执行同一接收命令",
            "不得把同时存在但并未造成阻断的来源缺口",
            "普通完整报告省略 `output_spec`",
            "不得向用户显示英文档位",
        )
        for contract in required_contracts:
            with self.subTest(contract=contract):
                self.assertIn(contract, skill_text)

        attachment_section = skill_text.split("### 1. 收集附件", 1)[1].split(
            "### 2. 收集网页链接与粘贴文字", 1
        )[0]
        self.assertNotIn("--file-path", attachment_section)
        self.assertIn("这是上传附件的唯一处理路径", attachment_section)
        self.assertIn("只有用户确认完整研究计划", attachment_section)
        self.assertIn("明确要求直接开始、无需确认、按默认开始", attachment_section)
        self.assertIn("并确认材料清单后", attachment_section)
        self.assertIn("授权依据已由用户说明或可信渠道确认", attachment_section)
        self.assertIn("用户确认后的第一轮", attachment_section)
        self.assertIn("不得猜测授权", attachment_section)
        self.assertIn("完整原始文件名清单", attachment_section)
        self.assertIn("只请用户重新上传未接收成功的文件", attachment_section)
        self.assertIn("精确字节数", attachment_section)
        self.assertIn("沿用首次授权和保留方式", attachment_section)

        submit_example = skill_text.split("python scripts/submit_research_job.py", 1)[1].split(
            "```", 1
        )[0]
        self.assertNotIn("--output-spec", submit_example)

    def test_skill_auto_triggers_and_avoids_low_value_plan_questions(self):
        skill_text = (package_skill.DEFAULT_SOURCE_ROOT / "SKILL.md").read_text(
            encoding="utf-8"
        )
        frontmatter = skill_text.split("---", 2)[1]
        trigger_contracts = (
            "普通用户",
            "消费金融政策",
            "相关材料",
            "研究报告",
            "行业或公司影响",
            "可落地建议",
            "自动使用本 Skill",
            "需求对齐",
            "主动给出研究计划",
        )
        for contract in trigger_contracts:
            with self.subTest(frontmatter_contract=contract):
                self.assertIn(contract, frontmatter)

        planning_contracts = (
            "不得重复询问",
            "能安全采用通用默认值",
            "无需澄清",
            "不得断言某项政策已经正式发布",
            "研究阶段核验项",
            "不要把用户输入的文件名称自动当作政策状态事实",
            "不把“是否用于会议”或“是否需要一页纸”作为默认澄清问题",
            "第三方渠道、平台及合作机构应作为业务影响链的一部分纳入研究",
            "而不是作为是否扩展研究范围的问题反问用户",
            "以持牌消费金融公司整体为主对象",
            "不询问用户是否分层",
            "默认覆盖自营渠道、第三方/助贷合作、广告与直播投放、算法触达和消费者权益等完整业务链",
            "不要求用户先选一个营销环节",
            "澄清问题必须为 0",
            "不得把已有安全默认答案的问题包装成“可跳过”的问题",
        )
        for contract in planning_contracts:
            with self.subTest(planning_contract=contract):
                self.assertIn(contract, skill_text)

    def test_agent_discovered_public_web_stays_out_of_user_reference_authorization(self):
        source_root = package_skill.DEFAULT_SOURCE_ROOT
        skill_text = (source_root / "SKILL.md").read_text(encoding="utf-8")
        permission_text = (source_root / "references" / "permission-model.md").read_text(
            encoding="utf-8"
        )
        api_text = (source_root / "references" / "api-spec.md").read_text(
            encoding="utf-8"
        )

        readme_text = (source_root / "references" / "readme.md").read_text(
            encoding="utf-8"
        )

        for contract in (
            "本节只处理用户主动发送、并要求作为指定材料使用的网页链接或粘贴文字",
            "Agent、通用搜索工具或研究后端自行发现的 HTTP(S) 公开网页属于研究证据候选",
            "不得对这些网页调用准备或确认工具",
            "不得创建 `_reference_item`",
            "也不得把它们加入 `reference_items` 后逐篇询问授权",
            "用户回复“去搜吧”“可以”“按公开资料做”等明确同意",
            "应在当前唯一研究任务内由后端自行检索、换源和记录局限",
            "不重新提交任务",
            "只有用户主动提供该链接或正文并要求作为材料使用时",
        ):
            with self.subTest(skill_contract=contract):
                self.assertIn(contract, skill_text)

        for contract in (
            "不走上述用户材料授权链，不调用 prepare/confirm",
            "用户同意“去搜权威公开资料”只确认当前研究的公开检索范围",
            "只有用户主动提供并要求使用时，才按本节做最小授权确认",
        ):
            with self.subTest(permission_contract=contract):
                self.assertIn(contract, permission_text)

        self.assertIn("公开检索中自行发现的 HTTP(S) 网页不调用本接口或下方确认接口", api_text)
        self.assertIn("付费、登录、内网、临时签名或其他受限页面不得由 Agent 自行确认成公开材料", api_text)
        self.assertIn("不走 prepare/confirm，不进入 `reference_items`", readme_text)

    def test_soft_source_gaps_are_post_delivery_limits_not_user_actions(self):
        source_root = package_skill.DEFAULT_SOURCE_ROOT
        skill_text = (source_root / "SKILL.md").read_text(encoding="utf-8")

        for contract in (
            "来源类别、权威层级、来源数量、交叉核验或覆盖度等内容质量问题统一降级为参考稿局限",
            "`报告局限` 和 `可选完善方向` 只用于报告交付后的透明说明",
            "永远不是用户必须执行的补证动作",
            "不得据此追问、等待确认、索要材料或链接、改写材料权限",
            "不得再被解释成普通零交付",
        ):
            with self.subTest(result_contract=contract):
                self.assertIn(contract, skill_text)

    def test_weekly_and_monthly_reports_share_zero_upload_periodic_contract(self):
        source_root = package_skill.DEFAULT_SOURCE_ROOT
        skill_text = (source_root / "SKILL.md").read_text(encoding="utf-8")
        api_text = (source_root / "references" / "api-spec.md").read_text(
            encoding="utf-8"
        )
        readme_text = (source_root / "references" / "readme.md").read_text(
            encoding="utf-8"
        )

        frontmatter = skill_text.split("---", 2)[1]
        for contract in ("月报", "周报", "其他周期报告", "即使用户没有上传材料"):
            with self.subTest(frontmatter_contract=contract):
                self.assertIn(contract, frontmatter)

        for contract in (
            "把月报、周报及其他按明确日期区间生成的报告统一视为“周期报告”",
            "周报不得走比月报更弱的临时流程",
            "零上传是正常入口",
            "不以“证据不足”拒绝",
            "由后端自主检索当期公开政策、数据、公司动态和其他可访问证据",
            "材料围绕同一主题即可，不要求每份材料都对应同一个政策",
            "周报、月报及其他周期报告都必须在最终完成回复中交付报告实际引用或采用的参考资料",
        ):
            with self.subTest(skill_contract=contract):
                self.assertIn(contract, skill_text)

        self.assertIn("零上传是合法请求", api_text)
        self.assertIn("研究后端负责自主检索公开证据", api_text)
        self.assertIn("月报、周报及其他按日期区间生成的报告统一走周期报告流程", readme_text)
        self.assertIn("周报与月报使用相同的托管执行", readme_text)

    def test_completion_lists_traceable_references_after_file_links(self):
        source_root = package_skill.DEFAULT_SOURCE_ROOT
        skill_text = (source_root / "SKILL.md").read_text(encoding="utf-8")
        api_text = (source_root / "references" / "api-spec.md").read_text(
            encoding="utf-8"
        )
        readme_text = (source_root / "references" / "readme.md").read_text(
            encoding="utf-8"
        )

        for contract in (
            "在报告正文和可用文件入口之后，必须继续列出 `参考资料`",
            "只列报告实际引用或采用的来源",
            "后端核验并规范化后的完整原始页面 HTTP(S) URL",
            "不得缩短、改写、根据标题或域名拼接",
            "完整真实的原始文件名",
            "用户提供文件（无公开链接）",
            "HTML 数字字符实体",
            "`安全显示文本`",
            "逐字照抄工具给出的 `Markdown显示行`",
            "即使文件名看起来像 URL 或 Markdown 链接，也不得放入链接语法",
            "不得为它杜撰公开地址",
        ):
            with self.subTest(skill_contract=contract):
                self.assertIn(contract, skill_text)

        for contract in (
            "只投影报告实际引用或采用、且允许输出的来源",
            '"来源类型": "网页|用户文件"',
            '"链接": "网页的完整原始 HTTP(S) URL；用户文件为空"',
            '"安全显示文本": "标题或文件名经过 HTML 数字字符实体编码后的 Markdown 安全显示文本"',
            '"Markdown显示行": "Agent 必须逐字照抄的完整 Markdown 行；网页仅含 canonical 原始页面链接，用户文件不可点击"',
            "用户文件以 intake ledger 的完整原始文件名为准，永远不生成 URL",
            "`snapshot.source_list` 是唯一权威的用户参考资料清单",
            "不回退到 raw evidence ledger 猜链接或文件名",
            "URL 投影额外拒绝 localhost、私网或其他非全局 IP、歧义数字主机",
        ):
            with self.subTest(api_contract=contract):
                self.assertIn(contract, api_text)

        self.assertIn("先给安全正文和所有可用文件入口，再列 `参考资料`", readme_text)
        self.assertIn("编码为 HTML 数字字符实体", readme_text)
        self.assertIn("唯一可信的 `Markdown显示行`", readme_text)
        self.assertIn("文件名即使呈现 URL 或 Markdown 样式，也不能进入链接语法", readme_text)

    def test_confirmed_research_acks_then_resumes_without_same_turn_polling(self):
        skill_text = (package_skill.DEFAULT_SOURCE_ROOT / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for contract in (
            "提交成功后立即使用工具返回的 `立即回复用户` 作简短回执并结束当前 Agent 回合",
            "不得在同一回合 sleep、循环查询或一直等待终态",
            "宿主未提供可验证的自动跨轮推送能力时",
            "不得承诺“完成后自动发给你”",
            "用户之后发送“查看进度”“上个任务”或任何新消息时",
            "每个用户回合最多查询一次",
            "下一次用户消息再使用同一 `_job_id` 查询",
            "不等待逐篇网页授权",
            "自主检索时跳过该来源，换用可访问来源",
            "不得让整个任务停在授权确认上",
        ):
            with self.subTest(managed_execution_contract=contract):
                self.assertIn(contract, skill_text)

    def test_skill_delivers_reference_candidate_without_hiding_other_failures(self):
        skill_text = (package_skill.DEFAULT_SOURCE_ROOT / "SKILL.md").read_text(
            encoding="utf-8"
        )
        required_contracts = (
            "按结果工具返回的中文字段判断交付，不猜测后台状态",
            "按正式研究报告交付",
            "把任务视为已经完成并交付参考稿",
            "正文是最低交付面",
            "0 个文件但正文非空、只有一个 ready 文件或缺少某一格式，都不改变报告已经交付",
            "行动可行性、影响分析、事实保障、时序、语义一致性、需求覆盖、有用性",
            "有问题的行动建议已从对话摘要删除、降级为待人工复核内容或不应直接执行",
            "不得再次否定已完成安全重建的参考稿",
            "不得维护或套用“允许交付的内容质量错误代码白名单”",
            "不得把缺失候选 schema/reason、双 gate 或内容降级凭证的异常结果当成旧版参考稿",
            "不得称为失败，不得为了追求正式报告自动重跑",
            "不得为了追求正式报告自动重跑或再次提交同一任务",
            "`可交付=false` 时，必须逐字转述结果工具返回的完整 `交付说明`",
            "不得删改、概括、补充或另作诊断",
            "不得从 `报告局限`、覆盖缺口、原始状态、错误代码、先前聊天或模型猜测",
            "补材料再研究／缩小范围／换通用搜索",
            "`用户无需操作=true`",
            "不要显示上述字段名、布尔值、后台枚举或错误代码",
            "包含内部英文标记的原始文件名",
            "只使用结果工具给出的安全 `报告正文`、中文显示文件名",
            "遍历结果实际返回的每一个 `报告文件`",
            "报告正文字符数",
            "不要等用户再问“报告在哪”",
            "某个文件签发或发送失败时，保留并交付正文和其他成功文件",
        )
        for contract in required_contracts:
            with self.subTest(contract=contract):
                self.assertIn(contract, skill_text)

        # 普通候选稿只转述工具给出的说明与人工复核状态，不自造固定原因。
        for contract in (
            "先转述结果工具给出的 `交付说明`",
            "`需人工复核=true`",
        ):
            with self.subTest(candidate_baseline_contract=contract):
                self.assertIn(contract, skill_text)

        self.assertIn("来源类别、权威层级、来源数量、交叉核验或覆盖度", skill_text)

        # 非交付终态不得退化成要求用户补材料或另选路线的话术。
        for forbidden in (
            "后续如补充相关权威资料，可进一步增强对应章节",
            "你想走哪条",
            "接下来你有几个选择",
        ):
            with self.subTest(forbidden_non_delivery_copy=forbidden):
                self.assertNotIn(forbidden, skill_text)

    def test_result_projection_has_minimum_body_surface_and_canonical_terminal_copy(self):
        source_root = package_skill.DEFAULT_SOURCE_ROOT
        api_text = (source_root / "references" / "api-spec.md").read_text(
            encoding="utf-8"
        )
        readme_text = (source_root / "references" / "readme.md").read_text(
            encoding="utf-8"
        )

        for contract in (
            "安全投影后的 `报告正文`",
            "一个格式失败、等待中或缺失不会隐藏另一个可用文件，也不会清空已通过后端安全投影的正文",
            "桥接层不会从旧状态、聊天话术、正文或文件自行重建可信候选状态",
            "正文是最低交付面，0、1 或多个 ready 文件都不改变已交付结论",
            "来源覆盖提示不是本次停止原因",
            "Agent 必须逐字转述该说明，不得自行诊断或列路线选择",
            "原 writer 正文已经丢弃或清洗并重建为来源归因摘要",
            "`content_degradation_hard_scan_pass=true` 且错误集为空",
            "`candidate_authorization_origin` 必须由服务端填写为 `service_default` 或 `managed_post_corpus`",
            "`content_degradation_safe_evidence_ids` 必须是非空、排序且唯一的字符串列表",
            "安全证据 ID 集合必须与规范报告包 `evidence_ledger` 的 ID 集合精确相等",
            "规范报告包正文与公开阅读投影允许不同",
            "桥接层分别校验规范正文哈希/字符数、公开投影字符数与 clean 凭证",
            "v3 放行不是内容质量错误代码白名单",
            "删除 schema/reason 或双 gate 不能回退为宽松旧候选",
            "本地重算 `report_package_semantic_sha256`",
            "不把 `snapshot.report_body` 当作正文来源",
            "没有公开正文 SHA 时不接受同长度替换来绕过",
            "任一指标缺失、伪造、不一致或扫描失败，均失败关闭",
        ):
            with self.subTest(api_contract=contract):
                self.assertIn(contract, api_text)

        for contract in (
            "正文是最低交付面",
            "0、1 或多个 ready 文件都不清空正文",
            "桥接层不从旧状态或可疑文件自行重建可信候选",
            "Agent 逐字转述结果工具的 `交付说明`",
            "不得列“补材料、缩小范围、改走通用搜索”等选择",
            "`用户无需操作=true` 时不再向用户提问或等待授权",
            "hard scan clean、human projection clean 完整证明",
            "安全证据 ID 必须以非空、排序、唯一列表绑定数量",
            "与规范报告包 `evidence_ledger` 的 ID 集合精确相等",
            "未知、缺失或注入来源及不完整、伪造 metrics 均不能放行",
            "不要求 canonical/public 文本相等",
            "v3 不维护内容质量错误码白名单",
            "删除 schema/reason 或双 gate 也不能回退旧候选",
            "重算 artifact-free `report_package_semantic_sha256`",
            "v3 只从 `delivery.final_report` 交付公开正文",
            "同长度替换不能绕过",
        ):
            with self.subTest(readme_contract=contract):
                self.assertIn(contract, readme_text)

    def test_v3_projection_requires_service_provenance_without_error_allowlist(self):
        bridge_text = (
            package_skill.DEFAULT_SOURCE_ROOT / "scripts" / "_bridge.py"
        ).read_text(encoding="utf-8")

        for contract in (
            'metrics.get("candidate_authorization_origin")',
            '"service_default", "managed_post_corpus"',
            "== len(_project_report_quality_body(canonical_report_body))",
            "== len(_project_report_quality_body(public_report_body))",
            '"NO_ELIGIBLE"',
            '"NO_FULLTEXT"',
            '"PERMISSION_UNKNOWN"',
            '"UNAUTHORIZED_OUTPUT"',
            '"OUTPUT_NOT_ALLOWED"',
            "_report_package_semantic_sha256(package)",
            "package_sha256 == recomputed_package_sha256",
            "_candidate_requires_v3",
            "_public_projection_is_locally_safe",
            'str(report_body or "") != public_report_body',
            "snapshot_report_body != public_report_body",
            "_managed_safe_reference_metrics_are_valid(metrics, package)",
            "safe_count == len(safe_ids)",
            "set(safe_ids) == set(package_evidence_ids)",
        ):
            with self.subTest(v3_provenance_contract=contract):
                self.assertIn(contract, bridge_text)
        self.assertNotIn("allowed_errors =", bridge_text)

    def test_build_is_deterministic_exact_and_runs_packaged_contract_suite(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.skill"
            second = Path(folder) / "second.skill"
            package_skill.build_skill(output_path=first, run_tests=True)
            package_skill.build_skill(output_path=second, run_tests=True)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first, "r") as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        package_skill.ARCHIVE_ROOT + relative
                        for relative in package_skill.RELEASE_FILES
                    },
                )
                self.assertEqual(len(archive.namelist()), 20)

    def test_forbidden_local_files_and_caches_are_never_packaged(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            source = self._copy_source(temp)
            (source / "scripts" / "_dev_credentials.py").write_text(
                "LOCAL_ONLY = True\n", encoding="utf-8"
            )
            cache = source / "scripts" / "__pycache__"
            cache.mkdir()
            (cache / "_bridge.pyc").write_bytes(b"not bytecode")
            (source / ".env").write_text("LOCAL_ONLY=1\n", encoding="utf-8")
            (source / ".tmp").mkdir()
            (source / ".tmp" / "state.db").write_bytes(b"local")
            output = temp / "clean.skill"
            package_skill.build_skill(
                source_root=source,
                output_path=output,
                run_tests=False,
            )
            with zipfile.ZipFile(output) as archive:
                lowered = "\n".join(archive.namelist()).lower()
            self.assertNotIn("_dev_credentials.py", lowered)
            self.assertNotIn(".env", lowered)
            self.assertNotIn("__pycache__", lowered)
            self.assertNotIn(".tmp", lowered)
            self.assertNotIn(".db", lowered)

    def test_unknown_regular_source_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            source = self._copy_source(temp)
            (source / "scripts" / "forgotten.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(package_skill.PackagingError, "未评审"):
                package_skill.build_skill(
                    source_root=source,
                    output_path=temp / "out.skill",
                    run_tests=False,
                )

    def test_any_source_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            source = self._copy_source(temp)
            link = source / "scripts" / "linked.md"
            link.write_text("simulated link", encoding="utf-8")
            original_is_symlink = Path.is_symlink

            def simulated_is_symlink(path):
                return path == link or original_is_symlink(path)

            with patch.object(Path, "is_symlink", simulated_is_symlink):
                with self.assertRaisesRegex(package_skill.PackagingError, "符号链接"):
                    package_skill.build_skill(
                        source_root=source,
                        output_path=temp / "out.skill",
                        run_tests=False,
                    )

    def test_secret_scanner_ignores_names_and_placeholders_but_catches_values(self):
        package_skill.scan_text_for_secrets(
            "COZE_RESEARCH_BRIDGE_KEY\n"
            "`COZE_RESEARCH_SUBJECT_<credential_id>`\n"
            "COZE_RESEARCH_BRIDGE_KEY = 'replace-me'\n"
            '"COZE_RESEARCH_SUBJECT_demo": "subject-token"\n',
            label="safe-doc-and-test-placeholders",
        )
        fake_secret = "sk-" + ("A" * 32)
        with self.assertRaisesRegex(package_skill.PackagingError, "潜在敏感值"):
            package_skill.scan_text_for_secrets(
                f'BRIDGE_KEY = "{fake_secret}"\n', label="unsafe"
            )

    def test_verify_archive_rejects_tampering_and_archive_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            payloads = package_skill._read_release_files(package_skill.DEFAULT_SOURCE_ROOT)
            output = temp / "bad.skill"
            package_skill._write_archive(payloads, output)
            with zipfile.ZipFile(output, "a") as archive:
                info = zipfile.ZipInfo(
                    package_skill.ARCHIVE_ROOT + "scripts/linked.py",
                    date_time=package_skill.FIXED_ZIP_TIMESTAMP,
                )
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "../SKILL.md")
            with self.assertRaises(package_skill.PackagingError):
                package_skill.verify_archive(output, payloads)

    def test_failed_candidate_preserves_previous_known_good_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "release.skill"
            previous = b"previous-known-good-release"
            destination.write_bytes(previous)
            with patch.object(
                package_skill,
                "run_packaged_contract_tests",
                side_effect=package_skill.PackagingError("injected failure"),
            ):
                with self.assertRaisesRegex(package_skill.PackagingError, "injected"):
                    package_skill.build_skill(
                        output_path=destination,
                        run_tests=True,
                    )
            self.assertEqual(destination.read_bytes(), previous)
            self.assertEqual(
                list(destination.parent.glob(f".{destination.name}.*.candidate")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
