# 外部业务系统对接

服务根地址记为 `BASE_URL`。除 `/health` 外，请求都带 `Authorization: Bearer <ONTORAG_SERVICE_API_KEY>`。仅服务端持有密钥，不要放入浏览器或移动端代码。生产环境使用 HTTPS。

## 上传文件

`POST /v1/documents`，`multipart/form-data`，字段名为 `file`。建议带 `Idempotency-Key`，同一业务文件重试时使用相同值；同一键对应不同文件内容会返回 `409`。

```bash
curl -X POST "$BASE_URL/v1/documents" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H "Idempotency-Key: order-manual-2026-v1" \
  -F "file=@manual.pdf"
```

成功返回 HTTP `202` 与 `id`、`status=queued`、`sha256`。业务系统保存 `id` 作为 `document_id`。目前文件上限 4 MiB；支持 `.md`、`.txt`、`.html`、`.htm`、`.epub`、`.pdf`。

## 跟踪任务

`GET /v1/documents/{document_id}` 返回 `queued`、`processing`、`completed` 或 `failed`，并带 `stage`、`attempts`、`error`。每 5–15 秒轮询；到 `completed` 后读取图。若为 `failed`，查看 `error`，修复配置后调用 `POST /v1/documents/{document_id}/retry`。`GET /v1/documents?limit=50` 列出最近任务。

## 获取图与查询

`GET /v1/documents/{document_id}/graph` 返回 Turtle RDF。

`POST /v1/documents/{document_id}/sparql` 请求体放 SPARQL 文本，`Content-Type: application/sparql-query`；仅允许 SELECT。例如：

```bash
curl -X POST "$BASE_URL/v1/documents/$DOCUMENT_ID/sparql" \
  -H "Authorization: Bearer $ONTORAG_SERVICE_API_KEY" \
  -H "Content-Type: application/sparql-query" \
  --data-binary 'SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20'
```

`GET /v1/documents/{document_id}/describe?iri=<URL编码的IRI>` 返回某资源的出边。

## MCP

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

实际客户端配置格式可能不同，但 URL 与 Authorization 头保持一致。工具包括 `list_knowledge_documents`、`get_knowledge_document`、`sparql_select`、`describe_resource`。后两项必须传 `document_id`。

## 错误与权限

`401` 密钥缺失或错误；`413` 文件超限；`415` 格式不支持；`409` 幂等冲突或图尚未完成；`400` 非 SELECT 查询；`404` 文档不存在。错误响应使用 `detail` 字段。单用户部署共享一个密钥，没有多租户隔离或细粒度权限。私有 Blob 中的原文和 RDF 不直接公开；调用方经受保护 API 获取结果。
