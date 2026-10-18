# 个人原则工作台：规格、数据与实现记录

## 2026-10-18：收藏后自动整理增量

用户指出只有保存和聊天，缺乏自动整理。沿用现有收藏、OCR、索引和资料库入口，新增 LibraryOrganization（迁移0009）、持久化整理队列和独立Codex后台实例。新收藏自动生成摘要、带引文的要点、主题和有双方引文的素材关联；原件与个人事实不变。旧素材由用户逐条触发，失败保留原件/上一份结果，可重新整理。

前端新增 SourceEditor 和 Organization，支持直接添加文字、图片/文本附件、来源网址，主题筛选与关联详情；更新聊天定位并附入有效AI整理摘要。修改范围为 library 领域服务、gateway接线、上游既有 library API/data-provider/components、定向测试和文档，没有另建工作台或迁移旧数据。最终结果见 docs/LIBRARY-VALIDATION.md 的2026-10-18记录。

## 新聊天集成（2026-10-16，当前有效）

当前已完成领域服务、共享上下文网关、LibreChat API 与界面接入。本轮最终检查见 docs/LIBRARY-VALIDATION.md：140 项 Python、4 项接口、12 项前端测试通过；实际聊天、资料库桌面/手机操作、停止重试和补丁重放通过，未穷举场景明确列出。需求以 PRODUCT.md 和用户最新确认的“原则优先，相关素材与经历自动补充；读取不等于写入”为准。

旧 Django 工作台和旧正式数据保留。新长期库独立使用 data/librechat-library，不自动迁移、归属分配或导入旧记录。第三方付费 API、发布、旧数据迁移仍按原授权边界另行处理。

### 运行结构与文件边界

| 部分 | 地址或位置 | 职责 |
| --- | --- | --- |
| LibreChat v0.8.8 | 127.0.0.1:3081 | 聊天、分支、附件、资料库弹窗、引用与确认入口 |
| 共享上下文网关 | 127.0.0.1:8768；integrations/librechat/gateway.py | 可信身份校验、一次检索、精确保存命令、可选草稿过滤和轮次快照 |
| Codex bridge | 127.0.0.1:8767；integrations/librechat/bridge.py | 模型协议、流式输出与取消；当前 gpt-6.1-sol / medium |
| 独立 MongoDB | 127.0.0.1:27018 | LibreChat 账号、聊天与附件元数据 |
| 独立长期库 | data/librechat-library/workbench.sqlite3；attachments 子目录 | 素材、原则版本、确认经历、依据快照、原件、解析与索引 |

聊天请求由 LibreChat 经 8768 网关送到 8767 bridge，回答反向流回。网关运行迁移和本机解析/索引工作队列；不把模型提供器适配当作唯一检索入口。LibreChat 服务器提供登录所有者，内部请求另有服务端鉴权；浏览器不能自报其他所有者。

Python 实现集中在 workbench/services/library.py、library_context.py、library_gateway.py、library_processing.py、semantic.py，复用现有 Source、PrincipleVersion、Observation、Suggestion；新增 LibraryEntry 等记录保存所有权、状态与关联。上游接入在 packages/api/src/library、packages/data-provider/src/library.ts、client/src/components/Library 及指定聊天挂载点。integrations/librechat/library.patch 已生成并通过固定基线应用检查，23 个文件内容一致，复现方法见同目录 README。

### 1. 收藏与可检索状态

- [x] “收藏为素材”从服务器读取当前账号的用户消息与附件；自然语言明确收藏复用领域入口。只上传到聊天不等于收藏。
- [x] 原件独立保存；原文、OCR/解析文字、索引分别记录状态。收藏不要求提炼，也不会自动新增原则。
- [x] 本地 RapidOCR 解析图片，FastEmbed / BAAI/bge-small-zh-v1.5 在 CPU 生成语义向量，向量存于 SQLite；普通聊天不调用付费 OCR 或嵌入接口。
- [x] 解析或索引失败保留原件，界面提供状态与重试入口；已解析但索引未就绪的指定资料不冒充已完成自动召回。
- [x] 真实截图收藏、解析 ready、索引 ready 和跨对话召回已有记录：artifacts/library-flow-acceptance.json。
- [x] 原件下载和导出通过真实界面哈希核对，解析失败与无永久轮询有自动测试；删除聊天后的原件下载未做破坏式浏览器组合实测。

### 2. 原则与确认保存

- [x] 用户明确给出保存意图和精确内容时可直接写入，返回程序保存回执；该路径不需要另一次模型提炼。
- [x] 需要模型推断或整理时，同一次回答可以附可编辑草稿；用户确认后保存，忽略不写入。“有道理”等泛泛赞同不当作保存授权。
- [x] 可指定素材与经历后在聊天中请求拟定原则；允许没有合适候选。新建、修订及依据关联由确认入口处理，不用相似度暗中覆盖。
- [x] 修订新增不可覆盖版本，保存当时表述、理由、状态、适用边界及 source_ids / experience_ids 和 evidence 摘录。界面历史版本展示当时依据，当前关联变化不替换旧快照。
- [x] “确认保存”等简短指令只绑定当前分支上一条回答匹配的待确认草稿；每次重新生成使用独立 assistant messageId 的轮次记录。
- [x] 真实拟定草稿 → 明确确认 → 修订新增版本 → 导出版本依据的闭环已有记录；前端确认、忽略、精确移除等 focused tests 12 项通过。
- [x] 草稿确认/忽略由前端测试覆盖，版本查看和重新生成去重通过浏览器验收，幂等/旧修订冲突/分支匹配有后端回归；未穷举所有浏览器组合。

### 3. 日常检索与对话设置

- [x] 默认参考当前已采纳的相关原则，同时允许相关、未关联原则的收藏素材和已确认经历自动补充；不固定每轮携带三类记录，也不把收藏观点当作用户认同。
- [x] 当前实现一次检索最多返回 5 条；有类型数量和字符预算，原则有优先权重，相关反例可以与原则一起出现。无相关记录时正常聊天。
- [x] 支持最多指定 5 条本对话参考、排除引用和建议开关。排除、移除或版本变化后，旧引用衍生的回答不能继续作为隐性资料回流。
- [x] 本轮引用只使用实际发送给模型的记录，保存 ID、类型、修订/版本和摘录快照。模型没有直接写数据库的权限。
- [x] 普通聊天一次有界检索、一次模型回答；可选草稿在同一次回答末尾处理，不串行追加提炼、反例、复盘或标题模型。明确保存命令可直接返回回执。
- [x] 默认每个对话最多一个自动提议，跨对话至少间隔 20 分钟；用户主动要求草稿不受自动提议频次限制。
- [x] 检索失败时普通问题提示按普通聊天回答；明确指定的资料无法读取时返回清楚说明，不调用模型假装分析过。
- [x] 独立合成语义验收 12/12 通过，含原则与反例共存、相关素材、无关问题空召回；这不是对真实大库召回质量的保证。
- [ ] 第三方兼容 API 尚待服务地址/模型及相应授权后真实连接验证；共享规则不能冒充其他供应商已实测。

### 4. 经历、原件与数据操作

- [x] 经历可独立确认保存，不必先建立 Decision；模型举例、假设、素材作者经历不能写成本人事实。相关已确认经历可自动读取，检验或修订仍须确认。
- [x] 原则、素材、经历和回收站分别浏览；主要入口仍是聊天，不恢复复杂多页面工作台。
- [x] 移入回收站要求精确标题确认，支持恢复，不等同于永久删除；实体 ID 统一数字类型，无标题回退 String(id)。
- [x] 新库账号导出覆盖全部记录、原件 data_base64、原则各版本与依据、对话设置、轮次快照和草稿，不受交互列表页数限制。真实导出原件 SHA-256 一致已记录。
- [ ] 旧数据迁移和永久删除均未执行；需要单独预览、备份与明确确认。

### 5. 阶段证据与最终验收

| 证据 | 已证明 | 不能据此宣称 |
| --- | --- | --- |
| artifacts/library-flow-acceptance.json | 7 次真实聊天步骤 pass=true、errors=[]；截图 OCR/索引 ready；新对话混合引用；草稿确认；旧版本与原件导出一致 | 所有资料库按钮、移动端、键盘和故障组合已经验收 |
| artifacts/library-semantic-acceptance.json | 12/12 合成查询通过；OCR 通过；单次检索 3–5 ms，整套 2.07 秒 | 用户真实大库准确率或固定延迟 |
| artifacts/library-lighthouse-final.log | 1 项通过；Mongo 查询附加 250 ms 的隔离测试下，LCP 中位数 3948.016 ms、CLS 0.01523、TBT 96.587 ms，均在预算内 | 模型首字耗时或全部 UI 功能通过 |
| client/src/components/Library/__tests__/Library.spec.tsx | 12 项 focused tests 已通过；本次 data-provider 类型检查及指定范围 lint 通过 | 最终重建后全量类型检查和真实浏览器结果 |

收尾结果已汇总到 docs/LIBRARY-VALIDATION.md，优先于以上阶段性证据表。测试使用隔离或明确虚构资料；不读取其他账号，不向旧正式库导入测试内容。未勾选的组合场景未被冒充全覆盖。

## 旧工作台规格与完成记录（历史：仅描述旧 Django 端）

下文已勾选阶段描述旧 Django 端。旧端 high 设置、JSON 输出和“SQLite 唯一事实源”等表述仅适用于其历史架构。正式数据库已有用户记录，不再按空库处理；所有后续测试继续使用隔离数据。

目标：交付可运行、可持久化、可追溯的完整核心循环。SQLite 是唯一业务事实源；AI 只生成待人工确认的草稿，不直接写正式记录。

## 数据关系

Source 原文/链接/原图与校对文字分离；Principle 是稳定身份，PrincipleVersion 记录不可覆盖的完整内容、状态、理由与来源关联。Case 保留情境、深度和选中引用；Message 保留对话与当次引用快照。Decision 保存用户确认的选择与具体版本；Observation 保存事实自述、解释、未知和期待差异，并独立关联支持/不支持/不确定的版本。Suggestion 单独存 AI 草稿、任务、演示标记与上下文快照。Settings 保存检索数量、字符预算、`ai_provider` 与 `codex_model`；确认的价值取向由对应类型且已采纳的原则表达。

## 公共约定

- 原则字段：title, statement, kind(value/belief/strategy), status(candidate/trial/adopted/retired), when_to_use, conditions, boundaries, questions, tags, reason。
- 所有正式写操作均为 CSRF 保护的 POST；业务服务检查 explicit_confirm 与 operation_key。涉及修改的操作检查 revision，冲突返回 409 并保留输入。
- 事务内先进行条件更新再写关联，SQLite 使用 IMMEDIATE 事务；unique operation_key 防止重复。
- Version ORM 禁止更新，数据库触发器禁止 UPDATE；删除只走明确确认的影响预览流程。
- AI 层只收有限上下文，不访问数据库；返回严格校验的建议，引用必须是发送的真实 ID。
- `UserSettings.ai_provider` 显式选择 `codex`、`api` 或 `demo`，`codex_model` 保存 Codex 模型；环境变量 `AI_PROVIDER`、`CODEX_MODEL`、`CODEX_TIMEOUT` 可覆盖设置。默认 Codex 模型是 `gpt-6.1-sol`，没有自动回退。
- Codex 使用 `openai-codex==0.162.1` 和 bundled CLI，每次使用 `ephemeral` 临时目录与只读 sandbox，`deny_all` 审批；继承的 MCP 和敏感 tool features 逐项关闭并核验。SDK 不恢复持久历史，只接收字典文本。
- 普通 API 使用兼容 Chat Completions 的 JSON 响应；Codex 使用受限 NDJSON delta。两种远程模式都不自动重试，演示模式必须显式选择且不会访问外部服务。
- 来源正文缺失时拒绝提炼；失败不影响此前保存的 Source。
- 聊天由首页或已有对话直接 POST 发送；先存原话、同页显示流式回复。输入框说明发送范围，设置签名与修订号校验仍保留。其他 AI 请求继续通过 GET /ai/preview/<task>/<id>/ 查看上下文，再 POST 确认发送。
- 删除预览展示影响，确认时重算影响签名，变化则重新确认。ZIP 导出 JSON 全表与原始附件。

## 实施阶段与验收

- [x] 1. 领域：先写版本、确认、幂等、冲突、引用与原文保护测试；实现 models、services/principles.py、sources.py、records.py、迁移。验收：无模型可录入与修订，历史版本不变。
- [x] 2. 使用闭环：Case、Message、Decision、Observation；关联、检索、近期关注、到期回看。验收：选择固定版本，观察不覆盖理由，手工引用增删生效。
- [x] 3. AI：provider + validation 独立模块、Codex/普通 API/演示三种显式模式、官方 OAuth 登录管理、任务上下文与建议保存。测试使用假 SDK、传输替身和无网络环境；真实供应商兼容性、费用与回答质量另行验证。
- [x] 4. 页面：模板、表单、渐进详情、保存反馈、空/错/加载状态，以及设置中的 provider 选择、状态刷新和 Codex 登录入口。完整浏览器验收结果由 `docs/VALIDATION.md` 记录；完整读屏和真机测试不在首版范围。
- [x] 5. 导出/删除/文档：完整 ZIP、影响预览、附件清理；Windows 安装启动脚本；迁移 `0004` 兼容旧记录。自动测试与浏览器、重启、导出验收以最终 `docs/VALIDATION.md` 为准。

## 验证重点

重复确认与旧表单并发；无正文 URL；原图损坏或过大；AI 超时和畸形结构；删除后无悬空内部引用；导出旧版本和附件；应用重启后数据仍在。UI 操作验证使用独立验收数据库，旧正式数据库已有用户记录，不按空库处理；不要把浏览器脚本产生的虚构资料指向正式库。

## 文件边界

config/：运行配置；workbench/models.py：数据；workbench/services/：领域、检索、AI 应用、数据管理；workbench/ai/：无数据库依赖的 provider、Codex 会话与校验；workbench/forms.py 和 views/：请求与页面；templates/workbench/、static/workbench/：界面；tests/：领域、HTTP、provider 与验收测试。

Windows 启动默认绑定 `127.0.0.1`。官方 SDK 管理登录凭据，不写入业务 SQLite 或导出包；应用不手动读取 Codex auth 文件；普通 API 的密钥只从本机 `.env` 或环境变量读取。验收过程未查看或写入账户令牌，也未配置独立 API Key。

2026-10-15 追加默认设置：迁移 0004 将原默认 gpt-5.6-luna 更新为 gpt-6.1-sol，新增 codex_reasoning_effort=high；Codex 上下文容量跟随原生窗口，普通 API/演示仍使用 context_chars。新默认与旧记录兼容；设置修订号更新使旧预览失效。
