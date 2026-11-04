# SPDX-License-Identifier: Apache-2.0
"""The vector store on gardend: no vectors leave the graph.

``PostgresVDB`` stores embedded texts with metadata in pgvector and does a
filtered k-NN. Here the same records become triples on their catalog row
(``dcterms:description`` carries the embed text, ``urn:ao:vdb#metadata`` the
record's metadata as JSON, ``urn:ao:vdb#collection`` the collection name), and
retrieval is gardend's ``find`` tool: every entity verbalized into one sentence
and embedded locally with multilingual-e5-small, searched by meaning and by
words. Nothing here calls an embedding endpoint, so the ``EMBED_*`` triplet is
not needed on this backend; ``Retriever`` passes the query text instead of a
vector when the store ``embeds_itself``.

Filters: ``where["label"]`` becomes ``find``'s ``types`` filter (the class as
``find`` labels it, e.g. ``"catalog column"``); other keys are matched against
the stored metadata after the search. ``find`` returns at most 25 hits per
call; ``top_k`` above that is capped and logged.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable

from auto_ontology.rdf.client import GardendError, client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import BASE, DCT_DESCRIPTION, parse_row_iri, row_iri_single

logger = logging.getLogger(__name__)

VDB_NS = "urn:ao:vdb#"
P_METADATA = f"{VDB_NS}metadata"
P_COLLECTION = f"{VDB_NS}collection"
P_TEXT = DCT_DESCRIPTION
_FIND_MAX = 25

#: AO catalog labels → the DM table whose rows carry them.
LABEL_TABLE = {
    "Database": "catalog_database",
    "Schema": "catalog_schema",
    "Table": "catalog_table",
    "Column": "catalog_column",
    "Sql": "sql_query",
    "CustomAnalysis": "custom_analysis",
    "SqlAttribute": "sql_attribute",
    "Term": "term",
    "ColumnAttribute": "column_attribute",
    "PqlAnalysis": "pql_analysis",
}
TABLE_LABEL = {v: k for k, v in LABEL_TABLE.items()}


def _find_type(table: str) -> str:
    """How ``find`` labels a class: the local name with spaces."""
    return table.replace("_", " ")


def _flatten(records: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for record in records:
        if isinstance(record, list):
            yield from _flatten(record)
        elif isinstance(record, dict):
            yield record


class GardendVDB:
    """One collection of embedded records, held in the catalog graph."""

    embeds_itself = True

    def __init__(self, *, collection_name: str, database_name: str | None = None, reset: bool = False, **_: Any) -> None:
        self.collection = collection_name
        self.database_name = database_name
        self._client = client()
        if reset and database_name:
            self.delete_by_database(database_name)

    # -- writes ---------------------------------------------------------------

    def create_index(self, **kwargs: Any) -> str:
        return self.collection

    def _subject_for(self, metadata: dict[str, Any]) -> str:
        node_id = str(metadata.get("id") or "")
        table = LABEL_TABLE.get(str(metadata.get("label") or ""))
        if node_id and table:
            return row_iri_single(table, node_id)
        # A record without a catalog row: its own resource in the vdb namespace.
        return f"{BASE}vdb/{self.collection}/{node_id or abs(hash(json.dumps(metadata, sort_keys=True, default=str)))}"

    def write_to_index(self, records: list, batch_size: int = 500, **kwargs: Any) -> int:
        lines: list[str] = []
        written = 0
        for record in _flatten(records):
            metadata = {k: v for k, v in (record.get("metadata") or {}).items() if k != "embedding"}
            text = record.get("text") or record.get("content") or metadata.get("content")
            if not text:
                continue
            subject = iri(self._subject_for(metadata))
            meta_json = json.dumps(metadata, default=str, ensure_ascii=False)
            lines.append(f"{subject} {iri(P_TEXT)} {lit(str(text))} .")
            lines.append(f"{subject} {iri(P_METADATA)} {lit({'collection': self.collection, 'metadata': metadata}) } .")
            lines.append(f"{subject} {iri(P_COLLECTION)} {lit(self.collection)} .")
            written += 1
            if len(lines) >= batch_size * 3:
                self._client.load("\n".join(lines) + "\n")
                lines = []
        if lines:
            self._client.load("\n".join(lines) + "\n")
        logger.info("GardendVDB.write_to_index: %d record(s) into %s", written, self.collection)
        return written

    def run(self, records: list) -> int:
        self.create_index()
        return self.write_to_index(records)

    def _delete_where(self, extra: str) -> int:
        g = iri(self._client.graph_id and f"urn:mnemosyne:local:graph:{self._client.graph_id}:user:rdf")
        count = int(
            self._client.scalar(
                f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ {wrap(f'?s {iri(P_COLLECTION)} {lit(self.collection)} . {extra}')} }}",
                "n",
            )
            or 0
        )
        if count:
            self._client.update(
                f"DELETE {{ GRAPH {g} {{ ?s {iri(P_TEXT)} ?t . ?s {iri(P_METADATA)} ?m . ?s {iri(P_COLLECTION)} ?c }} }} "
                f"WHERE {{ GRAPH {g} {{ ?s {iri(P_COLLECTION)} ?c . FILTER(?c = {lit(self.collection)}) {extra} "
                f"OPTIONAL {{ ?s {iri(P_TEXT)} ?t }} OPTIONAL {{ ?s {iri(P_METADATA)} ?m }} }} }}"
            )
        return count

    def delete_by_database(self, database_name: str) -> list[str]:
        rows = self._client.select(
            f"SELECT ?s ?m WHERE {{ {wrap(f'?s {iri(P_COLLECTION)} {lit(self.collection)} ; {iri(P_METADATA)} ?m .')} }}"
        )
        hits = [r["s"] for r in rows if (r.get("m") or {}).get("metadata", {}).get("database_name") == database_name]
        if hits:
            self._delete_where(values("s", hits))
        return hits

    def delete_all(self) -> int:
        return self._delete_where("")

    def delete_by_id(self, node_id: str) -> int:
        rows = self._client.select(
            f"SELECT ?s ?m WHERE {{ {wrap(f'?s {iri(P_COLLECTION)} {lit(self.collection)} ; {iri(P_METADATA)} ?m .')} }}"
        )
        hits = [r["s"] for r in rows if str((r.get("m") or {}).get("metadata", {}).get("id")) == str(node_id)]
        return self._delete_where(values("s", hits)) if hits else 0

    # -- reads ----------------------------------------------------------------

    def retrieval(self, queries: list, top_k: int = 10, **kwargs: Any) -> list[list[dict]]:
        """k-NN by *text* (``kwargs["texts"]``); the vectors in ``queries`` are ignored."""
        texts = kwargs.get("texts") or []
        where = dict(kwargs.get("where") or {})
        label = where.pop("label", None)
        types = [_find_type(LABEL_TABLE[label])] if label in LABEL_TABLE else None
        # Over-fetch: find ranks every entity of the graph, and rows of other
        # collections or filtered-out metadata are dropped below.
        k = _FIND_MAX
        if top_k > _FIND_MAX:
            logger.info("GardendVDB.retrieval: top_k %d capped at %d (find's limit)", top_k, _FIND_MAX)
        results: list[list[dict]] = []
        for text in texts:
            try:
                args: dict[str, Any] = {"graphId": self._client.graph_id, "query": text, "k": k}
                if types:
                    args["types"] = types
                found = self._client.mcp_call("find", args)
                hits = found.get("hits") or []
                subjects = [h["iri"] for h in hits]
                meta_by_s: dict[str, dict] = {}
                if subjects:
                    for r in self._client.select(
                        f"SELECT ?s ?m ?t WHERE {{ {wrap(f'{values(chr(115), subjects)} ?s {iri(P_COLLECTION)} {lit(self.collection)} ; {iri(P_METADATA)} ?m . OPTIONAL {{ ?s {iri(P_TEXT)} ?t }}')} }}"
                    ):
                        meta_by_s[r["s"]] = {"metadata": (r.get("m") or {}).get("metadata", {}), "text": r.get("t") or ""}
                out: list[dict] = []
                for h in hits:
                    stored = meta_by_s.get(h["iri"])
                    if stored is None:
                        continue  # a row of another collection, or never embedded
                    metadata = stored["metadata"]
                    if any(str(metadata.get(key)) != str(value) for key, value in where.items()):
                        continue
                    out.append({"text": stored["text"] or h.get("says", ""), "metadata": metadata, "_distance": 1.0 - float(h.get("score", 0.0))})
                results.append(out[:top_k])
            except GardendError as exc:
                logger.error("GardendVDB.retrieval: %s", exc)
                results.append([])
        return results

    def close(self) -> None:
        return None
