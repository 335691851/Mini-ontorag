"""Export one corpus graph and tabular entities/relations from downloaded TTLs."""

import argparse
import csv
import json
from pathlib import Path

from rdflib import Graph

from scripts.build_graph_viewer import combined_instances, document_data


def export(manifest: Path, graph_dir: Path, output_dir: Path) -> dict:
    records = list(json.loads(manifest.read_text(encoding="utf-8")).values())
    if not records:
        raise ValueError("No documents found in import record")
    output_dir.mkdir(parents=True, exist_ok=True)
    graph = Graph()
    schema = None
    docs = []
    for record in records:
        document_id = record["id"]
        current_schema = Graph().parse(graph_dir / f"{document_id}.schema.ttl", format="turtle")
        if schema is None:
            schema = current_schema
            graph += schema
        elif set(current_schema) != set(schema):
            raise ValueError(f"Different ontology in document {document_id}")
        graph.parse(graph_dir / f"{document_id}.graph.ttl", format="turtle")
        docs.append(document_data(record, graph_dir))
    instances = combined_instances(docs)
    graph.serialize(destination=output_dir / "领域知识图谱-全集.ttl", format="turtle")
    schema.serialize(destination=output_dir / "领域本体.ttl", format="turtle")
    with (output_dir / "领域实体.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["实体IRI", "名称", "类型", "来源文件", "原文引用数"])
        for node in instances["nodes"]:
            writer.writerow([node["id"], node["label"], node["type"], "；".join(node["documents"]), len(node["evidence"])])
    with (output_dir / "领域关系.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["源实体IRI", "关系IRI", "关系名称", "目标实体IRI"])
        for edge in instances["edges"]:
            writer.writerow([edge["source"], edge["iri"], edge["label"], edge["target"]])
    return {"documents": len(docs), "triples": len(graph), "schema_triples": len(schema), "entities": len(instances["nodes"]), "relations": len(instances["edges"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.manifest, args.graphs, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
