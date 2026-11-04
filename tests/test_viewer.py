import tempfile
import unittest
from pathlib import Path

from rdflib import Graph

from ontorag_service.domain import schema_card, schema_turtle
from scripts.build_graph_viewer import combined_instances, document_data
from scripts.export_domain_graph import export


class ViewerTest(unittest.TestCase):
    def test_documents_share_domain_ontology(self):
        namespace = schema_card()["namespace"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            views = []
            for index in (1, 2):
                document_id = f"document-{index}"
                (root / f"{document_id}.schema.ttl").write_bytes(schema_turtle())
                (root / f"{document_id}.graph.ttl").write_text(
                    f"@prefix la: <{namespace}> . la:role-{index} a la:Role .",
                    encoding="utf-8",
                )
                views.append(document_data({"id": document_id, "source": f"sample-{index}.pdf"}, root))
            self.assertEqual(views[0]["ontology"], views[1]["ontology"])
            self.assertEqual(len(views[0]["ontology"]["nodes"]), 35)
            self.assertEqual(len(views[0]["instances"]["nodes"]), 1)
            self.assertEqual(len(views[1]["instances"]["nodes"]), 1)
            combined = combined_instances(views)
            self.assertEqual(len(combined["nodes"]), 2)
            manifest = root / "manifest.json"
            manifest.write_text('{"one":{"id":"document-1","source":"sample-1.pdf"},"two":{"id":"document-2","source":"sample-2.pdf"}}', encoding="utf-8")
            result = export(manifest, root, root / "export")
            self.assertEqual(result["documents"], 2)
            self.assertEqual(result["entities"], 2)
            self.assertEqual(len(Graph().parse(root / "export" / "领域知识图谱-全集.ttl", format="turtle")), result["triples"])


if __name__ == "__main__":
    unittest.main()
