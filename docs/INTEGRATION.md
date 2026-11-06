# 外部业务系统对接

服务根地址记为 `BASE_URL`。除 `/health` 外，请求都带 `Authorization: Bearer <服务密钥>`。仅服务端持有密钥，不要放入浏览器或移动端代码。生产环境使用 HTTPS。REST 与 MCP 共用同一组密钥；此部署只面向一个可信业务系统，没有按用户或租户划分的权限。

部署时设置 `ONTORAG_SERVICE_API_KEY`。轮换时可改设 `ONTORAG_SERVICE_API_KEYS=旧密钥,新密钥`，让调用方切换到新密钥后再移除旧密钥并重新部署。密钥应为随机生成的长字符串。

接口版本固定在 `/v1`。`GET /openapi.json` 和 `/docs` 也需要 Bearer 密钥。业务接口清单：

| 方法与路径 | 用途 |
| --- | --- |
| `POST /v1/documents` | 上传并创建抽取任务 |
| `GET /v1/documents` | 列出最近文档，`limit` 为 1–100 |
| `GET /v1/documents/{id}` | 查询状态 |
| `GET /v1/documents/{id}/quality` | 查看抽取质量报告 |
| `POST /v1/documents/{id}/publish` | 人工复核后发布 |
| `PUT /v1/documents/{id}/graph` | 提交修正后的完整 Turtle 图 |
| `POST /v1/documents/{id}/retry` | 重新处理失败任务 |
| `POST /v1/documents/{id}/reprocess` | 以当前领域本体重新抽取已完成或失败的文件 |
| `GET /v1/ontology`、`GET /v1/ontology/ttl` | 获取版本化共享领域本体 JSON/Turtle |
| `GET /v1/knowledge-graph/summary` | 跨文件知识图谱统计 |
| `POST /v1/knowledge-graph/sparql` | 跨文件只读 SELECT 查询 |
| `GET /v1/documents/{id}/graph` | 下载 RDF 实例图 |
| `GET /v1/documents/{id}/schema` | 下载本体 RDF |
| `POST /v1/documents/{id}/sparql` | 只读 SELECT 查询 |
| `GET /v1/documents/{id}/describe` | 查看资源属性 |
| `DELETE /v1/documents/{id}` | 删除文档、任务记录和产物 |
| `POST /mcp/` | MCP Streamable HTTP 入口 |

列表只返回最近最多 100 条，不提供翻页；业务系统应保存上传响应中的 `document_id`。

## 上传文件

`POST /v1/documents`，`multipart/form-data`，字段名为 `file`。建议带 `Idempotency-Key`，同一业务文件重试时使用相同值；同一键对应不同文件内容会返回 `409`。

```bash
curl -X POST "$BASE_URL/v1/documents" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H "Idempotency-Key: order-manual-2026-v1" \
  -F "file=@manual.pdf"
```

成功返回 HTTP `202`，例如：

响应头 `Location: /v1/documents/{id}` 指向状态接口，`Retry-After: 5` 给出建议的首次轮询间隔。

```json
{"id":"6e4c10c2-4862-461c-9878-dac468bd840e","filename":"manual.pdf","sha256":"...","status":"queued","stage":null,"error":null,"attempts":0,"ontology_version":null,"created_at":"2026-11-04T00:00:00+00:00","updated_at":"2026-11-04T00:00:00+00:00"}
```

业务系统保存 `id` 作为 `document_id`。同一幂等键和相同内容再次提交会返回原 `id` 及其当前状态；不同内容返回 `409`。幂等键限 1–128 个无空格的可打印 ASCII 字符。文件上限 4 MiB；支持 `.md`、`.txt`、`.html`、`.htm`、`.epub`、`.pdf`。

## 跟踪任务

`GET /v1/documents/{document_id}` 返回 `queued`、`processing`、`needs_review`、`completed` 或 `failed`，并带 `stage`、`attempts`、`error`。每 5–15 秒轮询；到 `needs_review` 后检查质量报告和候选图，人工确认后发布，状态才变成 `completed`。若为 `failed`，查看 `error`，修复配置后调用 `POST /v1/documents/{document_id}/retry`。`GET /v1/documents?limit=50` 列出最近任务。

自动重试最多 3 次。重试期间状态回到 `queued`，`lease_until` 未到时 worker 不会重复领取。业务系统应以 `status` 为准，不依据 `attempts` 判断完成。

## 获取图与查询

`GET /v1/documents/{document_id}/graph` 返回该文件的实例与出处 Turtle RDF；`GET /v1/documents/{document_id}/schema` 返回抽取时使用的共享本体 Turtle RDF。两者在 `needs_review` 和 `completed` 状态可用。状态中的 `ontology_version=legal-audit-v1` 表示这份图已用当前共享本体重新抽取；旧图没有版本号，不会进入跨文件查询。调用 `POST /v1/documents/{document_id}/reprocess` 可从已保存的原文重新抽取，新结果重新进入 `needs_review`。

## 复核、修正与发布

新文件抽取完成后**不会自动进入跨文件领域图谱**。业务系统先调用 `GET /v1/documents/{id}/quality` 查看 `metrics`、`issue_counts`、`examples` 和 `extraction_examples`，重点检查缺少原文引用、笼统名称、孤立实体、未核实关系、条款编号和模型遗漏的文本块。再用单文件图接口或 SPARQL 对照原文复核。报告是排查线索，计数为 0 也不能证明语义完全正确。此前已发布的文档保持 `completed`；其质量接口会实时生成报告并标记 `legacy_published: true`。

如需修改，先下载完整 Turtle 图及响应头 `ETag`，编辑实体、关系、属性及原文引用，再以 `Content-Type: text/turtle` 和 `If-Match: <ETag>` 调用 `PUT /v1/documents/{id}/graph`。服务器会检查 Turtle 语法和领域本体约束，保存旧版图，返回新的 `needs_review` 状态；并发修改时返回 `412`，缺少 ETag 返回 `428`。修正后的图仍需重新复核和发布。此接口提交的是**完整替换图**，不是局部补丁。

```bash
curl -D graph.headers -o graph.ttl "$BASE_URL/v1/documents/$DOCUMENT_ID/graph" -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY"
# 从 graph.headers 取 ETag；编辑 graph.ttl 后提交：
curl -X PUT "$BASE_URL/v1/documents/$DOCUMENT_ID/graph" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H 'Content-Type: text/turtle' -H 'If-Match: <上一步的ETag>' \
  --data-binary @graph.ttl
```

确认图和出处后发布：

```bash
curl -X POST "$BASE_URL/v1/documents/$DOCUMENT_ID/publish" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"note":"已核对原文、实体与关系"}'
```

`note` 长度 3–1000 字。仅 `completed` 的图会进入 `/v1/knowledge-graph/*` 与 MCP 跨文件查询。对已发布图提交修正或重新抽取时，该文档暂时退出跨文件结果，重新发布后恢复。业务系统应同时保存 `document_id`、审核人和审核记录；当前服务仅记录审核时间与备注，使用同一服务密钥，不能识别具体审核人。

`GET /v1/ontology` 返回类、对象关系、数据属性、中文释义、版本与命名空间。此本体在所有文件间稳定复用；文档只提供实例事实和原文依据。`GET /v1/knowledge-graph/summary` 返回当前版本纳入的文档数、实体数和三元组数。跨文件 SELECT 示例：

```bash
curl -X POST "$BASE_URL/v1/knowledge-graph/sparql" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H "Content-Type: application/sparql-query" \
  --data-binary 'PREFIX la: <https://mini-ontorag.vercel.app/ontology/legal-audit/v1/> SELECT ?entity ?name WHERE { ?entity a la:Role ; <http://www.w3.org/2000/01/rdf-schema#label> ?name } LIMIT 20'
```

当前跨文件查询最多聚合 100 份已完成且本体版本匹配的文件；超过上限接口返回错误，需要扩展查询存储方案。实体 IRI 仍按来源块生成，同名实体尚未自动合并，业务系统不能仅凭同名认定同一主体。

`DELETE /v1/documents/{document_id}` 删除任务记录、原始文件与生成的图，成功返回 `204`。处理中的文档返回 `409`，请等任务完成或失败后重试。删除后同一 `document_id` 的查询返回 `404`。

`POST /v1/documents/{document_id}/sparql` 请求体放 SPARQL 文本，`Content-Type: application/sparql-query`；仅允许 SELECT。例如：

```bash
curl -X POST "$BASE_URL/v1/documents/$DOCUMENT_ID/sparql" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H "Content-Type: application/sparql-query" \
  --data-binary 'SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20'
```

`GET /v1/documents/{document_id}/describe?iri=<URL编码的IRI>` 返回某资源的出边。

## MCP

`get_document_quality` 可读取单份文档的质量报告；待复核图只进入单文件查询，发布后才进入 `sparql_select_domain`。发布和修正必须调用上面的 REST 接口。

Streamable HTTP MCP 端点为 `BASE_URL/mcp/`。请求头同样使用 Bearer 密钥。客户端配置示意：

```json
{
  "mcpServers": {
    "ontorag": {
      "url": "https://YOUR_DOMAIN/mcp/",
      "headers": { "Authorization": "Bearer YOUR_SERVICE_API_KEY" }
    }
  }
}
```

实际客户端配置格式可能不同，但 URL 与 Authorization 头保持一致。工具包括 `list_knowledge_documents`、`get_knowledge_document`、`get_domain_ontology`、`get_domain_graph_summary`、`sparql_select_domain`、`sparql_select`、`describe_resource`。单文件的 `sparql_select` 与 `describe_resource` 需要 `document_id`，`sparql_select_domain` 面向所有已纳入共享本体的文件，只传 `query`。例如：

```json
{"document_id":"6e4c10c2-4862-461c-9878-dac468bd840e","query":"SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20"}
```

文件上传走 REST；MCP 用于读取任务状态和查询知识图谱。MCP 使用 Streamable HTTP，客户端需支持给 MCP 请求附加 Bearer 请求头。

## 错误与权限

修正图缺少 `If-Match` 返回 `428`，图已发生并发更新返回 `412`；不合法的 Turtle 或领域本体断言返回 `422`。发布时状态不为 `needs_review` 返回 `409`。

`401` 密钥缺失或错误；`413` 文件超限；`415` 格式不支持；`409` 幂等冲突、重试状态不符或图尚未完成；`400` 幂等键不合法或非 SELECT 查询；`404` 文档不存在；`422` 请求字段缺失。REST 错误响应使用 `detail` 字段。`/health` 只表示 API 进程可响应；成功抽取还依赖 worker、Postgres、Blob 和模型 API。私有 Blob 中的原文和 RDF 不直接公开；调用方经受保护 API 获取结果。

网络错误和 `5xx` 可以用同一个 `Idempotency-Key` 重试上传。MCP 工具失败以 MCP 错误结果返回，业务系统应记录工具名、`document_id` 和错误文本。当前没有 webhook，使用状态轮询；没有按调用方细分权限或内置限流，密钥泄露后持有者可访问并删除全部文档。
