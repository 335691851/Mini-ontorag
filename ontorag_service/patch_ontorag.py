"""Apply the OntoRAG 0.1.13 provenance variable fix at image build time."""

from pathlib import Path

import ontorag.instances_to_ttl as instances_to_ttl


def main() -> None:
    path = Path(instances_to_ttl.__file__)
    original = path.read_text(encoding="utf-8")
    before = "source, file_, chunk = _describe_origin(g, namespace, chunk, chunk_id, described)"
    after = "source, file_, chunk_node = _describe_origin(g, namespace, chunk, chunk_id, described)"
    mention_before = "_add_mention(g, namespace, s, quote, file_, chunk, prov)"
    mention_after = "_add_mention(g, namespace, s, quote, file_, chunk_node, prov)"
    if after in original and mention_after in original:
        return
    if original.count(before) != 1 or original.count(mention_before) != 1:
        raise RuntimeError(f"Unexpected OntoRAG instances_to_ttl source: {path}")
    path.write_text(
        original.replace(before, after).replace(mention_before, mention_after),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
