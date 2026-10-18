# 验收记录

日期：2026-10-15。范围：Windows 本机单用户版本，Django 5.2.18，Python 3.12.14，SQLite；浏览器为本机 Edge / Playwright。

## 实际执行的检查

以下命令均在项目根目录、项目虚拟环境中运行：

| 命令 | 实际结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe manage.py test --verbosity 1` | 100 tests，1.222s，OK（含取消、整体超时与下一次发送回归） |
| `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | No changes detected |
| `.\.venv\Scripts\python.exe manage.py check` | System check identified no issues |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `.\.venv\Scripts\python.exe -m compileall -q config workbench tests` | 退出码 0 |
| `node scripts/browser_acceptance.cjs` | 完整核心闭环通过，JS errors=[] |
| 实际停止并重启验收服务器，再运行 `node scripts/browser_restart_check.cjs` | 原文、版本与旧选择保留，ZIP 下载成功 |
| `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Port 8765` | 迁移至 0004，首页 HTTP 200，默认 Codex；正式设置页刷新后已连接 |

自动测试使用临时数据库与临时图片目录，并通过专用 TestRunner 强制演示模式；SDK、普通 API 协议和失败状态用替身测试，不调用真实模型。真实 Codex 验收单独执行，详见下方记录。

## 对照需求中的 17 项验收场景

| 序号与要求 | 实际证据 | 结果 |
| --- | --- | --- |
| 1. 无 API 可保存素材、手建原则和选择 | `test_manual_workflow_without_model` | 通过 |
| 2. AI 失败保留原始文字、链接、截图 | `test_failure_preserves_source_and_creates_no_partial_suggestion`、`test_failed_ai_keeps_exact_original_image` | 通过 |
| 3. 无链接正文明确提示且不伪分析 | `test_source_post_and_link_only_label`、`test_url_without_body_refused_before_provider` | 通过 |
| 4. 候选未经确认不采纳 | `test_mock_is_only_a_suggestion_until_confirmation`、`test_explicit_confirmation_required` | 通过 |
| 5. 修订新建版本，旧版本不可覆盖 | `test_revision_keeps_history_and_decision_reference`、`test_versions_are_immutable` | 通过 |
| 6. 旧选择始终引用旧版本 | 领域测试、`test_http_end_to_end_preserves_old_choice_basis`、浏览器闭环与重启检查 | 通过 |
| 7. 对话只引用真实记录和版本 | `test_forged_reference_rolls_back`、`test_validate_rejects_unknown_and_fake_references` | 通过 |
| 8. 没有适用原则可以继续 | `test_no_matching_principle_is_valid` | 通过 |
| 9. 移除错误参考后续生效 | `test_removed_reference_not_sent_again`、`test_reference_removal_is_persisted` | 通过 |
| 10. 观察与期待分开，不覆盖选择理由 | `test_observation_does_not_overwrite_expectation_or_reason`、浏览器闭环 | 通过 |
| 11. 修订建议未经确认不修改原则 | HTTP 闭环测试明确断言生成修订建议后原则 revision 仍为 1，确认后才变为 2；通用确认服务测试 | 通过 |
| 12. 重复确认去重，并发冲突可见 | `test_duplicate_confirmation_is_idempotent`、`test_conflict_is_explicit`、`test_stale_form_keeps_input_with_409`、陈旧上下文与生成期间引用变化回归 | 通过 |
| 13. 错误格式和超时不产生半完成状态 | provider 超时/错误 JSON/深层嵌套测试、AI flow 失败与伪造引用回滚测试 | 通过；远程传输用替身 |
| 14. 素材里的指令不触发越权写入 | `test_prompt_injection_does_not_modify_principles`、`test_prompt_injection_is_data_and_does_not_change_control_structure` | 通过；不代表真实模型永不受诱导，正式写入由后端权限限制 |
| 15. 关闭重启保留已有数据 | 实际停止并重启服务，`artifacts/restart-check.json` | 通过 |
| 16. 导出历史及关联，删除清理引用和附件 | `tests.test_data_admin` 13 项、实际 ZIP 下载与 Python 解包核对 | 通过 |
| 17. Mock 明确标记 | `test_explicit_demo_returns_labeled_sample`、建议/消息浏览器断言、列表与详情演示标记检查 | 通过 |

浏览器实际走通：保存原文 → 演示提炼 → 编辑并采纳 → 新情境检索与对话 → 确认选择 → 补充观察 → 确认修订为 v2 → 旧选择仍引用 v1 且理由不变。

## 浏览器、重启与数据隔离

- 桌面 1440×1000、手机视口 390×844：导航与原则详情可操作，无横向溢出；完成截图人工检查。截图在 `artifacts/home-desktop.png`、`home-mobile.png`、`principle-desktop.png`、`principle-mobile.png`。
- 使用 Tab、Enter 与键盘文本输入，从首页进入素材表单并完成保存；结果 `artifacts/keyboard-check.json`。这是基础键盘路径验证，未覆盖全部页面的完整无障碍。
- 浏览器报告：`artifacts/browser-acceptance.json`。验收库位于 `artifacts/acceptance-data/`，所有素材与经历明确虚构。
- 重启报告：`artifacts/restart-check.json`。`artifacts/acceptance-export.zip` 解包后含 2 个原则版本，Decision 的 `versions=[1]`，原文仍在。本次浏览器素材为文字；原图导出及哈希由自动测试覆盖。
- 正式 `data/` 的素材、原则、版本、情境、消息、选择、观察和建议计数均为 0，附件为空。首页截图 `artifacts/formal-home-desktop.png`。验收资料不会混入正式库。

浏览器脚本仅用于独立验收服务（默认 `http://127.0.0.1:8766`），会创建虚构数据，不应指向正式库。需要额外可用的 Playwright 和 Edge；应用运行本身只需要 Python。运行重启检查前，先完成浏览器闭环并实际停止、重启同一验收库的服务器。

## 未验证项与首版边界

- 没有配置独立 API Key；普通 API / DeepSeek 等具体服务商的连通性、格式兼容、质量和费用未验证。Codex 的真实连接与网页闭环已验证；这不代表所有模型或长期回答质量均通过验证。
- 未验证真实手机、所有页面的完整键盘操作、读屏辅助与大规模数据压力。
- 首版有意不做 OCR、链接正文抓取、自动导入、应用自身的多用户账号系统、公网部署、后台推送。导出已验证，不声称具备自动恢复导入功能。


## Codex 接入：真实验收（2026-10-15）

先验证 SDK 可行，再整合项目。正式使用官方 `openai-codex==0.162.1` 及配套 `openai-codex-cli-bin==0.162.1`；本机 Windows x86_64、Python 3.12.14。首轮验收使用现有 ChatGPT 账号额度、模型 `gpt-5.6-luna`，没有配置或使用独立 API Key，没有用 Mock 冒充真实连接。

### 分层证据

1. **SDK 先行验证**：`artifacts/codex-sdk-probe/live-result.json`。已有登录类型为 ChatGPT；同一 ephemeral 会话真实完成素材提炼和反例分析两轮，34.94 秒，分别收到 256 和 66 条文本 delta；活跃 MCP 为 0、只读 sandbox、隔离目录没有新增文件。没有读取或输出 auth 文件、邮箱和令牌。
2. **真实网页流程**：`scripts/browser_codex_acceptance.cjs`、`artifacts/codex-browser-acceptance.json`。网页输入明确虚构素材 → Django 调用 Codex SDK → 生成“自由探索与家庭责任并重”候选 → 浏览器检查并编辑标题 → 勾选确认 → 保存为候选原则。确认前原则库为空；结果 `is_demo=false`、`provider=codex`。随后在同一情境中完成两轮真实对话，收到 128 和 119 条 delta，第二轮预览中包含 SQLite 已保存的 assistant 和 user 消息；第二轮回答给出了“兴趣触发的简短笔记”反例。
3. **验收脚本的一个限制**：首次真实提炼已成功保存建议并跳转，Playwright 在跳转后读取旧响应体时失败。修复了脚本的流观察方式，接着确认同一份真实建议，未为测试抓包重复调用提炼。因此该次网页提炼未记录精确 delta 条数；两轮情境对话保留了精确计数。原始脚本失败报告保留在 `artifacts/codex-browser-failure.json`，不将它隐藏为一次无中断验收。
4. **重启与导出**：实际停止并重启验收服务，`scripts/browser_codex_restart_check.cjs` 与 `artifacts/codex-restart-check.json` 验证候选原则、原文、两条真实 AI 消息仍在，浏览器 ZIP 导出成功。还从设置页切换 Codex → 普通 API → Codex 并保存，未调用普通 API。
5. **数据库核对**：`artifacts/codex-data-verification.json` 与 `artifacts/codex-acceptance-export.zip`。独立验收库含 1 素材、1 原则、1 版本、3 个真实 Codex 建议、3 条消息；已确认版本保持候选状态且关联原素材。正式库 8 类核心业务记录计数全部为 0。
6. **最终权限核对**：追加只读安全核验后，单独运行 `artifacts/codex-sdk-probe/verify_final_isolation.py`，以真实 SDK 核对所有有效配置、临时 cwd、ephemeral 标记，结果见 `artifacts/codex-final-isolation.json`；此项没有模型推理调用。临时目录在 SDK 关闭后清理。正式子进程也清空普通 API 的密钥环境变量。
7. **界面**：桌面 1440×1000 与手机视口 390×844，无横向溢出、JS errors=[]；修复了 CSS 覆盖 hidden 属性造成的空登录面板。截图为 `codex-settings-desktop.png`、`codex-settings-mobile.png`、`codex-candidate-desktop.png`、`codex-multiturn-desktop.png`、`formal-codex-settings.png`，均在 artifacts 下。

### 对照新增的 10 项要求

| 要求 | 结果与边界 |
| --- | --- |
| 官方 SDK 与 Windows | 官方文档核对、SDK 实际安装、Windows 真调用通过 |
| 复用登录、官方未登录流程 | 现有 ChatGPT 登录真实复用；官方 login_chatgpt URL、等待、取消和超时已实现并有替身单测；未退出当前账号做新账号人工登录 |
| 独立 Provider、多轮与流 | CodexProvider 独立；真实两轮对话与 NDJSON delta 通过；错误分类单测通过 |
| AI 不直接写业务库 | Provider 无 ORM；只收普通字典文本；建议与正式原则分开；浏览器确认边界和单测通过 |
| 最小权限、隔离目录 | read-only、deny_all、全部继承 MCP/敏感工具禁用；有效配置、cwd、ephemeral 核对通过；不是完整对抗性红队测试 |
| SQLite 唯一业务事实源 | 每次独立 ephemeral 会话，以 SQLite 最近消息与当前引用重建；不 resume SDK 历史；移除引用后旧 AI 上下文不回流的单测通过 |
| 清楚的连接和错误状态 | 已连接实际显示；未登录、额度不足/限流、超时、失败及脱敏用替身测试，未故意耗尽真实额度 |
| 未登录可用非 AI 功能 | 自动测试证明浏览和保存不启动 SDK；AI 失败不会删除原始素材 |
| 保留 API 抽象与选择 | Codex / 普通 API / 演示显式切换；网页实际切换通过；普通 API 传输测试通过，具体厂商未实测 |
| 真实网页确认闭环 | 真素材输入（内容明确虚构）、真实 SDK 调用、候选展示、编辑确认、SQLite 保存与重启通过；测试抓包中断如上披露 |

SDK/OAuth 状态不保存在业务数据库；导出不含账户令牌。网页断开时完整建议可能已经保存，界面只提示无法确认返回状态；同一操作号重试可取回已有建议。正式原则仍需要单独确认。


## 默认模型与上下文调整（同日追加）

按用户追加要求，默认模型改为 `gpt-6.1-sol`，推理强度 `high`；迁移 0004 同步原默认设置，正式库实际设置已核对。Codex 不再采用工作台字符截短，而是使用官方运行时的原生模型窗口/压缩配置，没有硬编码或宣称未经验证的窗口数值。普通 API / 演示仍保留字符预算；资料检索数量和近期消息范围不变。

- 82 项自动测试通过（0.790 秒）；新增测试证明大于原 12,000 字符的 Codex 上下文完整保留、普通 API 仍受字符预算限制，SDK turn 明确传入 high。
- `scripts/browser_codex_sol_high.cjs` 真实走通 gpt-6.1-sol/high 的网页素材输入、SDK 提炼、建议展示、确认候选保存；报告 `artifacts/codex-sol-high-acceptance.json`，source=2、suggestion=4、principle=2，全部位于独立测试库。
- 正式设置页的默认模型、推理强度、Codex 已连接、侧栏状态同步和隐藏登录面板已通过浏览器检查；截图 `artifacts/formal-codex-settings.png` 已更新。正式 8 类核心业务记录仍为 0。
- 收尾时强制 OAuth 刷新出现一次真实超时；状态查询改为官方 `account(refresh_token=False)`，令牌刷新交由 SDK 按需处理。改后官方本机状态和真实模型调用通过，错误本身仍明确显示、原文不丢失。
- 未测试最大上下文窗口的极限容量或长期压缩质量；没有据此承诺整个知识库会被发送或完整进入单次上下文。

## 页面与操作简化（同日追加）

首页改为直接聊天；点击发送即保存原话并调用当前模型，同页显示回复、继续追问。保留“仅保存”和正式记录确认。参考/深度/完整上下文等收进“参考与更多操作”，素材 9 个字段默认只展开文字、链接、截图，设置默认仅展开接入方式。此轮无需迁移，继续使用 0004。

- **自动测试**：新增直接聊天 11 项，覆盖一键发送、多轮历史、成功重放不重复调用、流中断不保存半条回复、仅保存/空输入继续回复、过期修订拒绝、显式操作、重复操作号篡改、CSRF，以及页面打开后换提供商拒绝发送。全量 93 项通过；Django check、迁移一致性、Python 编译和两份 JS 语法检查通过。
- **浏览器常用流程**：`scripts/browser_simple_acceptance.cjs` 与 `artifacts/simple-browser-acceptance.json`，在明确演示模式的独立库完成同页两轮、Ctrl+Enter、仅保存再继续、模拟丢失完成事件后重试（原话和回复均不重复）、只填文字保存素材、默认折叠设置、移动导航与 Esc 关闭。初次发现表单 action 按钮覆盖 DOM form.action，修复为读取 HTML attribute 后完整重跑通过。
- **真实 Codex**：新界面实际使用 gpt-6.1-sol / high 调用，case=4、suggestion=5、provider=codex、is_demo=false；完整回复落入 SQLite，刷新后保留，无正式原则或选择自动写入。报告 `artifacts/simple-codex-acceptance.json`。首次脚本在 UI 已完成后用 Playwright response.text 读取网络响应失败；随后核对同一条真实回复，没有重复推理。此条未保存精确 delta 数量，不将其报告成已测得。
- **视觉确认**：桌面 1440×1000、手机视口 390×844，对首页、对话、素材和设置逐页检查，无横向溢出或 JS 错误。修复移动侧栏关闭后残留阴影；最后确认截图 `artifacts/simple-final-*.png`。Impeccable detector 执行一次，仅提示原有 AI 预览状态条的单侧强调边线；该状态提示不属于本次新增聊天界面，保留。
- **数据隔离**：此轮虚构输入仅在 `artifacts/simple-ui-data/`。收尾读取正式库计数：Case=1，其余 7 类核心业务对象=0；已有这一条情境保留，未改写或删除。前面“正式库为空”的描述是先前验收时点，不代表当前状态。
- **限制**：手机为浏览器视口模拟，未进行真机或完整读屏验证；普通 API 厂商、新账号人工 OAuth 的既有限制不变。

## 离开对话后持续卡住的修复（同日追加）

用户实际遇到后续发送一直显示忙碌。只读取本应用进程的调用栈（未读取局部变量或账号令牌），定位到 HTTP 断开后的清理逻辑同步等待 `handle.interrupt()`；该 SDK RPC 没有超时，导致生成锁和聊天锁一直未释放。诊断文件为 `artifacts/stuck-stack.json`。先终止已核实属于本应用的卡住 SDK 子进程恢复服务，再修复代码。

- **回归先复现**：关闭流、超时取消、准备 RPC 截止时间三项新测试在旧实现上失败；修复后通过。新增共 7 项，另覆盖思考期间状态心跳、关闭卡住时仅终止自身子进程、初始化迟到后的关闭，以及 HTTP 流断开后下一次发送成功。全量 100 项通过；Django check、迁移一致性和 Python 编译通过，无数据库结构变更。
- **修复范围**：移除同步等待取消 RPC；统一收尾关闭该次临时 SDK 实例，关闭也有等待上限。登录状态和准备阶段纳入整个请求时间限制，无输出时每秒发状态信号。原话保存、操作去重、完整输出校验和正式记录确认规则不变。
- **真实浏览器验证**：`scripts/browser_codex_cancel.cjs` 在独立 `artifacts/cancel-test-data/` 使用 gpt-6.1-sol/high。第一次发送后收到两个状态事件、尚无 delta 时主动离开；约 **3.852 秒**内本次 SDK 子进程退出。回到同一条已保存对话，点击“继续回复”，**25.804 秒**完成真实回复，收到 **140 个 delta**，刷新后保留。无 JS 错误；报告 `artifacts/codex-cancel-acceptance.json`、截图 `artifacts/codex-cancel-recovered.png`。以上时长是本次测量，不是性能承诺。
- **持久化与正式数据**：独立测试库只有 1 条 Case、1 条 assistant Message、1 条 Suggestion；结果 provider=codex、model=gpt-6.1-sol、is_demo=false，首次中断没有保存半条回复或复制原话。正式服务重启后 HTTP 200，账号显示已连接；正式 3 条 Case 的 revision 仍为 1/2/1，原有 1 条 Message 和 1 条 Suggestion 保留，设置仍为 Codex / gpt-6.1-sol / high。未重新发送用户的原始内容。
- **边界**：针对本次取消阻塞完成单元、HTTP 层和真实 Windows 浏览器验证；未进行长时间压力测试或其他操作系统的进程终止测试。
