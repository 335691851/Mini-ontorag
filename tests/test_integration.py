import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class IntegrationTest(unittest.TestCase):
    def test_upload_worker_query_and_mcp(self):
        with tempfile.TemporaryDirectory() as temp:
            os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp) / 'test.db'}"
            os.environ["STORAGE_BACKEND"] = "local"
            os.environ["ONTORAG_SERVICE_API_KEYS"] = "test-secret,rotated-secret"
            os.environ["OPENROUTER_API_KEY"] = "test-key"
            from app import app
            from ontorag_service import worker

            def fake_stage(_cli, stage, root):
                target = root / "ontology"
                target.mkdir(exist_ok=True)
                if stage == "propose":
                    (target / "proposals.raw.jsonl").write_text('{"chunk_id":"demo"}\n', encoding="utf-8")
                else:
                    (target / "schema.ttl").write_text("@prefix ex: <https://example.org/> . ex:Thing a <http://www.w3.org/2000/01/rdf-schema#Class> .", encoding="utf-8")
                    (target / "world.ttl").write_text("@prefix ex: <https://example.org/> . ex:alice a ex:Thing .", encoding="utf-8")

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
                with patch.object(worker, "_cli", return_value="ontorag"), patch.object(worker, "_run_stage", side_effect=fake_stage):
                    self.assertTrue(worker.run_once())
                self.assertEqual(client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"], "completed")
                self.assertIn("alice", client.get(f"/v1/documents/{doc_id}/graph", headers=headers).text)
                self.assertIn("Thing", client.get(f"/v1/documents/{doc_id}/schema", headers=headers).text)
                result = client.post(f"/v1/documents/{doc_id}/sparql", headers=headers, content="SELECT ?s WHERE { ?s a <https://example.org/Thing> }")
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
                openapi = client.get("/openapi.json", headers=headers).json()
                self.assertIn("application/sparql-query", openapi["paths"]["/v1/documents/{document_id}/sparql"]["post"]["requestBody"]["content"])
                self.assertEqual(openapi["paths"]["/v1/documents"]["post"]["security"], [{"ServiceBearer": []}])
                self.assertEqual(client.get("/openapi.json").status_code, 401)
                call = client.post("/mcp/", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sparql_select", "arguments": {"document_id": doc_id, "query": "SELECT ?s WHERE { ?s a <https://example.org/Thing> }"}}})
                self.assertEqual(call.status_code, 200)
                self.assertIn("alice", call.text)
                self.assertEqual(client.post("/mcp/", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}).status_code, 401)
                internal = client.post("/v1/documents", headers=headers, files={"file": ("internal.md", b"Worker source", "text/markdown")}).json()["id"]
                claimed_id, internal_token = worker.claim_job()
                self.assertEqual(claimed_id, internal)
                source_url = f"/v1/internal/documents/{internal}/source"
                artifact_url = f"/v1/internal/documents/{internal}/artifacts"
                self.assertEqual(client.get(source_url, headers=headers).status_code, 409)
                internal_headers = {**headers, "X-Worker-Lease": internal_token}
                self.assertEqual(client.get(source_url, headers=internal_headers).content, b"Worker source")
                self.assertEqual(client.post(artifact_url, headers=headers, files={}).status_code, 422)
                artifacts = client.post(artifact_url, headers=internal_headers, files={
                    "graph": ("world.ttl", b"@prefix ex: <https://example.org/> . ex:bob a ex:Thing .", "text/turtle"),
                    "schema": ("schema.ttl", b"@prefix ex: <https://example.org/> . ex:Thing a <http://www.w3.org/2000/01/rdf-schema#Class> .", "text/turtle"),
                })
                self.assertEqual(artifacts.status_code, 200)
                self.assertEqual(client.get(f"/v1/documents/{internal}", headers=headers).json()["status"], "completed")
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
            from ontorag_service.db import engine
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
