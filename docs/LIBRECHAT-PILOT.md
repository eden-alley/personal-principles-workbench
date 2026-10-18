# LibreChat 集成与运行验收记录

2026-10-18增量：新增素材表单、收藏后后台摘要/要点/主题/素材关联、原文依据、主题筛选和重试已完成。后台整理使用独立只读Codex实例，不占前台聊天锁；普通聊天提示词改为资料整理与讨论助手，并附入当前有效的AI整理摘要。最终验证见 docs/LIBRARY-VALIDATION.md 的2026-10-18记录；下文2026-10-16为基础接入记录。

更新：2026-10-16。聊天底座已接入独立长期资料库、共享上下文网关和资料库界面；当前默认模型为 gpt-6.1-sol / medium。下文将当前证据与首轮 high 试验分开，最终检查及未验证项见 docs/LIBRARY-VALIDATION.md。

## 当前行为与边界

- 聊天、图片/文本附件、历史、流式回复、停止和重新生成沿用 LibreChat v0.8.8。新增资料库弹窗、用户消息收藏、助手引用和可编辑草稿确认；不是原版零修改状态。
- 素材、原则、经历分开保存。日常优先参考相关已采纳原则，相关未关联素材与已确认经历可以自动补充；素材明确标注“收藏不代表认同”，反例允许挑战原则。
- 明确保存意图和精确内容可直接保存并返回程序回执；模型推断和整理形成草稿，确认后才写入。“有道理”等泛泛认可不会自动保存。
- 普通聊天最多一次有界检索和一次模型回答，可选草稿在同一次回答中处理；不另加后台提炼或标题模型。明确保存命令可不调用模型。
- 本地 RapidOCR 和 FastEmbed / BAAI/bge-small-zh-v1.5 已接入；原件、解析文字与索引分别记录状态。收藏成功不等于图片已经可按内容检索。
- 使用已有 Codex 登录，不读取或导出账号令牌，不新增付费 OCR/向量服务。第三方兼容 API 尚未真实接入，待地址、模型与相应授权后验证。
- 新长期数据独立放在 data/librechat-library；旧工作台与旧正式库保持独立，不自动迁移。测试材料明确虚构，不作为用户事实。

## 使用和启动

在 [本地注册页](http://127.0.0.1:3081/register) 创建自己的本地账号（与 Codex 登录不同），随后在 [聊天页](http://127.0.0.1:3081/c/new) 使用。资料库入口位于聊天 Header。聊天附件需明确收藏才进入长期库。

已有环境重新启动：

```powershell
cd 'D:\桌面文件\知识库\02.输出区\个人原则工作台'
powershell -ExecutionPolicy Bypass -File .\integrations\librechat\start.ps1
```

脚本按 MongoDB（27018）、Codex bridge（8767）、共享上下文网关（8768）、LibreChat（3081）的顺序启动四项服务，均监听 127.0.0.1，不注册开机服务；已监听的端口跳过，不终止现有进程。网关启动时只迁移独立新库，恢复待处理索引。脚本不负责重下载运行环境。

| 位置 | 当前用途 |
| --- | --- |
| artifacts/librechat-upstream | 官方 v0.8.8 基线 e8f3be08623663d4ad7f7241e693c94469b63bb0，加当前本地接入修改 |
| integrations/librechat/librechat.yaml | 自定义端点连接 8768/v1；默认 gpt-6.1-sol / medium |
| integrations/librechat/gateway.py | 8768 上下文与资料 API；接收服务端认证身份，调用 Python 领域服务 |
| integrations/librechat/bridge.py | 8767 Codex 协议适配、流式输出和取消 |
| artifacts/librechat-runtime/mongo-data | 27018 独立聊天数据库 |
| data/librechat-library | 新长期库 SQLite 与独立原件附件 |
| artifacts/librechat-runtime | 服务日志与本地服务配置；包含服务密钥，不作为可公开导出的材料 |
| integrations/librechat/library.patch | 已生成并通过固定基线23文件应用及内容一致性检查；复现见同目录 README |

请求链路是 LibreChat（3081）→ 共享上下文网关（8768）→ Codex bridge（8767），流式回答反向返回。轮次快照按 assistant messageId 区分，重新生成不会共用父用户消息的引用槽位。自然语言简短确认还会核对当前分支上一回答与草稿的对应关系。

## 当前已有验收证据

### 真实聊天与原件闭环

artifacts/library-flow-acceptance.json 记录 gpt-6.1-sol / medium 的 7 个真实聊天步骤，pass=true、errors=[]。已记录截图“先收着” → 本地 OCR 与索引 ready → 新对话社交问题召回原则、未关联素材及已确认经历 → 泛泛认可继续聊天 → 无关 Python 问题空召回 → 指定素材/经历拟定草稿 → 明确确认保存。记录包含多个对话，不能解读为每轮都固定读取全部类型。

保存结果包含 5 个原则版本：旧版本关联旧素材/经历 ID，新版本关联新 ID，历史 evidence 摘录保留。导出原件 SHA-256 核对一致，exportOriginalHash=true。该证据证明本次聊天与 API 闭环，不代表所有资料库按钮都已经完成浏览器验收。

| 此次步骤 | 首个可见字 | 完成 | 检索耗时 |
| --- | --- | --- | --- |
| 社交问题自动补充资料 | 11.022 秒 | 14.425 秒 | 31 ms |
| 无关 Python 问题 | 5.276 秒 | 6.120 秒 | 16 ms |
| 指定资料拟定原则草稿 | 5.047 秒 | 10.993 秒 | 31 ms |
| 明确“确认保存”回执 | 0.216 秒 | 0.300 秒 | 0 ms |

这些是本次样本的网页计时，不是模型或检索延迟保证，也不能直接与不同冷/热状态的旧 high 测试比较。

### 语义与 OCR 样例

artifacts/library-semantic-acceptance.json 来自 scripts/library-semantic-check.py 的独立合成样例：12/12 查询通过，OCR 通过，检索查询 3–5 ms，整套 2.07 秒。包含同义问题、原则与反例共存，以及不相关问题空召回；不等同于真实大库准确率评估。

### 页面性能预算

artifacts/library-lighthouse-final.log 记录隔离环境 1 项通过，测试按上游要求给 Mongo 查询增加 250 ms 延迟。测试地址为临时 3098，不是日常 3081：

| 指标 | 中位数 | 预算 |
| --- | --- | --- |
| LCP | 3948.016 ms | 4500 ms |
| CLS | 0.01523 | 0.1 |
| TBT | 96.587 ms | 500 ms |

这是页面加载预算，不是聊天首字或模型速度指标。前端12项测试和相关workspace类型检查通过，最终构建、界面及补丁结果见 docs/LIBRARY-VALIDATION.md。

## 收尾验证与未做范围

- 桌面/手机新增、修订、历史版本、回收恢复、原件下载与导出通过浏览器验收；停止后新发、重新生成去重和刷新保留通过。键盘与全部故障组合未穷举。
- Python140项、接口4项、前端12项通过，相关类型检查与构建通过；身份隔离、冲突和分支等由自动回归覆盖。
- integrations/librechat/library.patch 已生成，在固定基线23个原始文件上重放并核对内容一致。
- 第三方兼容 API 真实连接、旧数据迁移、永久删除、容器运行、远程访问和公网发布均未执行。当前收藏支持 PNG/JPEG/WebP 与 TXT/Markdown；PDF、Office、音频、网络搜索和执行工具不在此次接入范围。

## 历史：首轮聊天底座验证

以下保留 2026-10-15 至早期 2026-10-16 的安装与模型延迟记录。当时只验证聊天底座、没有接长期库；其中“三项服务”“原版未改”和 high 默认等描述仅是当时情况，不作为当前启动或能力说明。

### 2026-10-16 早期 medium 与延迟排查

用户同意将当前默认推理强度从 high 调为 medium；已同步修改 LibreChat 自定义端点和桥接默认值并重启这两项服务，模型仍为 gpt-6.1-sol。下文 high 数据是首轮验收的历史记录。

同一句虚构连通测试直接经桥接调用，分段计时如下：

| 场景 | 准备阶段 | turn 启动返回后至首字 | 总首字时间 |
| --- | --- | --- | --- |
| 冷 medium | 7.703 秒（登录检查 2.453、运行时准备 0.469，其余为 thread/turn 准备） | 12.953 秒 | 20.656 秒 |
| 热 medium | 0.485 秒 | 6.500 秒 | 6.985 秒 |
| 热 high | 0.484 秒 | 11.969 秒 | 12.453 秒 |

随后真实网页 medium 测试首字 6.349 秒、完成 6.524 秒，正常返回。桥接 13 项回归通过。直接桥接仍能复现等待，说明等待不全来自 LibreChat 界面；但这个小样本没有区分服务端排队、网络和推理时间，也不能断言 medium 是唯一影响因素或承诺固定提速。没有测量 Codex 桌面客户端或 ChatGPT 同时段的对照延迟。

新增测量脚本 `scripts/librechat-latency.py`、`scripts/librechat-medium-check.cjs`；结果在 `artifacts/librechat-latency-medium.json` 和 `artifacts/librechat-medium-browser.json`，只含虚构测试输入和耗时。

用户另要求增加兼容 OpenAI 格式的第三方 API，但尚未准备服务名称、Base URL 和模型。本轮保留该待办，未猜测供应商、创建无效入口或处理 API 密钥，也未发起付费 API 调用。待提供非敏感连接信息后使用 LibreChat 原生自定义端点接入。

### 首轮安装记录

- 初始检查：Docker CLI 可用但后台未启动；`docker desktop start` 因注册表缺项失败，改为启动现有 Docker Desktop 程序。GitHub API 限流/迁移响应不稳定，改用公开 Git tags 核实 v0.8.8。
- 本轮使用当前对话直接实施；遵循知识库的按需流程，不额外创建 worktree，不自动暂存或提交。

### 首轮聊天底座验收（high，历史）

真实 Edge 浏览器、真实 Codex SDK、gpt-6.1-sol / high，未使用模拟回复：

| 场景 | 首个可见字 | 完成 | 结果 |
| --- | --- | --- | --- |
| 本轮首条短问答（冷启动） | 25.672 秒 | 25.871 秒 | 正常 |
| 追问前文临时暗号 | 8.208 秒 | 8.324 秒 | 正确回答“橙子” |
| 图片识别 | 6.461 秒 | 7.299 秒 | 正确辨认红色方形、蓝色圆形 |
| TXT 附件 | 10.694 秒 | 11.538 秒 | 正确读出青松计划、Q7M4、73元 |
| 停止后新发 | 10.526 秒 | 10.711 秒 | 正常回复 |

重新生成成功；刷新保留消息；重启全部三项服务后消息与附件卡片仍在。桌面与手机截图已检查，浏览器脚本未捕获页面异常。以上为本次短输入的实际测量，不是速度保证；high 推理仍可能有较长等待。

`python manage.py test tests --noinput`：113 项通过，Django system check 无问题。桥接其中 13 项覆盖鉴权、输入拒绝、流/非流、温热复用、取消清理、准备失败、总超时和 header 断开后释放锁。最初直接用 unittest discover 跑整个 Django 测试集因未初始化 settings 失败，随后使用正确的项目测试入口全量通过。原版 `npm ci` 与 `npm run frontend` 成功，构建有依赖弃用、直接 eval 与大体积分块提示，没有因此改动上游源码。

证据文件：`artifacts/librechat-acceptance.json`、`artifacts/librechat-restart-check.json`、`artifacts/librechat-final.png`、`artifacts/librechat-mobile.png`。最初连通脚本有固定等待，不能用它的 31 秒作为响应耗时；上表来自后续按消息出现与完成按钮计时的正式验收。
