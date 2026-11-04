"""Parity: Key Features beyond the engine: Authentication & RBAC, the web UI,
flexible deployment."""
import os
import socket
import subprocess
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import FORK, NIDRA, Demo  # noqa: E402

d = Demo("17-platform", "auth and RBAC, web UI, deployment",
         "Authentication & RBAC (Better Auth), a web UI, Helm or Docker Compose deployment.",
         "README.md § Key Features, § Deployment, § Authentication")

d.section("authentication and RBAC at the engine")
for label, headers in (("no identity header", {}), ("x-auto-ontology-user-id: someone-else", {"x-auto-ontology-user-id": "someone-else"}),
                       ("x-api-key: bogus", {"x-api-key": "bogus"})):
    r = httpx.get(f"{NIDRA}/api/terms", headers=headers, timeout=30)
    d.log(f"GET /api/terms with {label}: {r.status_code}, {len(r.json().get('terms', [])) if r.status_code == 200 else '-'} terms")
d.log("FastAPI trusts x-auto-ontology-user-id and checks no permission itself (docs/mcp.md: 'the FastAPI port is a trusted "
      "internal service boundary'); Better Auth sessions, API tokens and RBAC live in the Next.js gateway, which Nidra does not run.")
d.check(False, "an unauthenticated or unprivileged caller is refused")
d.note("auth and RBAC are not yet in Nidra: whoever reaches :3001 reads and writes as anyone. Upstream has the same "
       "property at FastAPI and relies on the gateway; Nidra's nidra/README.md names Sophia's gateway or app as that front door.")

d.section("web UI")
s = socket.socket()
s.settimeout(1)
ui = s.connect_ex(("127.0.0.1", 3000)) == 0
s.close()
d.log(f"Next.js UI on 127.0.0.1:3000 listening: {ui}")
d.check(ui, "the web UI is served")

d.section("deployment: Docker Compose (compose.nidra.yaml) and Helm")
ps = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project.config_files=" + os.path.join(FORK, "compose.nidra.yaml"),
                     "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"], capture_output=True, text=True, timeout=30)
for ln in ps.stdout.strip().splitlines():
    d.log("  " + ln)
d.check(bool(ps.stdout.strip()), "the Nidra stack runs under Docker Compose")
d.log(f"Helm chart present in the fork: {os.path.isdir(os.path.join(FORK, 'helm'))} (upstream's chart deploys the Postgres stack; "
      "no Nidra chart was tried)")
d.finish("partial", "Compose runs; no auth/RBAC and no web UI in Nidra")
