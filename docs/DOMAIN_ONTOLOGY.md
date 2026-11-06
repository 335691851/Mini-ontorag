# 法务与审计共享领域本体（legal-audit-v1）

本体文件为 [`legal_audit_v1.json`](../ontorag_service/resources/legal_audit_v1.json)。它定义全领域共用的 35 个类、37 个对象关系和 15 个数据属性。所有文档使用同一命名空间：`https://mini-ontorag.vercel.app/ontology/legal-audit/v1/`。新文件只抽取符合这套词汇的实例；制度文件本身是实例与证据来源，不再为每份文件创设一套类和关系。

| 领域 | 主要类 | 主要关系 |
| --- | --- | --- |
| 制度与规范依据 | `PolicyDocument`、`PolicyClause`、`Obligation`、`ComplianceRequirement` | `hasClause`、`imposesObligation`、`governsActivity` |
| 组织与职责 | `Organization`、`Department`、`Role`、`Responsibility` | `issuedBy`、`responsibilityOfRole`、`responsibilityOfOrganization` |
| 流程与控制 | `Process`、`ProcessStep`、`Approval`、`Risk`、`ControlMeasure`、`InternalControl` | `hasStep`、`requiresApproval`、`mitigatedBy`、`controlMitigatesRisk` |
| 法务 | `Contract`、`ContractReview`、`LegalDisputeCase`、`LegalOpinion` | `reviewsContract`、`involvesContract`、`handledBy` |
| 审计与整改 | `AuditProject`、`AuditFinding`、`AuditReport`、`RectificationAction`、`AccountabilityAction` | `producesFinding`、`findingEvidence`、`requiresRectification`、`triggersAccountability` |

`PolicyClause` 只用于有实际规则名称的条款；单独的“第一条”“第二条”等编号不是领域实体。抽取后会清除纯编号条款节点；编号明确时作为 `https://ontorag.org/provenance#sourceClauseNumber` 来源属性附到其连接的业务实体上，并把条款的原文引用和来源文件转给这些实体。旧版已存图在读取、SPARQL 查询和离线查看器生成时也执行同一规则，无需重新调用模型。若抽取器把多个编号误放到同一节点，只在原文引用节点保留这些编号，不把无法确定的编号归给业务实体。若该条没有抽出业务实体，编号仍留在原文引用节点，不增加领域实体。另一个文件提到同一类岗位时仍使用 `Role` 这个类，因此可按全领域类型查询。审计发现与整改措施通过 `requiresRectification` 连接，原文引用随实例保留。

## 数据分层

1. **领域本体**：版本化、人工维护的类、关系、属性与中文定义。`GET /v1/ontology` 和 `/v1/ontology/ttl` 返回同一版本。
2. **文件事实**：模型从各份文件抽取的实体、属性、关系。每份文件单独保存 RDF 图，便于追溯和重处理；各图使用相同本体命名空间。
3. **全域查询**：`POST /v1/knowledge-graph/sparql` 在查询时合并当前版本的文件事实与一份领域本体。MCP 工具 `sparql_select_domain` 提供同样的查询能力。

实例 IRI 目前根据来源文本块生成。同一名称在不同文件中可能对应多个实例，系统不会自动声称它们是同一法人、岗位或事项。跨文件归并需要增加企业主数据标识、别名规则与人工审核；仅凭名称相同不得合并。模型提取的是制度中的陈述，不表示实际业务中已发生违规、风险、审计发现或整改。

## 版本和变更

修改类或关系的含义、范围时，应创建新本体版本并重新抽取受影响文件。`Document.ontology_version` 标识每份实例图所用版本；没有版本号的旧版文件图不会进入当前全域查询。业务系统可调用 `POST /v1/documents/{id}/reprocess` 从已保存原文重新抽取。

正式用于法务审查、审计分析前，建议由业务负责人核对类边界、关系方向、制度条款定位和原文引用，并建立领域词汇变更记录。
