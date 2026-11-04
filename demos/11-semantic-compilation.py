"""Parity: semantic compilation trigger and status (Discover: generate the
ontology from metadata)."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, nidra  # noqa: E402

d = Demo("11-semantic-compilation", "semantic compilation: trigger and status",
         "Discover: generate enterprise ontologies from metadata (semantic compilation over the catalog).",
         "README.md (Discover); skills/nvidia-ontology-management step 6; skills/nvidia-ontology-setup § Verify")

code, before = nidra("GET", "/api/semantic-compilation/status")
d.show(f"status before ({code})", before)
code, terms = nidra("GET", "/api/terms", params={"limit": 1})
n_before = terms.get("total")
d.log(f"terms before: {n_before}")

d.section("POST /api/semantic-compilation/trigger")
since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
code, trig = nidra("POST", "/api/semantic-compilation/trigger")
d.show(f"trigger ({code})", trig)
d.check(code == 202 and trig.get("status") == "accepted", "trigger accepted (202)")

d.section("poll status for 60 s")
seen_running = False
for i in range(12):
    time.sleep(5)
    code, st = nidra("GET", "/api/semantic-compilation/status")
    seen_running = seen_running or bool(st.get("running"))
    d.log(f"  t+{(i + 1) * 5:2d}s running={st.get('running')} last_success_at={st.get('last_success_at')} last_failure_at={st.get('last_failure_at')}")
code, terms = nidra("GET", "/api/terms", params={"limit": 1})
d.log(f"terms after: {terms.get('total')}")

d.section("what the ingestion service did with it (docker logs nidra-ingestion, since the trigger)")
try:
    logs = subprocess.run(["docker", "logs", "--since", since + "Z", "nidra-ingestion"], capture_output=True, text=True, timeout=30)
    lines = [ln for ln in (logs.stdout + logs.stderr).splitlines() if "semantic" in ln.lower() and "GET /semantic/status" not in ln]
except Exception as exc:  # noqa: BLE001
    lines = [f"(could not read logs: {exc})"]
for ln in lines[-15:]:
    d.log("  " + ln[:300])
skipped = any("disabled" in ln for ln in lines)
d.check(seen_running or (terms.get("total") or 0) > (n_before or 0), "a compilation pass actually ran")
if skipped:
    d.note("the pass was skipped: 'semantic: compilation disabled in settings; skipping run'. Upstream stores the "
           "switch in the frontend's configurations table (Prisma), toggled by PUT /api/configurations/semantic-compilation, "
           "a Next.js route Nidra does not run; on the gardend backend the switch is the environment variable "
           "AO_FLAG_SEMANTIC_COMPILATION_ENABLED=true on the ingestion service (auto_ontology/infra/feature_flags.py), "
           "and the live nidra-ingestion container does not set it.")
code, cfg = nidra("GET", "/api/configurations/semantic-compilation")
d.log(f"GET /api/configurations/semantic-compilation -> {code} {str(cfg)[:120]}")
d.log("The current glossary (5 terms) is the hand-authored fixture from dev_tools/fixtures/seed_graph_fixture.py, "
      "not a compilation result; 'calculated: true' only means some Term exists (router.py semantic_status).")
d.finish(None if not skipped else "not yet",
         "trigger and status answer; the pass is skipped because compilation is switched off and Nidra has no settings route to switch it on" if skipped else "")
