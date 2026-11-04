"""Parity: check_readiness, folded with upstream's own code."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, nidra, upstream_readiness  # noqa: E402

d = Demo("01-readiness", "check_readiness: compiled layer + live connection + catalog",
         "check_readiness tells 'not set up' from 'not understood' in three cheap reads.",
         "docs/mcp.md § About check_readiness; mcp/auto_ontology_mcp/readiness.py")

d.section("the three probes, as the MCP server makes them")
code_h, health = nidra("GET", "/api/health")
d.show(f"GET /api/health ({code_h})", health)
code_s, status = nidra("GET", "/api/semantic-compilation/status")
d.show(f"GET /api/semantic-compilation/status ({code_s})", status)
code_c, conns = nidra("GET", "/api/connections")
d.show(f"GET /api/connections ({code_c})", conns)
code_d, dbs = nidra("GET", "/api/datasources/dbs")
d.show(f"GET /api/datasources/dbs ({code_d})", {"count": dbs.get("count"), "names": [x["name"] for x in dbs.get("data", [])]})
code_e, env_source = nidra("GET", "/api/connections/source")
d.show(f"GET /api/connections/source ({code_e}) — is CONNECTION_STRINGS set", env_source)

d.check(code_h == 200 and health.get("status") == "ok", "engine health ok (store: gardend)")
d.check(code_s == 200 and status.get("calculated") is True, "semantic layer reported built (calculated: true)")
d.check(code_d == 200 and {"pagila", "chinook"} <= {x["name"] for x in dbs.get("data", [])}, "catalog holds pagila and chinook")

d.section("upstream's verdict (readiness._summarise, unmodified)")
verdict = upstream_readiness(status, conns, dbs)
d.show("check_readiness", verdict)
d.check(verdict["ready"] is True, "upstream check_readiness says ready")
if not verdict["ready"] and env_source is True:
    d.note("upstream's fold reads only UI-managed connections (/api/connections count 0); this engine's "
           "connection comes from CONNECTION_STRINGS (/api/connections/source = true), so check_readiness "
           "reports 'No database connection is configured' while Chinook questions do execute (demo 03). "
           "Same behaviour upstream for any env-configured deployment.")
d.finish(None if verdict["ready"] else "partial",
         "probes all answer; upstream's fold reports a false blocker for an env-configured connection" if not verdict["ready"] else "")
