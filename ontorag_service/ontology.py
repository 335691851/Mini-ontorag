"""Compatibility for OntoRAG 0.1.13 schema prefixes based on UUID slugs."""

import re


def valid_schema_turtle(data: bytes, document_id: str) -> bytes:
    slug = document_id.replace("-", "")
    text = data.decode("utf-8")
    if not re.search(rf"(?m)^@prefix\s+{re.escape(slug)}:", text):
        return data
    # Turtle prefix names cannot start with a digit. Older jobs used the
    # document UUID as the dataset slug, including in every prefixed term.
    return re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(slug)}:", f"d{slug}:", text).encode("utf-8")
