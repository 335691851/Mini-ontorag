"""Run OntoRAG extraction with source-grounded proposal checks."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from ontorag import instance_extractor_openrouter as extractor
from ontorag.instances_to_ttl import instance_proposals_to_graph

from .domain import ARTICLE_NUMBER


DOMAIN_RULES = """
Domain requirements for legal and audit制度:
- A PolicyClause label must summarize the substantive rule, never just 第X条; put the article number in clauseNumber.
- Extract each numbered clause separately. Never leave both label and id_hint empty.
- Each instance needs a short verbatim quote from this chunk. If the exact text is absent, omit the candidate.
- Relation target_class must match the ontology property's range or a subclass. Never invent an unnamed target.
- A requirement or risk described in a policy is normative text, not evidence that a real violation or audit finding occurred.
""".strip()

TEXT_FIELDS = {
    "PolicyDocument": "title",
    "PolicyClause": "clauseText",
    "Obligation": "obligationText",
    "ComplianceRequirement": "obligationText",
    "Responsibility": "responsibilityText",
    "Risk": "riskDescription",
    "ControlMeasure": "controlDescription",
    "AuditFinding": "findingDescription",
}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def prepare_proposals(chunks: list[dict], proposals: list[dict], card: dict) -> tuple[list[dict], dict]:
    """Fix empty identities and reject proposals that cannot be grounded."""
    chunks_by_id = {str(chunk.get("chunk_id")): chunk for chunk in chunks}
    classes = {item["name"] for item in card["classes"]}
    properties = {item["name"] for item in card["object_properties"]}
    attributes = {item["name"] for item in card["datatype_properties"]}
    issues: list[dict] = []
    issue_counts = Counter()
    output: list[dict] = []

    def issue(code: str, chunk_id: str, detail: str = "") -> None:
        issue_counts[code] += 1
        if len(issues) < 100:
            issues.append({"code": code, "chunk_id": chunk_id, "detail": detail[:120]})

    for proposal in proposals:
        chunk_id = str(proposal.get("chunk_id") or "")
        source = chunks_by_id.get(chunk_id)
        if source is None:
            issue("unknown_chunk", chunk_id)
            continue
        source_text = _compact(str(source.get("text") or ""))
        cleaned = {"chunk_id": chunk_id, "instances": [], "warnings": proposal.get("warnings") or []}
        for candidate in proposal.get("instances") or []:
            kind = str(candidate.get("class") or "").strip()
            if kind not in classes:
                issue("unknown_class", chunk_id, kind)
                continue
            raw_attributes = candidate.get("attributes") or {}
            attrs = {name: value for name, value in raw_attributes.items() if name in attributes and value is not None}
            label = str(candidate.get("label") or "").strip()
            id_hint = str(candidate.get("id_hint") or "").strip()
            if kind == "PolicyClause" and ARTICLE_NUMBER.fullmatch(label):
                attrs.setdefault("clauseNumber", label)
                label = ""
            if not label:
                label = str(attrs.get(TEXT_FIELDS.get(kind, ""), "") or "").strip()
            valid_mentions = []
            for mention in candidate.get("mentions") or []:
                quote = str(mention.get("quote") or "").strip()
                if quote and _compact(quote) in source_text:
                    valid_mentions.append({**mention, "quote": quote})
                elif quote:
                    issue("quote_not_in_chunk", chunk_id, quote)
            if not label and valid_mentions:
                label = valid_mentions[0]["quote"]
            if kind == "PolicyClause":
                label = re.sub(r"^第\s*[一二三四五六七八九十百千万零〇两0-9]+\s*条[\s:：、.．-]*", "", label).strip()
            if not label and not id_hint:
                issue("missing_identity", chunk_id, kind)
                continue
            if kind == "PolicyClause" and (not label or ARTICLE_NUMBER.fullmatch(label)):
                issue("number_only_clause", chunk_id)
                continue
            if not valid_mentions:
                issue("missing_verbatim_quote", chunk_id, label or id_hint)
            relations = []
            for relation in candidate.get("relations") or []:
                predicate = str(relation.get("predicate") or "").strip()
                target_kind = str(relation.get("target_class") or "").strip()
                target_label = str(relation.get("target_label") or "").strip()
                target_hint = str(relation.get("target_id_hint") or "").strip()
                if predicate not in properties or target_kind not in classes:
                    issue("unknown_relation", chunk_id, predicate)
                    continue
                if not target_label and not target_hint:
                    issue("unnamed_relation_target", chunk_id, predicate)
                    continue
                if target_kind == "PolicyClause" and ARTICLE_NUMBER.fullmatch(target_label):
                    issue("number_only_relation_target", chunk_id, predicate)
                    continue
                relations.append(relation)
            cleaned["instances"].append({**candidate, "label": label[:180], "id_hint": id_hint, "attributes": attrs,
                                         "relations": relations, "mentions": valid_mentions})
        output.append(cleaned)
    return output, {"chunks_total": len(chunks), "chunks_with_proposals": len(output),
                    "issue_counts": dict(issue_counts), "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", required=True, type=Path)
    parser.add_argument("--schema-card", required=True, type=Path)
    parser.add_argument("--out-ttl", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True, type=Path)
    args = parser.parse_args()
    chunks = [json.loads(line) for line in args.chunks.read_text(encoding="utf-8").splitlines() if line.strip()]
    card = json.loads(args.schema_card.read_text(encoding="utf-8"))
    original = extractor.build_instance_prompt
    extractor.build_instance_prompt = lambda chunk, schema: original(chunk, schema) + "\n\n" + DOMAIN_RULES
    try:
        proposals = extractor.extract_instance_chunk_proposals(chunks, card)
    finally:
        extractor.build_instance_prompt = original
    cleaned, diagnostics = prepare_proposals(chunks, proposals, card)
    diagnostics["skipped_chunk_ids"] = list(extractor.LAST_SKIPPED)
    diagnostics["model_warning_count"] = sum(len(item.get("warnings") or []) for item in proposals)
    graph = instance_proposals_to_graph({chunk["chunk_id"]: chunk for chunk in chunks}, cleaned, card["namespace"])
    args.out_ttl.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=args.out_ttl, format="turtle")
    args.diagnostics.write_text(json.dumps(diagnostics, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
