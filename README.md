# 个人原则工作台

**个人原则工作台是一款在本机运行的 AI 资料整理与思考工具。**你可以保存文字、图片和日常想法，让 AI 整理摘要、提取要点、关联相关素材，也可以记录自己的原则和经历，在聊天时作为参考。整理结果保留原文依据，个人原则由你确认后保存。支持 Codex SDK 和兼容 OpenAI 的 API 接入。

新电脑安装、SDK / API Key 通道切换见 [安装与配置](docs/INSTALL.md)。本仓库仅含源码与 LibreChat 修改补丁，不包含个人资料、数据库、登录凭据或真实密钥。

> 2026-10-18：已补上“收藏后自动整理”。打开聊天顶部书本按钮 → **新增素材** → 粘贴文字或上传图片/文本 → **保存并自动整理**。不必再发整理指令；完成后在素材详情查看摘要、要点、主题和相关素材，并可按主题筛选。旧素材点“整理这份素材”。AI整理保留原文依据，不自动成为你的原则或经历。

> 2026-10-16：长期资料库已接入 [LibreChat 聊天页](http://127.0.0.1:3081/c/new)，默认 Codex gpt-6.1-sol / medium。顶部书本按钮管理素材、原则和经历；明确收藏后保存原件，在本机解析和索引。聊天优先参考相关原则，也可读取相关素材和经历；精确的“记住：……”直接保存，模型草稿确认后保存。实际检查与限制见 [本轮验收](/D:/桌面文件/知识库/02.输出区/个人原则工作台/docs/LIBRARY-VALIDATION.md)。Windows 路径：`D:\桌面文件\知识库\02.输出区\个人原则工作台\docs\LIBRARY-VALIDATION.md`。

当前电脑启动新版：在项目根目录执行 `powershell -ExecutionPolicy Bypass -File .\integrations\librechat\start.ps1`。新资料库与旧库独立；旧数据未迁移。产品定义见 PRODUCT.md，接入与补丁复现见 integrations/librechat/README.md。

## 以下为旧 Django 工作台使用说明

旧入口和资料保留，以下启动与操作描述仅适用于 8765 旧工作台。

一个可在本机实际使用的中文应用：保存思想和经历，形成自己确认的原则，在具体情境中参考，并保留每次改变判断的依据。

源码、数据库迁移、Windows 启动脚本、测试和演示页都在本目录。默认数据库为空，不导入知识库个人资料，不预置正式原则。

## Windows 启动

当前电脑已建好 `.venv`、安装依赖并初始化数据库。以后在 PowerShell 执行：

```powershell
cd 'D:\桌面文件\知识库\02.输出区\个人原则工作台'
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

浏览器打开 **http://127.0.0.1:8765**。关闭运行窗口或按 `Ctrl+C` 停止；再次启动保留已有资料。`-Port 8767` 可换端口。

在另一台 Windows 电脑初次安装，需要 Python 3.12 或更新的兼容版本：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

`setup.ps1` 创建隔离虚拟环境、安装锁定依赖、执行迁移与配置检查。不修改系统 Python 包。若 `python` / `py` 是无法运行的应用商店别名，可指定安装路径：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python 'C:\你的Python目录\python.exe'
```

这是本机单用户版本：启动脚本绑定 `127.0.0.1`，后端也拒绝非环回请求；没有应用自身的用户账号系统与公网部署配置；Codex 认证独立通过官方 SDK 完成。

## 使用流程

1. **直接聊天**：首页输入问题，点“发送”（或 Ctrl / ⌘ + Enter），回复就在同一页出现。继续输入即可追问，不用先创建情境、打开预览或跳转建议页。原话先保存；调用失败可原地重试。暂时不需要 AI 时点“仅保存”，以后打开对话点“继续回复”。
2. **存一条素材**：点输入框下方“先存一条素材”，文字、链接、截图任填一种即可保存。标题、标签和来源信息默认收起，可稍后补充。保存后可提炼候选原则或关联已有原则；链接不自动获取正文，截图不自动 OCR。
3. **确认值得保留的内容**：聊天下面点“记录我的选择”，或检查并编辑提炼出的原则草稿，再明确确认。正式原则、选择和版本始终由你决定；聊天回复不会自动成为正式记录。

“参考与更多操作”里可以调整引用与对话深度、查看完整发送内容、生成选择草稿或删除对话。新对话默认日常模式，并按关键词检索相关的已采纳/试用中原则与经历；没有适用原则也能继续。引用链接随回复保留，可查看具体历史版本。

记录选择时可填理由、期待与回看日期；之后分别补充实际发生、自己的解释和仍不确定的地方。修订会创建新版本，旧选择的依据保持原样。

确认采纳价值取向时，请在原则库选择“价值取向”类型。它与经验判断、行动策略共用版本机制。采纳不代表被系统认证为客观真理。

首页以聊天输入框为主，下方显示最近对话；有内容时才显示可展开的关注原则与到期回看。没有空白任务面板、评分、打卡或后台推送。设置中的模型、推理强度和参考数量收在“高级设置”内，默认值无需修改。

## 模型模式

应用默认选择 **Codex**，模型为 `gpt-6.1-sol`，推理强度为 `high`。Codex 上下文容量遵循 SDK 原生的模型窗口与压缩配置，不再按工作台 12,000 字符上限截短；设置中的字符上限仅对普通 API 和演示生效。它复用当前 ChatGPT 登录和已有 Codex 额度，使用官方 `openai-codex==0.162.1` SDK 及 bundled CLI；没有登录时会明确报错，不会自动退回演示或普通 API。也可以在“设置与导出”中选择 **普通 API** 或 **本地演示**。设置保存到 `UserSettings.ai_provider` 和 `UserSettings.codex_model` / `codex_reasoning_effort`，环境变量 `AI_PROVIDER`、`CODEX_MODEL`、`CODEX_REASONING_EFFORT`、`CODEX_TIMEOUT` 可覆盖对应设置。

本地演示必须显式选择，显示“演示数据 / 非真实模型输出”。它不调用外部服务，不消耗额度，只演示可编辑建议和确认流程；未匹配就不编造引用。演示素材与结果不会自动进入正式原则或选择库，用户主动保存后仍保留演示标记。

Codex 每次分析都使用临时目录和 `ephemeral` 会话，只接收业务层整理出的字典文本，不读取或修改业务文件，不恢复 SDK 历史。业务记录、近 12 条消息和引用关系仍由 SQLite 保存，用这些内容重建多轮上下文。工具能力、继承的 MCP、网络搜索和写入权限均在启动前禁用并核验；正式内容仍须人工编辑和确认。Codex 使用 NDJSON 真 delta；普通 API 等待完整 JSON，不逐 token 流式展示。

选择普通 API 时，复制 `.env.example` 为 `.env`，自行在本机编辑：

```dotenv
AI_API_KEY=你的服务商密钥
AI_BASE_URL=https://服务商地址/v1
AI_MODEL=服务商支持的模型名称
AI_TIMEOUT=30
```

重启应用使配置生效。普通 API 采用兼容 Chat Completions 的 `POST /chat/completions` 和 JSON 模式；当前只承诺该兼容接口，DeepSeek 等具体厂商尚未做真实连通、费用和质量验证。不要把密钥写入页面、聊天或提交到 Git。`.env`、数据库与附件均已在本项目 `.gitignore` 排除。

普通 API 默认超时 30 秒，Codex 默认超时 120 秒，均不自动重试，避免意外重复费用。Codex 的时间上限包含登录状态读取、环境准备和生成；离开页面或超时后关闭本次临时进程，不等待可能无响应的取消 RPC。模型思考时保留轻量连接信号，以便发现浏览器断开。原话已保存时，重新打开对话点击“继续回复”即可再试。普通 API 仅允许 HTTPS 地址，环回地址可使用 HTTP。普通 API 没有自动切换到其他供应商，也不保证所有兼容服务都能通过校验。

**本地保存不等于本地推理。**聊天输入框说明将发送当前对话和相关参考；点击“发送”即明确发起模型调用，“仅保存”不调用模型。完整上下文仍可从“参考与更多操作”查看；素材提炼、选择草稿与原则修订保留发送预览和确认。固定规则、输出 schema 与模型参数由 provider 附加。页面打开后若接入方式或模型设置改变，旧页拒绝发送，避免悄悄换用其他服务。

Codex 首次使用可在“设置”点击“登录 Codex”，浏览器只打开官方 HTTPS OAuth 地址；“刷新状态”只读取官方 SDK 的粗粒度登录状态，不主动刷新 OAuth 令牌，续期由 SDK 按需处理。Codex 使用已有账号额度；普通 API 按所选服务商规则计费。仅发送当前任务所需的文字，不发送原始截图文件。高级设置可调整单次引用数量；普通 API / 演示可另设字符预算。Codex 的模型容量由 SDK 决定，不会自动发送整库或原始附件。

输出经严格字段、类型、长度与真实引用校验；超时、错误 JSON、伪造 ID、保存期间内容变化都会中止，保留此前已经保存的原始资料。外部素材内的指令不会成为数据库操作。本机 Codex 真实连接和网页闭环已通过；普通 API 的连通性、计费和具体厂商兼容性仍需另行验证。

## 数据、版本、导出与删除

- `data/workbench.sqlite3`：所有结构化数据。
- `data/attachments/`：原始截图，不覆盖原图；只接收验证后的 PNG/JPEG/WebP，单图上限 10 MB、4000 万像素。
- `data/.secret`：本机 Django 签名密钥，不进入导出或版本库。
- `workbench/migrations/`：建表及禁止覆盖历史原则版本的数据库触发器。

当前版本迁移为 `0004`，兼容已有记录；SQLite 是业务事实源，Codex 临时会话和普通 API 响应都不会替代或写入另一套历史库。

原则修改创建新版本。旧选择引用 `PrincipleVersion`，不会自动转向最新版本。原文保留空白与换行，素材编辑只能补元数据与校对文字；重新录入不同原文请新建素材。

写操作包含独立操作编号，并在事务内去重。双页面编辑、陈旧建议、引用变化返回清楚的冲突提示；表单保留你的输入，需要复制并对照最新内容重新合并。

“设置与导出”下载 ZIP：`data.json` 包含所有核心对象、历史版本、M2M 关联与确认/演示状态；`manifest.json` 包含结构版本、时间和附件 SHA-256；`attachments/` 包含原始图片。图片缺失时导出会失败并提示，不生成假装完整的备份。第一版不提供自动导入。

停用只改变原则状态并保留历史。永久删除须先查看影响，再勾选确认；确认时重新核对影响签名。删除原则会移除其全部版本及版本引用，保留正式选择和观察正文；删除选择会同时删除它的观察；删除素材会移除原图并解除来源关联。受影响的 AI 建议与消息快照会清理，用户自行书写的文本不会被推测性改写。若影响范围发生变化，必须重新预览。

## 测试与开发

```powershell
.\.venv\Scripts\python.exe manage.py test --verbosity 2
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe -m pip check
```

或运行 `test.ps1`。测试使用临时 SQLite 数据库与临时图片目录，不调用真实模型。领域规则与 provider 的测试先于对应实现编写；审查发现的问题增加回归测试。

实际命令结果、验收边界见 `docs/VALIDATION.md`。演示闭环脚本是 `scripts/browser_acceptance.cjs`；真实 Codex 脚本为 `scripts/browser_codex_acceptance.cjs`，会消耗已有账号额度，不由单元测试自动运行。两者只用于独立验收数据库，需要额外可用的 Playwright + Edge；运行应用本身不需要 Node.js。演示验收数据在 `artifacts/acceptance-data/`，真实 Codex 验收数据在 `artifacts/codex-e2e-data/`，不进入正式 `data/`。

## 常见问题

| 现象 | 处理 |
|---|---|
| Python 打开 Microsoft Store 或没有输出 | 安装 Python 3.12+，或给 setup.ps1 传 `-Python` 路径。 |
| `No module named django` | 使用项目 `.venv\Scripts\python.exe`，先运行 setup.ps1。 |
| `no such table` | 在项目目录运行 `.venv\Scripts\python.exe manage.py migrate`。 |
| 端口已占用 | 原窗口可能仍在运行；继续访问，或 `start.ps1 -Port 8767`。 |
| 双页面保存冲突 | 复制当前表单内容，重新打开最新版本对照后保存。不会静默覆盖。 |
| 素材链接不能提炼 | 补充正文或手工校对文字；首版不抓网页和识图。 |
| 模型超时 / JSON 校验失败 | 原始素材已保留；检查服务商配置与 JSON 模式支持，再手动重试。 |
| 页面过期或 CSRF 错误 | 使用同一个本机地址，返回复制未保存内容，再刷新重试；不要关闭 CSRF 校验。 |
| 数据库正忙 | 等正在保存的操作结束后重试；只开一个服务进程，避免在同步盘中同时运行同一数据库。 |
| 导出提示附件缺失 | 检查附件目录是否被移动或删除，恢复缺失文件后重试。 |

简短架构说明见 `docs/ARCHITECTURE.md`，实施规格见 `docs/IMPLEMENTATION.md`。Django 5.2 LTS 版本依据：[官方支持版本表](https://www.djangoproject.com/download/)。


## Codex 本机验收

2026-10-15 在 Windows x86_64 / Python 3.12.14 上，先核对官方文档，再以 `openai-codex==0.162.1` 复用已有 ChatGPT 登录，真实完成 SDK 两轮调用。集成后从网页保存虚构素材、生成真实候选、编辑并确认保存，再完成两轮情境对话；重启和导出证据见 `docs/VALIDATION.md`。没有使用独立 API Key。

设置中的“刷新状态”核对登录；未登录时，“登录 Codex”提供官方 HTTPS 登录链接，凭据由官方 SDK 管理。没有为了验收退出当前账号，所以新账号完整人工登录、真实额度耗尽和所有网络故障仍未实测，这些状态由替身单测覆盖。

浏览器若在结果返回时断开，完整建议可能已保存为草稿。保留原页面重试会通过操作编号返回同一建议；不会因重复提交重复生成正式原则。

官方参考：[Codex Python SDK](https://learn.chatgpt.com/docs/codex-sdk)、[认证方式](https://learn.chatgpt.com/docs/auth)、[Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)。
