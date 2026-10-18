# LibreChat 长期资料库验收

## 2026-10-18：自动素材整理增量验收

本轮补齐新增素材入口和收藏后的自动整理，服务已启动。新收藏先保存原件，再经本机解析/索引进入独立后台Codex整理任务；旧素材逐条手动触发。以下使用测试账号合成材料，没有批量处理正式用户已有素材。

| 检查 | 本轮结果 |
| --- | --- |
| Python全量 | 152项通过，9.942秒；artifacts/library-organization-full-tests.log |
| 整理领域回归 | 11项：排队去重、旧素材不自动排队、独立结果、同账号关联与引文、失败/重试、解析失败、并发/过期租约、移除/恢复后的旧结果失效、上下文AI标签 |
| API代理Jest | 7项通过，新增直接素材字段白名单、附件大小和鉴权整理入口；library-organization-proxy-tests.log |
| 前端Jest | 16项通过，新增直接保存、主题筛选、失败保留上次结果、MIME缺失图片识别；library-organization-ui-tests.log |
| 类型、构建、lint | packages/api、packages/data-provider、client标准tsc通过；完整前端/包构建通过；Library组件及对应API/data-provider ESLint通过 |
| 数据库迁移 | makemigrations --check --dry-run返回No changes detected；新增0009仅用于独立新库/隔离测试，旧正式库未迁移 |
| 真实模型 | 两份文字+一张中文图片均自动产生摘要、要点、主题；第二份与第一份形成补充关系及双方引文；整理期间前台聊天成功 |
| 浏览器复查 | 主题筛选、持久化结果、原文依据、导出、原图SHA-256、390px无横向溢出均通过，JS errors=[]；library-organization-ui-finish.json |
| Lighthouse | npm run lighthouse完整命令通过，1项/2.3分钟；LCP4093.847ms、CLS0.0152384、TBT145.721ms均低于预算；library-organization-lighthouse.log |
| 补丁重放 | 固定基线25文件干净应用后内容一致；library-organization-patch-check.log及更新后的manifest |
| 当前服务 | 3081登录页HTTP200且包含应用根节点；Windows PowerShell可正确解析UTF-8 BOM启动脚本 |

真实整理证据在 artifacts/library-organization-acceptance.json。首次流程完成三份资料生成，但在主题筛选标签处失败；修正显式label关联并重建、重启服务后，用已生成的同一批记录完成UI复查，避免重复调用模型。最终UI结果在 library-organization-ui-finish.json；两份证据应合并阅读，不把首次失败隐藏成全程一次通过。截图为 library-organization-desktop.png 和 library-organization-mobile.png。

独立只读审阅未发现任务租约、失败恢复、owner限定及来源引用边界的重要未解决缺陷；发现MIME缺失时图片被误作文本的问题，已补扩展名映射并通过先失败后成功的回归。引文仅被程序校验为输入中的字面内容，概括或关联解释的语义仍可能有误，界面明确要求核对原文。

当前限制：只自动处理明确收藏的新素材；旧素材点击“整理这份素材”。直接表单最多4附件、合计2MB，更大文件可用聊天上传后收藏。URL不抓正文，不自动物理合并/删除重复材料，也没有全库主题综述的定时更新。每份素材正常使用当前Codex额度调用一次整理模型；手动重试会再次调用。普通聊天不会因此增加串行模型调用。个人原则/经历仍须明确保存意图，旧数据迁移和第三方付费API未执行。

## 2026-10-16：首次资料库接入验收

日期：2026-10-16。固定 LibreChat v0.8.8 / e8f3be08623663d4ad7f7241e693c94469b63bb0，Windows Edge，Codex gpt-6.1-sol / medium。仅用测试账号的合成材料；未读取其他账号、导入个人画像或迁移旧正式库。

## 已执行

| 检查 | 结果与证据 |
| --- | --- |
| `python manage.py test tests --noinput` | 140 项通过，9.526 秒；artifacts/library-full-tests.log |
| `python manage.py makemigrations --check --dry-run` | No changes detected；library-migrations-check.log |
| API 路由 focused Jest | 4 项通过；library-api-tests.log |
| Library 前端 focused Jest | 12 项通过；library-client-tests.log |
| 改动 workspace 类型检查 | packages/api、packages/data-provider、packages/data-schemas、client 的 tsc --noEmit 均通过；构建不代替类型检查 |
| 构建与 lint | data-provider、data-schemas、API 及前端构建成功；指定改动文件 ESLint 通过，最后修改的路由测试再次 lint 通过 |
| 独立语义与 OCR | 12 条合成查询通过，中文图片 OCR 通过；library-semantic-acceptance.json |
| 真实聊天闭环 | library-flow-acceptance.json：7 个聊天步骤通过，JS errors=[]，原件导出 SHA-256 一致 |
| 真实资料库 UI | library-ui-acceptance.json：创建、修订原因、旧版本保留、回收与恢复、原件下载哈希、JSON 导出均通过；390px 手机宽度无横向溢出，JS errors=[] |
| 停止与重试 | library-retry-acceptance.json：停止后新发成功、重新生成不重复保存、刷新保留回执与记录 |
| Lighthouse | 1 项通过；LCP 3948.016ms < 4500ms，CLS 0.01523 < 0.1，TBT 96.587ms < 500ms；library-lighthouse-final.log |
| 补丁重放 | scripts/package-library-patch.py：23 文件在固定基线原始文件上应用成功，逐文件内容一致；integrations/librechat/library-patch-manifest.json |

聊天闭环覆盖图片“先收着”→解析/索引 ready→新对话以不同说法召回原则、未关联素材及确认经历→“有道理”不写入→无关 Python 问题空召回→指定素材/经历拟定原则→自然语言“确认保存”→新版本和当时依据导出。明确保存的回执由程序返回，本次自然确认约300ms；普通模型回答本次首字约5–11秒。检索约14–31ms，首字耗时还受模型端影响，不能当作固定性能保证。

单元/接口回归覆盖账号隔离、虚构引用拒绝、确认幂等、版本冲突、分支绑定、解析失败保留原件、缓存重放、并发租约及过期请求不能覆盖新结果。独立代码审阅提出的轮次缓存、完整版本依据和租约竞态已修正并加入回归。

## 复现注意

浏览器脚本在项目根目录运行：scripts/library-flow-acceptance.cjs、library-ui-acceptance.cjs、library-retry-acceptance.cjs；使用本机 Playwright / Edge 和指定测试账号，不读取正式用户资料。完整聊天脚本会调用当前 Codex 模型并在测试账号保存合成记录。

Windows 的前端 Jest 测试进程临时添加 `^\\./src/(.*)$` 到 `<rootDir>/src/$1` 的映射，未修改共享 Jest 配置。接口测试在 beforeEach 内安装 fetch mock、afterEach 恢复，避免上游 restoreMocks 清掉 mock 后误发真实请求。

`npm run lighthouse` 的构建阶段成功，初次测试因系统中文使上游英文登录选择器失败。通过 scripts/librechat-test-locale.cjs 仅给隔离测试浏览器设置 en-US 后，`npm run lighthouse:run` 完成通过；生产界面语言没有为此改变。测试使用独立3098服务及临时Mongo，注入每次Mongo查询250ms延迟。

## 明确限制

- 尚未配置或真实验证第三方兼容 API；当前实际模型仅 Codex。没有调用付费 OCR/嵌入 API。
- 合成检索样例不能代表用户真实大库的召回率；阈值可能漏掉相关内容。可手动指定本对话参考资料。
- 原件可下载，尚无内嵌图片预览和原聊天一键回链；链接不会自动抓取全文。资料列表当前最多显示200条，导出不受该上限影响。
- 本轮真实浏览器未穷举故障、键盘导航、所有编辑分支组合，也未通过删除聊天来实测原件独立性；相关持久化、授权与冲突边界由领域/接口测试及代码审阅覆盖。没有进行正式用户数据迁移。
- 界面回收站为可恢复移除，未实现永久删除。新旧库独立保存；不是公网部署。

桌面和手机验收截图位于 artifacts/library-ui-desktop.png、library-ui-mobile.png；这些为测试材料，不是用户事实。


## 2026-10-18 GitHub 源码发布与双通道

新增 LIBRARY_PROVIDER=codex|api，前台沿用同一 SSE / ProposalFilter / 取消路径，后台使用独立 SDK 或非流式兼容 API。API 模型覆盖前端占位值，服务令牌和账号头不传给供应商；整理记录保存实际模型名称。默认仍为 Codex，没有真实付费 API 调用。新增通道与来源模型测试后，完整 Python 测试 160 项通过（13.550 秒），manage.py check 与 makemigrations --check --dry-run 通过，LibreChat 25 文件补丁重放比对通过。

新增安装与配置说明、第三方许可和源码导出脚本。上传只含源码、测试、说明和固定基线补丁，排除 data、artifacts、.venv、.env 和密钥。具体供应商端到端与全新电脑安装未验证；本轮没有更改前端源码，沿用前次界面检查。
