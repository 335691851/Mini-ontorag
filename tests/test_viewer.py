import tempfile
import unittest
from pathlib import Path

from ontorag_service.domain import schema_card, schema_turtle
from scripts.build_graph_viewer import document_data


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


if __name__ == "__main__":
    unittest.main()
