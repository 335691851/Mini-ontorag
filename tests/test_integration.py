import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class IntegrationTest(unittest.TestCase):
    def test_numeric_ontorag_schema_prefix_is_repaired(self):
        from rdflib import Graph
        from ontorag_service.ontology import valid_schema_turtle

        document_id = "8063712e-c264-58c7-a118-7886532ad044"
        slug = document_id.replace("-", "")
        bad = f"@prefix {slug}: <https://example.org/> .\n{slug}:Rule a <http://www.w3.org/2000/01/rdf-schema#Class> .".encode()
        fixed = valid_schema_turtle(bad, document_id)
        self.assertIn(f"@prefix d{slug}:".encode(), fixed)
        graph = Graph()
        graph.parse(data=fixed.decode(), format="turtle")
        self.assertEqual(len(graph), 1)

    def test_upload_worker_query_and_mcp(self):
        with tempfile.TemporaryDirectory() as temp:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp) / 'test.db'}"
            os.environ["STORAGE_BACKEND"] = "local"
            os.environ["ONTORAG_SERVICE_API_KEYS"] = "test-secret,rotated-secret"
            os.environ["OPENROUTER_API_KEY"] = "test-key"
            from app import app
            from ontorag_service import worker
            from ontorag_service.domain import schema_card, schema_turtle
            namespace = schema_card()["namespace"]

            def fake_stage(_cli, root, stage, *args):
                target = root / "ontology"
                target.mkdir(exist_ok=True)
                if stage == "ingest":
                    chunks = root / "dto" / "chunks"
                    chunks.mkdir(parents=True)
                    (chunks / "demo.jsonl").write_text('{"chunk_id":"demo"}\n', encoding="utf-8")
                else:
                    (target / "world.ttl").write_text(f"@prefix la: <{namespace}> . la:alice a la:Role .", encoding="utf-8")

            with TestClient(app) as client:
                headers = {"Authorization": "Bearer test-secret"}
                self.assertEqual(client.get("/v1/documents").status_code, 401)
                self.assertEqual(client.get("/v1/documents", headers={"Authorization": "Bearer rotated-secret"}).status_code, 200)
                uploaded = client.post("/v1/documents", headers={**headers, "Idempotency-Key": "sample-1"}, files={"file": ("sample.md", b"Alice is a thing.", "text/markdown")})
                self.assertEqual(uploaded.status_code, 202)
                doc_id = uploaded.json()["id"]
                self.assertEqual(uploaded.headers["Location"], f"/v1/documents/{doc_id}")
                self.assertEqual(uploaded.headers["Cache-Control"], "no-store")
                again = client.post("/v1/documents", headers={**headers, "Idempotency-Key": "sample-1"}, files={"file": ("sample.md", b"Alice is a thing.", "text/markdown")})
                self.assertEqual(again.json()["id"], doc_id)
                with patch.object(worker, "_cli", return_value="ontorag"), patch.object(worker, "_run_cli", side_effect=fake_stage):
                    self.assertTrue(worker.run_once())
                self.assertEqual(client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"], "needs_review")
                quality = client.get(f"/v1/documents/{doc_id}/quality", headers=headers)
                self.assertEqual(quality.status_code, 200)
                self.assertEqual(quality.json()["metrics"]["entities"], 1)
                self.assertEqual(client.get("/v1/knowledge-graph/summary", headers=headers).json()["documents"], 0)
                self.assertEqual(client.post(f"/v1/documents/{doc_id}/publish", headers=headers, json={"note": "Reviewed source quote and entity"}).status_code, 200)
                self.assertEqual(client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"], "completed")
                graph_response = client.get(f"/v1/documents/{doc_id}/graph", headers=headers)
                self.assertTrue(graph_response.headers.get("ETag"))
                corrected = client.put(f"/v1/documents/{doc_id}/graph", headers={**headers, "Content-Type": "text/turtle", "If-Match": graph_response.headers["ETag"]}, content=graph_response.content)
                self.assertEqual(corrected.status_code, 200)
                self.assertEqual(corrected.json()["status"], "needs_review")
                self.assertEqual(client.get("/v1/knowledge-graph/summary", headers=headers).json()["documents"], 0)
                self.assertEqual(client.put(f"/v1/documents/{doc_id}/graph", headers={**headers, "Content-Type": "text/turtle", "If-Match": graph_response.headers["ETag"]}, content=graph_response.content).status_code, 412)
                self.assertEqual(client.post(f"/v1/documents/{doc_id}/publish", headers=headers, json={"note": "Approved corrected Turtle graph"}).status_code, 200)
                self.assertEqual(client.get(f"/v1/documents/{doc_id}", headers=headers).json()["ontology_version"], "legal-audit-v1")
                self.assertIn("alice", client.get(f"/v1/documents/{doc_id}/graph", headers=headers).text)
                self.assertIn("Role", client.get(f"/v1/documents/{doc_id}/schema", headers=headers).text)
                self.assertIn("Role", client.get("/v1/ontology/ttl", headers=headers).text)
                result = client.post(f"/v1/documents/{doc_id}/sparql", headers=headers, content=f"SELECT ?s WHERE {{ ?s a <{namespace}Role> }}")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(len(result.json()["results"]["bindings"]), 1)
                bad = client.post(f"/v1/documents/{doc_id}/sparql", headers=headers, content="DELETE WHERE { ?s ?p ?o }")
                self.assertEqual(bad.status_code, 400)
                mcp_headers = {**headers, "Accept": "application/json, text/event-stream"}
                init = client.post("/mcp/", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
                self.assertEqual(init.status_code, 200)
                tools = client.post("/mcp/", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                self.assertEqual(tools.status_code, 200)
                self.assertIn("sparql_select", tools.text)
                self.assertIn("sparql_select_domain", tools.text)
                openapi = client.get("/openapi.json", headers=headers).json()
                self.assertIn("application/sparql-query", openapi["paths"]["/v1/documents/{document_id}/sparql"]["post"]["requestBody"]["content"])
                self.assertEqual(openapi["paths"]["/v1/documents"]["post"]["security"], [{"ServiceBearer": []}])
                self.assertEqual(client.get("/openapi.json").status_code, 401)
                call = client.post("/mcp/", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sparql_select", "arguments": {"document_id": doc_id, "query": f"SELECT ?s WHERE {{ ?s a <{namespace}Role> }}"}}})
                self.assertEqual(call.status_code, 200)
                self.assertIn("alice", call.text)
                self.assertEqual(client.post("/mcp/", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}).status_code, 401)
                internal = client.post("/v1/documents", headers=headers, files={"file": ("internal.md", b"Worker source", "text/markdown")}).json()["id"]
                claimed_id, internal_token = worker.claim_job()
                self.assertEqual(claimed_id, internal)
                source_url = f"/v1/internal/documents/{internal}/source"
                artifact_url = f"/v1/internal/documents/{internal}/artifacts"
                self.assertEqual(client.get(source_url, headers=headers).status_code, 409)
                internal_headers = {**headers, "X-Worker-Lease": internal_token, "X-Ontology-Version": "legal-audit-v1"}
                self.assertEqual(client.get(source_url, headers=internal_headers).content, b"Worker source")
                self.assertEqual(client.post(artifact_url, headers=headers, files={}).status_code, 422)
                artifacts = client.post(artifact_url, headers=internal_headers, files={
                    "graph": ("world.ttl", f"@prefix la: <{namespace}> . la:bob a la:Role .".encode(), "text/turtle"),
                    "schema": ("schema.ttl", schema_turtle(), "text/turtle"),
                })
                self.assertEqual(artifacts.status_code, 200)
                self.assertEqual(client.get(f"/v1/documents/{internal}", headers=headers).json()["status"], "needs_review")
                self.assertEqual(client.post(f"/v1/documents/{internal}/publish", headers=headers, json={"note": "Reviewed internal sample graph"}).status_code, 200)
                summary = client.get("/v1/knowledge-graph/summary", headers=headers)
                self.assertEqual(summary.json()["documents"], 2)
                domain_query = client.post("/v1/knowledge-graph/sparql", headers=headers, content=f"SELECT ?s WHERE {{ ?s a <{namespace}Role> }}")
                self.assertEqual(len(domain_query.json()["results"]["bindings"]), 2)
                self.assertIn("alice", domain_query.text)
                self.assertEqual(client.get(source_url, headers=internal_headers).status_code, 409)
                self.assertEqual(client.delete(f"/v1/documents/{doc_id}", headers=headers).status_code, 204)
                self.assertEqual(client.get(f"/v1/documents/{doc_id}", headers=headers).status_code, 404)
                another = client.post("/v1/documents", headers=headers, files={"file": ("lease.md", b"Lease test.", "text/markdown")}).json()["id"]
                claimed_id, token = worker.claim_job()
                self.assertEqual(claimed_id, another)
                self.assertTrue(worker.renew_lease(another, token))
                from ontorag_service.db import Document, Session
                with Session.begin() as session:
                    session.get(Document, another).lease_token = "replacement"
                self.assertFalse(worker.renew_lease(another, token))
                with self.assertRaises(RuntimeError):
                    worker.set_stage(another, token, "extract")
                reprocessed = client.post(f"/v1/documents/{internal}/reprocess", headers=headers)
                self.assertEqual(reprocessed.status_code, 202)
                self.assertIsNone(reprocessed.json()["ontology_version"])
            from ontorag_service.db import engine
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
