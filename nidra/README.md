# Nidra

Nidra is NVIDIA Auto Ontology's engine, the semantic layer and the text-to-SQL
agent, running on the Sophia engine (gardend) instead of Postgres: the catalog,
the glossary, the SQL attributes and the vector index are one RDF graph.

Nidra is built on NVIDIA Auto Ontology (Apache-2.0). Sophia Labs is not
affiliated with NVIDIA.

## Run it

From the download (two files) or this checkout:

    docker load -i nidra-gardend-<date>.tar     # the Sophia engine image (skip if you build it: nidra/gardend)
    tar xzf nidra-local-<date>.tar.gz && cd nidra-local-<date>
    cp .env.example .env                         # set DEFAULT_MODELS_* (any OpenAI-compatible chat model;
                                                 # OpenRouter works) and GARDEND_TOKEN; no embedding endpoint
    docker compose -f compose.nidra.yaml up -d   # builds the engine image on first run
    curl -s http://127.0.0.1:3001/api/health

The engine answers on :3001 (its API is the upstream FastAPI surface; the
front door that authenticates and routes is Sophia's gateway or your Sophia
app, which forwards `x-auto-ontology-user-id`). Ask a question:

    curl -s -X POST http://127.0.0.1:3001/api/chat/completions \
      -H 'content-type: application/json' -H 'x-auto-ontology-user-id: me' \
      -d '{"question": "How many tracks did each artist sell last year?"}'

The sample source is Chinook (SQLite, `nidra/data`). Point `CONNECTION_STRINGS`
at your own warehouse (Postgres, Snowflake, Databricks, DuckDB, MySQL, Trino,
ClickHouse, SQLite) to catalogue it; the ingestion service sweeps it every
`INGEST_INTERVAL_HOURS`.

**Attached to your own Sophia app** (the local edition): run without the
`gardend` service (`--scale gardend=0`) and set `GARDEND_URL` to the app's
loopback API (`http://host.docker.internal:7674`), `GARDEND_TOKEN` to a token
from `gardend token create --grant session-all`, `GARDEND_GRAPH_ID` to the
graph you want the catalog in. Turn `find` on for that graph with
`gardend index --graph <id>`.

## How it is built

- `auto_ontology/rdf/`: the Direct Mapping of upstream's catalog schema, the
  gardend client, the write primitive, the backend switch.
- `auto_ontology/dal_rdf/`: every DAL read and write, ported to SPARQL, graded
  by upstream's own golden captures (`auto_ontology/dal/tests/golden/`) and by
  `dev_tools/fixtures/write_diff.py`.
- `auto_ontology/vdb/gardend.py`: the vector store on gardend's `find`.
- `nidra/gardend/`: the gardend release image plus ONNX Runtime.
- `nidra/closure.sh`: the proof that nothing of the engine's state is in
  Postgres: seed the fixture with Postgres unreachable, replay the golden.
