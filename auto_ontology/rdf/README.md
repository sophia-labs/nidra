# The RDF ontology adapter (Sophia Labs fork)

Upstream keeps the catalog, the labeled property graph it ported from Neo4j, in
Postgres and reads it through `auto_ontology.dal`. This package lets the same
DAL functions read the catalog from a **gardend** graph instead:

- `vocab.py`: the IRI scheme. A W3C Direct Mapping of upstream's own
  SQLAlchemy metadata (`auto_ontology.dal.schema.METADATA`): a row of table `T`
  with primary key `id=X` is `<urn:ao:T/id=X>`, typed `<urn:ao:T>`; a column
  `c` is the property `<urn:ao:T#c>`; a foreign-key column also yields
  `<urn:ao:T#ref-c>` pointing at the referenced row. Because the mapping is
  derived from the metadata, an upstream column added tomorrow is exported
  tomorrow with no code change here.
- `export.py`: Postgres catalog → N-Triples, table by table, through the
  metadata. `python -m auto_ontology.rdf.export > catalog.nt`.
- `writes.py`: the one write seam, `commit(ops, witness, origin, txn)`, and the
  row primitives built on it (`row_ops`, `update_ops`, `delete_ops`;
  `insert_rows`, `update_rows`, `delete_subjects`). `NIDRA_WRITES=sparql`
  (default) renders the ops to one SPARQL update; `NIDRA_WRITES=commit` posts
  them to gardend's Emporium ledger (`/emporium/commit/{graph}/ao`). See
  `nidra/README.md`, "Writes".
- `client.py`: the gardend HTTP client (`/api/sparql/query`, `/api/rdf/load`,
  `/api/sparql/update`) with N-Triples term parsing. Every pattern must be
  wrapped in `GRAPH ?g { ... }`: cell stores are named-graph partitioned and an
  unwrapped pattern silently matches nothing (`wrap()` does it).
- `patch.py`: when `CATALOG_READ_BACKEND=rdf`, replaces each DAL read that has
  a port in `auto_ontology.dal_rdf` with that port. Installed from
  `auto_ontology/dal/__init__.py`, so `from auto_ontology.dal.terms import f`
  anywhere in `retrieval/` binds the port. `AO_RDF_STRICT=1` makes an
  unported read raise instead of falling back to Postgres.

The oracle is upstream's own: `auto_ontology/dal/tests/golden/dal_reads.json`,
captured by NVIDIA for their Neo4j → Postgres port. `test_golden.py` replays
it against whichever backend is installed:

    CATALOG_READ_BACKEND=rdf uv run --no-sync pytest auto_ontology/dal/tests/test_golden.py

Environment: `GARDEND_URL` (default `http://127.0.0.1:7690`), `GARDEND_TOKEN`,
`GARDEND_GRAPH_ID` (default `ao-catalog`).
