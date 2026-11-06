import unittest

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, RDFS

from ontorag_service.domain import PROVENANCE, schema_card
from ontorag_service.extract_instances import prepare_proposals
from ontorag_service.quality import graph_quality_report


class ExtractionQualityTest(unittest.TestCase):
    def test_numbered_clauses_get_distinct_content_labels(self):
        chunks = [{"chunk_id": "one", "text": "第一条 应当审查合同。第二条 应当保存审计证据。"}]
        proposals = [{"chunk_id": "one", "instances": [
            {"class": "PolicyClause", "label": "第一条", "attributes": {"clauseText": "应当审查合同。"},
             "mentions": [{"quote": "第一条 应当审查合同。"}], "relations": []},
            {"class": "PolicyClause", "label": "第二条", "attributes": {"clauseText": "应当保存审计证据。"},
             "mentions": [{"quote": "第二条 应当保存审计证据。"}], "relations": []},
        ]}]

        cleaned, diagnostics = prepare_proposals(chunks, proposals, schema_card())
        instances = cleaned[0]["instances"]
        self.assertEqual([x["label"] for x in instances], ["应当审查合同。", "应当保存审计证据。"])
        self.assertEqual([x["attributes"]["clauseNumber"] for x in instances], ["第一条", "第二条"])
        self.assertEqual(diagnostics["issues"], [])

    def test_unsupported_quote_and_unnamed_target_are_reported(self):
        chunks = [{"chunk_id": "one", "text": "应当审查合同。"}]
        proposals = [{"chunk_id": "one", "instances": [
            {"class": "Obligation", "label": "合同审查义务", "mentions": [{"quote": "不存在的原文"}],
             "relations": [{"predicate": "imposesObligation", "target_class": "Obligation", "target_label": ""}]},
        ]}]

        cleaned, diagnostics = prepare_proposals(chunks, proposals, schema_card())
        self.assertEqual(cleaned[0]["instances"][0]["mentions"], [])
        self.assertEqual(cleaned[0]["instances"][0]["relations"], [])
        self.assertEqual({x["code"] for x in diagnostics["issues"]},
                         {"quote_not_in_chunk", "missing_verbatim_quote", "unnamed_relation_target"})

    def test_report_flags_uncited_and_unverified_entities(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        graph.add((ns.duty1, RDF.type, ns.Obligation))
        graph.add((ns.duty1, RDFS.label, Literal("合同审查义务")))
        graph.add((ns.assertion1, RDF.type, PROVENANCE.UnverifiedRelation))

        report = graph_quality_report(graph, {"chunks_total": 1, "chunks_with_proposals": 1, "issues": []})
        self.assertTrue(report["review_required"])
        self.assertEqual(report["metrics"]["entities"], 1)
        self.assertEqual(report["issue_counts"]["missing_mention"], 1)
        self.assertEqual(report["issue_counts"]["unverified_relation"], 1)


if __name__ == "__main__":
    unittest.main()
