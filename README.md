# OntoRAG 文件知识图谱服务

面向单一业务系统的 API：上传文档后，后台 worker 用 [OntoRAG](https://github.com/ontorag/ontorag) 执行 `run-stage propose` 与 `run-stage extract`，将 RDF 图持久保存，再通过 REST 与 MCP 查询。模型配置为 DeepSeek `deepseek-flash`。

## 本地运行

需要 Python 3.12。PowerShell 中运行：

```powershell
Copy-Item .env.example .env
notepad .env
.\setup.ps1
.\start.ps1
```

在 `.env` 中设置自己的 `ONTORAG_SERVICE_API_KEY` 与 `OPENROUTER_API_KEY`。另开终端启动后台 worker：

```powershell
.\.venv\Scripts\python.exe -m ontorag_service.worker
```

检查 `GET http://127.0.0.1:8890/health`。单独运行 API 只会将上传任务入队；需要 worker 才会抽取。默认本地使用 SQLite 与 `data/objects`。

## Vercel 部署

`app.py` 是 Vercel Python Function；`Dockerfile` 与 `vercel.json` 定义 Vercel Daemon worker。Daemons 目前为公开测试功能，须确认目标团队可用。部署前在目标项目创建**私有 Blob**，并连接一个 Postgres 数据库。设置以下环境变量：

已有 Supabase 项目可以直接作为 Postgres。到 Supabase 项目的 **Connect** 中复制 **Transaction pooler** URI（通常是 `:6543`），配置为 Vercel 的 `DATABASE_URL`；不要使用 Supabase 的 Project URL 或 anon/service key 代替数据库连接串。代码会使用单连接池、禁用 Psycopg 自动 prepared statements，并要求 SSL。任务表创建在 `ontorag` schema 中；不需要向 Supabase Data API 开放该 schema。

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | Postgres 连接串，API 和 worker 共用 |
| `BLOB_READ_WRITE_TOKEN` | 私有 Blob 存储访问令牌 |
| `ONTORAG_SERVICE_API_KEY` | 外部系统调用 REST/MCP 的长随机密钥；轮换时可用 `ONTORAG_SERVICE_API_KEYS=旧,新` |
| `OPENROUTER_API_KEY` | DeepSeek API 密钥；变量名沿用 OntoRAG |
| `OPENROUTER_BASE_URL` | `https://api.deepseek.com` |
| `OPENROUTER_MODEL` | `deepseek-flash` |
| `STORAGE_BACKEND` | `blob` |
| `ONTORAG_BASE_IRI` | 生成图所用的稳定命名空间，例如 `https://your-domain.example/kg/` |

同一密钥用于全部文档。不要提交 `.env`。`vercel.json` 默认将 worker 放在 `iad1`；数据库与 Blob 尽量选邻近区域。部署后先检查 `/health`，再按 [对接说明](docs/INTEGRATION.md) 上传测试文档并轮询状态。真实 DeepSeek 抽取需要有效密钥、余额和网络连接，目前未使用真实密钥验证。

## 当前范围

- 直传上限 4 MiB，支持 Markdown、TXT、HTML、EPUB、PDF。大文件需要增加直传 Blob 流程。
- 每份文档形成独立知识图谱，查询和 MCP 工具均需 `document_id`。当前不做跨文档联合查询。
- 提案自动进入抽取，没有人工审查本体的审批环节。
- Worker 最多重试 3 次；失败后可调用 `/v1/documents/{id}/retry`。中断任务在租约到期后重新领取。
- Worker 处理时每分钟续租；同一任务不会在租约过期后由旧 worker 覆盖新结果。
- 图和原文的持久化依赖私有 Blob；任务状态依赖 Postgres。缺任一资源时无法完成生产部署。
- 查询仅允许 SPARQL SELECT，最多返回 500 行。上传、图下载和 MCP 均需 Bearer 密钥。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖上传、幂等、worker 产物入库、REST 查询与 MCP 握手；为避免付费调用，模型阶段使用固定 RDF 产物代替。
