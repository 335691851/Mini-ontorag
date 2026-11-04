# OntoRAG 文件知识图谱服务

面向单一业务系统的 API：上传文档后，后台 worker 用 [OntoRAG](https://github.com/ontorag/ontorag) 执行 `ingest` 与 `extract-instances`。全部制度共用版本化的[法务与审计领域本体](ontorag_service/resources/legal_audit_v1.json)，文档图只记录实体、关系、属性与原文出处。REST 与 MCP 支持跨文件查询。模型配置为 DeepSeek `deepseek-flash`。领域模型的范围与数据分层见[本体建模说明](docs/DOMAIN_ONTOLOGY.md)。

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
| `BLOB_STORE_ID` | 私有 Blob 连接后由 Vercel 自动设置，API Function 使用 OIDC 访问 |
| `BLOB_READ_WRITE_TOKEN` | 可选；仅在非 Vercel 环境直接访问 Blob 时使用 |
| `ONTORAG_SERVICE_API_KEY` | 外部系统调用 REST/MCP 的长随机密钥；轮换时可用 `ONTORAG_SERVICE_API_KEYS=旧,新` |
| `OPENROUTER_API_KEY` | DeepSeek API 密钥；变量名沿用 OntoRAG |
| `OPENROUTER_BASE_URL` | `https://api.deepseek.com` |
| `OPENROUTER_MODEL` | `deepseek-flash` |
| `STORAGE_BACKEND` | `blob` |
| `ONTORAG_API_BASE_URL` | 常驻 worker 通过 API Function 访问私有 Blob 的 Production 地址，例如 `https://mini-ontorag.vercel.app` |
| `ONTORAG_BASE_IRI` | 旧版逐文件本体流程保留的配置；共享本体命名空间由版本化资源文件固定 |

同一密钥用于全部文档。不要提交 `.env`。`vercel.json` 默认将 worker 放在 `iad1`；数据库与 Blob 尽量选邻近区域。部署后先检查 `/health`，再按 [对接说明](docs/INTEGRATION.md) 上传测试文档并轮询状态。2026-11-04 已在 Production 完成 16 份制度文件的真实抽取，并验证图谱、本体与 SPARQL 查询。

## 查看知识图谱

在线接口 `GET /v1/ontology/ttl` 返回共用本体；`GET /v1/documents/{id}/graph` 返回每份文件的事实与出处，均需 Bearer 密钥。`GET /v1/knowledge-graph/summary` 和 `POST /v1/knowledge-graph/sparql` 查询整个领域知识图谱。离线查看器由 `scripts/build_graph_viewer.py` 从下载的 Turtle 文件生成，默认显示整个领域本体，并允许切换到单份文件的实例和原文依据。重新抽取后应重新下载全部图再生成查看器。

```powershell
$viewer = 'C:\path\to\知识图谱查看' # 改为本机实际目录
.\.venv\Scripts\python.exe scripts\build_graph_viewer.py --manifest (Join-Path $viewer '导入记录.json') --graphs (Join-Path $viewer 'rdf_domain') --d3 (Join-Path $viewer 'assets\d3.min.js') --output (Join-Path $viewer '领域知识图谱.html')
```

生成的 `文档索引.csv` 列出每份制度的 `document_id`、实体和关系数量。所有输入图必须使用同一个本体版本，脚本会检查并拒绝混用。图谱内容由模型自动抽取，正式使用前应核对关键实体、关系与原文依据。

## 当前范围

- 直传上限 4 MiB，支持 Markdown、TXT、HTML、EPUB、PDF。大文件需要增加直传 Blob 流程。
- 共用 `legal-audit-v1` 本体（35 个类、37 个对象关系、15 个数据属性）；实例按文档留存出处，联合查询聚合最多 100 份已按当前版本抽取的文件。
- 本体是首版领域模型，需要法务与审计人员确认概念边界。模型只抽取文件中明确提及的候选事实，不据此自动认定真实违规或法律责任。
- Worker 最多重试 3 次；失败后可调用 `/v1/documents/{id}/retry`。中断任务在租约到期后重新领取。
- Worker 处理时每分钟续租；同一任务不会在租约过期后由旧 worker 覆盖新结果。
- 图和原文的持久化依赖私有 Blob；任务状态依赖 Postgres。缺任一资源时无法完成生产部署。
- 查询仅允许 SPARQL SELECT，最多返回 500 行。上传、图下载和 MCP 均需 Bearer 密钥。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖上传、幂等、worker 产物入库、REST 查询与 MCP 握手；为避免付费调用，模型阶段使用固定 RDF 产物代替。
