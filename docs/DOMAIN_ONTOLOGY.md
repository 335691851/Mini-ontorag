# 法务与审计共享领域本体（legal-audit-v1）

本体文件为 [`legal_audit_v1.json`](../ontorag_service/resources/legal_audit_v1.json)。它定义全领域共用的 35 个类、37 个对象关系和 15 个数据属性。所有文档使用同一命名空间：`https://mini-ontorag.vercel.app/ontology/legal-audit/v1/`。新文件只抽取符合这套词汇的实例；制度文件本身是实例与证据来源，不再为每份文件创设一套类和关系。

| 领域 | 主要类 | 主要关系 |
| --- | --- | --- |
| 制度与规范依据 | `PolicyDocument`、`PolicyClause`、`Obligation`、`ComplianceRequirement` | `hasClause`、`imposesObligation`、`governsActivity` |
| 组织与职责 | `Organization`、`Department`、`Role`、`Responsibility` | `issuedBy`、`responsibilityOfRole`、`responsibilityOfOrganization` |
| 流程与控制 | `Process`、`ProcessStep`、`Approval`、`Risk`、`ControlMeasure`、`InternalControl` | `hasStep`、`requiresApproval`、`mitigatedBy`、`controlMitigatesRisk` |
| 法务 | `Contract`、`ContractReview`、`LegalDisputeCase`、`LegalOpinion` | `reviewsContract`、`involvesContract`、`handledBy` |
| 审计与整改 | `AuditProject`、`AuditFinding`、`AuditReport`、`RectificationAction`、`AccountabilityAction` | `producesFinding`、`findingEvidence`、`requiresRectification`、`triggersAccountability` |

`PolicyClause` 表示有正文内容的制度规则，“第一条”“第二条”等编号只是出处属性。抽取器若用编号作节点名称，系统会用唯一的条款正文重新命名规则实体，保留原有关联关系；编号记录在 `clauseNumber` 和 `https://ontorag.org/provenance#sourceClauseNumber`，原文引用也关联到业务目标。若一个节点混合多条正文和编号，系统不猜测它们之间的对应关系，而将编号留在引用节点并移除有歧义的领域节点。关系目标类型不符合本体时，原断言记录为 `https://ontorag.org/provenance#UnverifiedRelation`，不当作已确认的领域关系。旧图在读取、SPARQL 查询和离线导出时执行相同规则，无需重新调用模型。

## 数据分层

1. **领域本体**：版本化、人工维护的类、关系、属性与中文定义。`GET /v1/ontology` 和 `/v1/ontology/ttl` 返回同一版本。
2. **文件事实**：模型从各份文件抽取的实体、属性、关系。每份文件单独保存 RDF 图，便于追溯和重处理；各图使用相同本体命名空间。
3. **全域查询**：`POST /v1/knowledge-graph/sparql` 在查询时合并当前版本的文件事实与一份领域本体。MCP 工具 `sparql_select_domain` 提供同样的查询能力。

实例 IRI 目前根据来源文本块生成。跨文件全域查询仅对本批制度中完整法人名称“鲁银投资集团股份有限公司”的精确匹配统一身份，并为原 IRI 保留 `owl:sameAs`；每份文档图仍保留自身 IRI 和引用。其余同名实体（尤其“权属企业”等泛称）不自动合并，仍需企业主数据标识与人工消歧。同一份文档图中，标题与规范化文号均相同的 `PolicyDocument` 节点会合并；不同年份、文号的同名制度仍分开。多个文号同时挂在一份制度上时，只有文件名与制度名称都明确支持的文号会保留为 `referenceNumber`，其他值转为待核查的出处属性。模型提取的是制度中的陈述，不表示实际业务中已发生违规、风险、审计发现或整改。

## 版本和变更

修改类或关系的含义、范围时，应创建新本体版本并重新抽取受影响文件。`Document.ontology_version` 标识每份实例图所用版本；没有版本号的旧版文件图不会进入当前全域查询。业务系统可调用 `POST /v1/documents/{id}/reprocess` 从已保存原文重新抽取。

正式用于法务审查、审计分析前，建议由业务负责人核对类边界、关系方向、制度条款定位和原文引用，并建立领域词汇变更记录。
