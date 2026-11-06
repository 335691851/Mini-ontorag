import unittest

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF, RDFS

from ontorag_service.domain import PROVENANCE, canonical_entity_iri, canonicalize_domain_entities, normalize_policy_clauses, resolve_reference_numbers, schema_card, validate_instance_graph


class ClauseNormalizationTest(unittest.TestCase):
    def test_number_is_provenance_and_business_fact_keeps_evidence(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        graph.add((ns.clause1, RDF.type, ns.PolicyClause))
        graph.add((ns.clause1, RDFS.label, Literal("第十二条")))
        graph.add((ns.clause1, ns.clauseNumber, Literal("第十二条")))
        graph.add((ns.clause1, ns.clauseText, Literal("合同签订前必须完成法律审查。")))
        graph.add((ns.clause1, ns.imposesObligation, ns.duty1))
        graph.add((ns.clause1, PROVENANCE.hasMention, ns.mention1))
        graph.add((ns.mention1, PROVENANCE.mentions, ns.clause1))
        graph.add((ns.clause1, PROVENANCE.attestedIn, ns.source1))
        graph.add((ns.duty1, RDF.type, ns.Obligation))
        graph.add((ns.rule1, RDF.type, ns.PolicyClause))
        graph.add((ns.rule1, RDFS.label, Literal("合同签订前必须审查")))

        self.assertEqual(normalize_policy_clauses(graph), 1)
        self.assertEqual(normalize_policy_clauses(graph), 0)
        self.assertIn((ns.clause1, RDF.type, ns.PolicyClause), graph)
        self.assertIn((ns.clause1, ns.imposesObligation, ns.duty1), graph)
        self.assertEqual(str(graph.value(ns.clause1, RDFS.label)), "合同签订前必须完成法律审查。")
        self.assertIn((ns.duty1, PROVENANCE.hasMention, ns.mention1), graph)
        self.assertIn((ns.duty1, PROVENANCE.sourceClauseNumber, Literal("第十二条")), graph)
        self.assertIn((ns.mention1, PROVENANCE.mentions, ns.duty1), graph)
        self.assertIn((ns.duty1, PROVENANCE.attestedIn, ns.source1), graph)
        self.assertIn((ns.rule1, RDF.type, ns.PolicyClause), graph)
        validate_instance_graph(graph)

    def test_ambiguous_numbers_are_not_assigned_to_business_entity(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        graph.add((ns.clause1, RDF.type, ns.PolicyClause))
        graph.add((ns.clause1, ns.clauseNumber, Literal("第一条")))
        graph.add((ns.clause1, ns.clauseNumber, Literal("第二条")))
        graph.add((ns.clause1, ns.imposesObligation, ns.duty1))
        graph.add((ns.clause1, PROVENANCE.hasMention, ns.mention1))
        graph.add((ns.duty1, RDF.type, ns.Obligation))

        self.assertEqual(normalize_policy_clauses(graph), 1)
        self.assertFalse(list(graph.objects(ns.duty1, PROVENANCE.sourceClauseNumber)))
        self.assertEqual({str(value) for value in graph.objects(ns.mention1, PROVENANCE.clauseNumber)}, {"第一条", "第二条"})

    def test_incompatible_relation_is_quarantined(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        graph.add((ns.rule1, RDF.type, ns.PolicyClause))
        graph.add((ns.process1, RDF.type, ns.Process))
        graph.add((ns.rule1, ns.governsActivity, ns.process1))

        self.assertEqual(normalize_policy_clauses(graph), 1)
        self.assertNotIn((ns.rule1, ns.governsActivity, ns.process1), graph)
        assertions = list(graph.objects(ns.rule1, PROVENANCE.hasUnverifiedRelation))
        self.assertEqual(len(assertions), 1)
        self.assertIn((assertions[0], RDF.predicate, ns.governsActivity), graph)
        self.assertIn((assertions[0], RDF.object, ns.process1), graph)
        validate_instance_graph(graph)

    def test_document_number_is_resolved_from_matching_filename(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        graph.add((ns.doc1, RDF.type, ns.PolicyDocument))
        graph.add((ns.doc1, RDFS.label, Literal("合同管理办法")))
        graph.add((ns.doc1, ns.referenceNumber, Literal("鲁银发〔2024〕16号")))
        graph.add((ns.doc1, ns.referenceNumber, Literal("鲁银发〔2025〕26号")))

        self.assertEqual(resolve_reference_numbers(graph, "关于印发《合同管理办法》的通知（鲁银发〔2025〕26号）.pdf"), 1)
        self.assertEqual([str(x) for x in graph.objects(ns.doc1, ns.referenceNumber)], ["鲁银发〔2025〕26号"])
        self.assertEqual([str(x) for x in graph.objects(ns.doc1, PROVENANCE.unresolvedReferenceNumber)], ["鲁银发〔2024〕16号"])

    def test_only_curated_full_organization_name_is_merged(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        for name in (ns.org1, ns.org2):
            graph.add((name, RDF.type, ns.Organization))
            graph.add((name, RDFS.label, Literal("鲁银投资集团股份有限公司")))
        graph.add((ns.role1, RDF.type, ns.Role))
        graph.add((ns.role1, RDFS.label, Literal("权属企业")))
        graph.add((ns.responsibility1, RDF.type, ns.Responsibility))
        graph.add((ns.responsibility1, ns.responsibilityOfOrganization, ns.org1))

        self.assertEqual(canonicalize_domain_entities(graph), 2)
        canonical = canonical_entity_iri("Organization", "鲁银投资集团股份有限公司")
        self.assertIn((canonical, RDF.type, ns.Organization), graph)
        self.assertIn((ns.responsibility1, ns.responsibilityOfOrganization, canonical), graph)
        self.assertIn((ns.org1, OWL.sameAs, canonical), graph)
        self.assertIsNone(canonical_entity_iri("Organization", "权属企业"))
        self.assertEqual(canonicalize_domain_entities(graph), 0)


if __name__ == "__main__":
    unittest.main()
