# Nidra

Nidra is NVIDIA Auto Ontology's engine, the semantic layer and the text-to-SQL
agent, running on the Sophia engine (gardend) instead of Postgres: the catalog,
the glossary, the SQL attributes and the vector index are one RDF graph.

Nidra is built on NVIDIA Auto Ontology (Apache-2.0). Sophia Labs is not
affiliated with NVIDIA.

## Run it

One command (macOS or Linux, with Docker running):

    curl -fsSL https://nidra.computer/install.sh | sh

On Windows, in PowerShell:

    irm https://nidra.computer/install.ps1 | iex

Both ask for an OpenRouter key (or take `OPENROUTER_API_KEY` from the
environment), start one container and print the link to open. Or run the
container yourself:

    docker run -d --name nidra --restart unless-stopped \
      -p 127.0.0.1:8086:8086 -p 127.0.0.1:3001:3001 -v nidra:/data \
      -e OPENROUTER_API_KEY=sk-or-... ghcr.io/sophia-labs/nidra
    docker exec nidra nidra url     # the link to open (it holds the key)

`ghcr.io/sophia-labs/nidra` (`Dockerfile.nidra`) holds the engine, gardend with
its embedding model, and the Sophia UI with the Nidra screens; everything that
lasts is in the `/data` volume. Model settings: `OPENROUTER_API_KEY` alone
means Claude Sonnet through OpenRouter; `NIDRA_MODEL` picks another model;
`DEFAULT_MODELS_ENDPOINT`, `DEFAULT_MODELS_API_KEY` and `DEFAULT_MODELS_MODEL`
name any OpenAI-compatible endpoint instead. `CONNECTION_STRINGS` names your
own databases (below). `NIDRA_URL` is the address printed in the link (default
`http://localhost:8086`).

To build the image: `nidra/oneliner/stage-ui.sh` (copies the built Sophia UI
into `nidra/oneliner/ui`), then `docker build -f Dockerfile.nidra -t
ghcr.io/sophia-labs/nidra .`

**The advanced way, with compose** (the engine and gardend as separate
containers, from the download or this checkout):

    docker load -i nidra-gardend-<date>.tar.gz  # the Sophia engine image, with the embedding model baked in
    tar xzf nidra-local-<date>.tar.gz && cd nidra-local-<date>
    cp .env.example .env                         # set DEFAULT_MODELS_* (any OpenAI-compatible chat model;
                                                 # OpenRouter works) and GARDEND_TOKEN; no embedding endpoint
    docker compose -f compose.nidra.yaml up -d   # builds the engine image on first run
    curl -s http://127.0.0.1:3001/api/health

The engine answers on :3001 (its API is the upstream FastAPI surface; the
front door that authenticates and routes is Sophia's gateway or your Sophia
app, which forwards `x-auto-ontology-user-id`; the engine has no login of its own, so keep
:3001 on 127.0.0.1, as the commands above do). Ask a question:

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
- `Dockerfile.nidra`, `nidra/oneliner/`: the one-container image (`run.sh`
  supervises gardend, the ingestion service and the engine; the Nidra service
  `nidra/service/main.mjs` runs attached behind gardend) and the installers.
- `nidra/closure.sh`: the proof that nothing of the engine's state is in
  Postgres: seed the fixture with Postgres unreachable, replay the golden.
