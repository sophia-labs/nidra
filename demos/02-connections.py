"""Parity: connections management (list, env source, test) and why the demos
connect Pagila through a side engine instead of POST /api/connections."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, SIDE, nidra, side_up  # noqa: E402

d = Demo("02-connections", "connections: list, source, test",
         "Connections come from the UI/API (stored on the catalog row, or in Vault) or from CONNECTION_STRINGS.",
         "README.md § Connections Management; skills/nvidia-ontology-setup § Connect a source")

d.section("what the live engine is connected to")
code, conns = nidra("GET", "/api/connections")
d.show(f"GET /api/connections ({code})", conns)
code2, src = nidra("GET", "/api/connections/source")
d.show(f"GET /api/connections/source ({code2})", src)
d.check(code == 200 and code2 == 200, "both connection reads answer")
d.log("The live engine's CONNECTION_STRINGS (from the fork's .env) names one source: sqlite:////data/chinook.sqlite.")
d.log("Pagila's catalog is in the graph (seeded by dev_tools/fixtures/seed_graph_fixture.py), but no connection can run its SQL.")

d.section("POST /api/connections/test: Pagila with the .env credentials (nothing is stored)")
pg = {"type": "postgresql", "host": "host.docker.internal", "port": int(os.environ.get("POSTGRES_PORT", "5432")),
      "user": os.environ["POSTGRES_USER"], "password": os.environ["POSTGRES_PASSWORD"], "database": "pagila"}
code, body = nidra("POST", "/api/connections/test", json={"connection": pg})
d.show(f"test pagila ({code})", body)
d.check(code == 200, "Pagila connection test passes from inside the engine container")

d.section("POST /api/connections/test: the same with a wrong password (expect the driver's error, HTTP 422)")
bad = dict(pg, password="not-the-password")
code, body = nidra("POST", "/api/connections/test", json={"connection": bad})
d.show(f"test pagila, bad password ({code})", body, 1200)
d.check(code == 422, "a bad credential is refused with 422")
if code == 422 and "password" not in str(body).lower() and "auth" not in str(body).lower():
    d.note("the 422 carries a connection-pool timeout after 30 s, not the driver's authentication error the setup skill promises")


d.section("why the demos do not POST /api/connections for Pagila on the live engine")
d.log("1. UI connections shadow CONNECTION_STRINGS for SQL execution (auto_ontology/connectors/registry.py get_connectors:")
d.log("   env strings are read only when no UI connection exists), and the structured connection form has no")
d.log("   SQLite type (connection_string_factory.py), so adding Pagila would take Chinook away from the engine.")
d.log("2. DELETE /api/connections/{db} also asks the ingestion service to reset that database's catalog")
d.log("   (service.delete_connection -> trigger_reset -> /ingest/delete), so undoing it would delete Pagila's")
d.log("   hand-authored semantic layer from ao-catalog.")
d.log("3. Without Vault the password would be stored in plaintext on the catalog row, i.e. in the graph.")
d.note("the ingestion service reads the other precedence (CONNECTION_STRINGS first, ingestion_service/connections.py), "
       "so the two services can disagree about which sources exist")
d.log("")
d.log(f"Instead the Pagila questions run on a side engine ({SIDE}, demos/lib/side-engine.sh): the same code and the same")
d.log("graph, with CONNECTION_STRINGS naming both Pagila and Chinook.")
up = side_up()
d.check(up, f"side engine with both sources is up at {SIDE}")
d.finish("partial", "list/source/test work; creating Pagila on the live engine would shadow Chinook, so it runs on a side engine")
