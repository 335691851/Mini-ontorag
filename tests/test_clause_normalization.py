import unittest

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, RDFS

from ontorag_service.domain import PROVENANCE, remove_number_only_clauses, schema_card, validate_instance_graph


class ClauseNormalizationTest(unittest.TestCase):
    def test_number_is_provenance_and_business_fact_keeps_evidence(self):
        ns = Namespace(schema_card()["namespace"])
        graph = Graph()
        graph.add((ns.clause1, RDF.type, ns.PolicyClause))
        graph.add((ns.clause1, RDFS.label, Literal("第十二条")))
        graph.add((ns.clause1, ns.clauseNumber, Literal("第十二条")))
        graph.add((ns.clause1, ns.imposesObligation, ns.duty1))
        graph.add((ns.clause1, PROVENANCE.hasMention, ns.mention1))
        graph.add((ns.mention1, PROVENANCE.mentions, ns.clause1))
        graph.add((ns.clause1, PROVENANCE.attestedIn, ns.source1))
        graph.add((ns.duty1, RDF.type, ns.Obligation))
        graph.add((ns.rule1, RDF.type, ns.PolicyClause))
        graph.add((ns.rule1, RDFS.label, Literal("合同签订前必须审查")))

        self.assertEqual(remove_number_only_clauses(graph), 1)
        self.assertEqual(remove_number_only_clauses(graph), 0)
        self.assertFalse(list(graph.triples((ns.clause1, None, None))))
        self.assertFalse(list(graph.triples((None, None, ns.clause1))))
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

        self.assertEqual(remove_number_only_clauses(graph), 1)
        self.assertFalse(list(graph.objects(ns.duty1, PROVENANCE.sourceClauseNumber)))
        self.assertEqual({str(value) for value in graph.objects(ns.mention1, PROVENANCE.clauseNumber)}, {"第一条", "第二条"})


if __name__ == "__main__":
    unittest.main()
