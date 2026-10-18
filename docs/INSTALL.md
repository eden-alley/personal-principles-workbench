# 在另一台 Windows 电脑安装

仓库保存应用源码、迁移、测试和 LibreChat 补丁，不包含个人数据库、账号、附件、密钥、下载模型或依赖目录。它不是个人数据备份。当前入口为 LibreChat；根目录 `start.ps1` 是旧 Django 入口。

## 环境与安装

需要 Git、Python 3.12+、与 LibreChat v0.8.8 兼容的 Node.js/npm，以及 Windows x64 MongoDB。以下命令都在仓库根目录执行。安装会下载公开依赖和本地检索模型，不调用付费模型 API。

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
New-Item -ItemType Directory -Force artifacts | Out-Null
git clone --branch v0.8.8 --depth 1 https://github.com/danny-avila/LibreChat.git artifacts/librechat-upstream
git -C artifacts/librechat-upstream rev-parse HEAD
# 必须为 e8f3be08623663d4ad7f7241e693c94469b63bb0，其他版本不要直接套补丁。
$patch = (Resolve-Path integrations/librechat/library.patch).Path
git -C artifacts/librechat-upstream apply --check $patch
git -C artifacts/librechat-upstream apply $patch
Push-Location artifacts/librechat-upstream
npm ci
npm run frontend
Pop-Location
.\.venv\Scripts\python.exe scripts/setup-library.py
```

从 MongoDB 官方下载 Windows x64 8.0.20 ZIP，解压后让 `mongod.exe` 位于：

```text
artifacts/mongodb-8.0.20/mongodb-win32-x86_64-windows-8.0.20/bin/mongod.exe
```

创建数据目录并生成本机随机服务密钥：

```powershell
New-Item -ItemType Directory -Force artifacts/librechat-runtime/mongo-data | Out-Null
powershell -ExecutionPolicy Bypass -File .\integrations\librechat\configure.ps1
powershell -ExecutionPolicy Bypass -File .\integrations\librechat\start.ps1
```

打开 http://127.0.0.1:3081 ，首次使用注册本机账号。服务仅绑定本机；这份配置不用于公网部署。Codex 模式需本机完成官方 Codex 登录。

## 两种模型接入通道

复制 `.env.example` 为 `.env`（已有 `.env` 时直接编辑，勿覆盖已有密钥）。系统环境变量优先于文件。新版使用 `LIBRARY_*`，旧 Django 使用独立的 `AI_*` 设置。

| 通道 | 配置 | 使用范围 |
| --- | --- | --- |
| 官方 Codex SDK，默认 | `LIBRARY_PROVIDER=codex` | 聊天和后台整理使用现有 Codex 登录/额度 |
| OpenAI-compatible API | 下方四项 | 聊天和后台自动整理均使用该 API，按服务商计费 |

```dotenv
LIBRARY_PROVIDER=api
LIBRARY_API_BASE_URL=https://服务商地址/v1
LIBRARY_API_KEY=在本机填写真实密钥
LIBRARY_API_MODEL=服务商支持的模型名称
LIBRARY_API_TIMEOUT=120
```

API 必须支持 Chat Completions：聊天需 SSE 流式响应及 `[DONE]`，整理需非流式文本响应；图片聊天还要求模型支持图片内容。除本机环回服务外仅允许 HTTPS。配置错误或调用失败不会自动切到其他通道。

切换后先停止当前网关和 LibreChat 进程，再重新执行 `configure.ps1` 和 `start.ps1`。启动脚本会跳过已监听端口，所以仅再次执行启动脚本不会让旧进程读取新配置。SDK 页面显示 GPT-6.1，API 页面显示“API 模型”，实际模型由服务端 `.env` 决定。

新增素材的“保存并自动整理”和手动重新整理都会调用所选模型。API 模式下，这些操作也可能产生费用。不要把真实 API Key 放进 YAML、聊天、测试文件或 Git。

## 验证与备份

```powershell
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py check
```

SDK 已在原电脑使用；API 适配通过模拟接口测试，尚未验证具体付费供应商。另一台电脑的全新安装尚未实机验证。图片 OCR、语义检索需要本机依赖及模型缓存。

个人资料请另行使用资料库导出，并单独备份 MongoDB 和 `data/`；GitHub 源码仓库不会同步聊天或个人素材。LibreChat 来源和许可证见 `THIRD_PARTY_NOTICES.md`。
