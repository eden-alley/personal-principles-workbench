# LibreChat 长期资料库接入

本地入口：http://127.0.0.1:3081/c/new 。默认 Codex gpt-6.1-sol / medium。账号及聊天保存在独立 MongoDB，长期资料保存在 `data/librechat-library`，旧 `data/workbench.sqlite3` 不自动迁移。

## 当前电脑启动

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\integrations\librechat\start.ps1
```

脚本启动 MongoDB（27018）、Codex bridge（8767）、资料上下文网关（8768）和 LibreChat（3081），均限本机。前端顶部书本按钮打开资料库；聊天消息可收藏，明确的 `记住：具体原则` 直接保存，模型拟定的草稿需用户确认。

2026-10-18新增：资料库中的“新增素材”可直接粘贴文字或上传图片/文本。保存后，本机解析/索引完成即后台调用当前Codex整理摘要、带引文的要点、主题和相关素材；无需再发整理指令。旧素材按“整理这份素材”处理。整理使用单独的只读模型实例，不占前台聊天请求锁；仍使用当前账号额度，不新增付费API。原件、整理结果和个人原则分开。Windows启动/配置脚本使用UTF-8 BOM，兼容系统自带PowerShell解析中文。

## 固定上游及重放

上游基线：LibreChat v0.8.8，提交 `e8f3be08623663d4ad7f7241e693c94469b63bb0`。`library.patch` 包含账号代理接口、配置开关、前端及相关测试；文件清单见 `library-patch-manifest.json`。补丁已在基线原始文件上应用并逐文件比对。

在该基线的干净源码目录执行（先检查，再应用）：

```powershell
git apply --check 'D:/桌面文件/知识库/02.输出区/个人原则工作台/integrations/librechat/library.patch'
git apply 'D:/桌面文件/知识库/02.输出区/个人原则工作台/integrations/librechat/library.patch'
npm ci
npm run frontend
```

上游源码放在 `artifacts/librechat-upstream`。本项目 Python 依赖见 requirements.txt；初次运行本地语义检索需在项目虚拟环境执行 `python scripts/setup-library.py` 下载公开模型，运行时仅使用本地缓存。已有环境不必重复安装或下载。

`configure.ps1` 负责本机配置，使用受保护的本地服务令牌；不要把生成的 `.env` 或 `service-secrets.json` 加入版本库。`librechat.yaml` 的 `interface.library: true` 显式启用资料库，默认关闭时不会给原版启动增加资料库查询。

## 验证与限制

验收见项目 `docs/LIBRARY-VALIDATION.md`。补丁打包可运行 `python scripts/package-library-patch.py`；它不暂存、不提交，只重建补丁并验证基线应用结果。升级上游时需重新检查和构建。

支持 Codex SDK 和通过 LIBRARY_PROVIDER=api 选择的兼容 API，聊天和后台整理统一切换。配置见 ../../docs/INSTALL.md；第三方 API 尚未进行真实付费调用。图片支持本地 OCR，链接只保存地址，不自动抓正文；资料库提供原件下载，尚未提供内嵌图片预览或一键跳回原聊天。界面最多展示最近200条记录，导出包含全部所属账号记录。回收站为可恢复移除，不是永久删除。
