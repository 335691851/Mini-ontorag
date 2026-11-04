"""Build an offline viewer from downloaded OntoRAG Turtle files."""

import argparse
import csv
import json
import html as html_module
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS


ORP = Namespace("https://ontorag.org/provenance#")
OA = Namespace("http://www.w3.org/ns/oa#")


def name(iri: object) -> str:
    value = str(iri)
    return unquote(value.rsplit("#", 1)[-1].rsplit("/", 1)[-1])


def quoted_evidence(graph: Graph, entity: URIRef) -> list[str]:
    quotes = []
    for mention in graph.objects(entity, ORP.hasMention):
        for target in graph.objects(mention, OA.hasTarget):
            for selector in graph.objects(target, OA.hasSelector):
                for quote in graph.objects(selector, OA.exact):
                    value = str(quote).strip()
                    if value and value not in quotes:
                        quotes.append(value)
    return quotes


def document_data(doc: dict, graph_dir: Path) -> dict:
    document_id = doc["id"]
    graph = Graph()
    schema = Graph()
    graph_path = graph_dir / f"{document_id}.graph.ttl"
    graph_text = graph_path.read_text(encoding="utf-8")
    graph.parse(data=graph_text, format="turtle")
    schema.parse(graph_dir / f"{document_id}.schema.ttl", format="turtle")
    classes = {subject for subject in schema.subjects(RDF.type, OWL.Class) if isinstance(subject, URIRef)}
    if not classes:
        raise ValueError(f"Missing ontology classes in {document_id}.schema.ttl")
    base = str(next(iter(classes))).rsplit("/", 1)[0] + "/"
    entities = {
        subject for subject, _, kind in graph.triples((None, RDF.type, None))
        if isinstance(subject, URIRef) and isinstance(kind, URIRef)
        and kind in classes and str(subject).startswith(base)
    }
    nodes = []
    for entity in sorted(entities, key=str):
        entity_classes = sorted({name(kind) for kind in graph.objects(entity, RDF.type) if str(kind).startswith(base)})
        attrs = [{"name": name(predicate), "value": str(value)} for predicate, value in graph.predicate_objects(entity)
                 if predicate != RDFS.label and str(predicate).startswith(base) and not isinstance(value, URIRef)]
        nodes.append({
            "id": str(entity),
            "label": str(graph.value(entity, RDFS.label) or name(entity)),
            "type": entity_classes[0] if entity_classes else "实体",
            "attributes": attrs,
            "evidence": quoted_evidence(graph, entity),
        })
    edges = [
        {"source": str(subject), "target": str(value), "label": name(predicate), "iri": str(predicate)}
        for subject, predicate, value in graph
        if subject in entities and value in entities and predicate != RDF.type and str(predicate).startswith(base)
    ]
    edges.sort(key=lambda item: (item["source"], item["label"], item["target"]))

    schema_nodes = []
    for class_iri in sorted(classes, key=str):
        data_properties = [name(prop) for prop in schema.subjects(RDFS.domain, class_iri)
                           if (prop, RDF.type, OWL.DatatypeProperty) in schema]
        schema_nodes.append({
            "id": str(class_iri),
            "label": str(schema.value(class_iri, RDFS.label) or name(class_iri)),
            "type": "类",
            "attributes": ([{"name": "定义", "value": str(schema.value(class_iri, RDFS.comment))}] if schema.value(class_iri, RDFS.comment) else [])
                + [{"name": "数据属性", "value": item} for item in sorted(set(data_properties))],
            "evidence": [],
        })
    schema_edges = []
    for prop in schema.subjects(RDF.type, OWL.ObjectProperty):
        domains = [node for node in schema.objects(prop, RDFS.domain) if node in classes]
        ranges = [node for node in schema.objects(prop, RDFS.range) if node in classes]
        for domain in domains:
            for range_ in ranges:
                schema_edges.append({"source": str(domain), "target": str(range_), "label": name(prop), "iri": str(prop)})
    for child in classes:
        for parent in schema.objects(child, RDFS.subClassOf):
            if parent in classes:
                schema_edges.append({"source": str(child), "target": str(parent), "label": "子类属于", "iri": str(RDFS.subClassOf)})
    schema_edges.sort(key=lambda item: (item["source"], item["label"], item["target"]))

    title = Path(doc["source"]).stem
    folder = Path(doc["source"]).parent.name
    return {
        "id": document_id,
        "title": title,
        "folder": folder,
        "counts": {"entities": len(nodes), "relations": len(edges), "classes": len(schema_nodes), "schemaRelations": len(schema_edges)},
        "instances": {"nodes": nodes, "edges": edges},
        "ontology": {"nodes": schema_nodes, "edges": schema_edges},
    }


def combined_instances(docs: list[dict]) -> dict:
    nodes = {}
    edges = {}
    for doc in docs:
        for node in doc["instances"]["nodes"]:
            if node["id"] not in nodes:
                nodes[node["id"]] = {**node, "documents": [doc["title"]]}
            else:
                existing = nodes[node["id"]]
                if doc["title"] not in existing["documents"]:
                    existing["documents"].append(doc["title"])
                for evidence in node["evidence"]:
                    if evidence not in existing["evidence"]:
                        existing["evidence"].append(evidence)
        for edge in doc["instances"]["edges"]:
            edges[(edge["source"], edge["iri"], edge["target"])] = edge
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--d3", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default="制度知识图谱查看器")
    args = parser.parse_args()
    records = list(json.loads(args.manifest.read_text(encoding="utf-8")).values())
    docs = [document_data(record, args.graphs) for record in records]
    if not docs:
        raise ValueError("No documents found in import record")
    domain = docs[0]["ontology"]
    if any(doc["ontology"] != domain for doc in docs[1:]):
        raise ValueError("Documents use different ontologies; reprocess and download all graphs first")
    totals = Counter()
    for doc in docs:
        totals.update(doc["counts"])
    payload = json.dumps({"documents": docs, "domain": domain, "allInstances": combined_instances(docs), "totals": totals}, ensure_ascii=False, separators=(",", ":"))
    d3 = args.d3.read_text(encoding="utf-8")
    template = (Path(__file__).parent / "graph_viewer_template.html").read_text(encoding="utf-8")
    template = template.replace("__VIEWER_TITLE__", html_module.escape(args.title))
    html = template.replace("__D3_BUNDLE__", d3.replace("</script", "<\\/script"))
    html = html.replace("__GRAPH_DATA__", payload.replace("</script", "<\\/script"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    with args.output.with_name("文档索引.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["部门", "制度文件", "document_id", "实体数", "关系数", "本体类数", "本体关系数"])
        for doc in docs:
            counts = doc["counts"]
            writer.writerow([doc["folder"], doc["title"], doc["id"], counts["entities"], counts["relations"], counts["classes"], counts["schemaRelations"]])
    print(f"Wrote {args.output} ({args.output.stat().st_size:,} bytes)")
    print(f"Documents: {len(docs)}; entities: {totals['entities']}; relations: {totals['relations']}; shared classes: {len(domain['nodes'])}")


if __name__ == "__main__":
    main()
