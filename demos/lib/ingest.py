"""Trigger the ingestion service's POST /ingest inside the nidra-ingestion
container and wait for the pass to finish (read from its log)."""
from __future__ import annotations

import json
import subprocess
import time

CONTAINER = "nidra-ingestion"


def trigger(database: str, timeout: float = 300.0) -> dict:
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1))
    code = ("import json,urllib.request;"
            "r=urllib.request.urlopen(urllib.request.Request('http://localhost:3002/ingest',"
            f"data=json.dumps({{'database':'{database}'}}).encode(),headers={{'content-type':'application/json'}}),timeout=30);"
            "print(r.status, r.read().decode())")
    out = subprocess.run(["docker", "exec", CONTAINER, "python", "-c", code], capture_output=True, text=True, timeout=60)
    reply = (out.stdout or out.stderr).strip()
    t0 = time.time()
    finished, lines = None, []
    while time.time() - t0 < timeout:
        time.sleep(2)
        logs = subprocess.run(["docker", "logs", "--since", since, CONTAINER], capture_output=True, text=True, timeout=30)
        lines = [ln for ln in (logs.stdout + logs.stderr).splitlines()
                 if "sparql" not in ln and "GET /semantic/status" not in ln and "rdf/load" not in ln]
        done = [ln for ln in lines if f"Data ingestion finished successfully for database {database}" in ln
                or "Background ingest failed" in ln or "Ingest skipped" in ln]
        if done:
            finished = done[-1]
            break
    return {"reply": reply, "finished": finished, "log": lines, "seconds": round(time.time() - t0, 1)}
