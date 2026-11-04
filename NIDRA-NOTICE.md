# Nidra: notice of modifications

Nidra is built on NVIDIA Auto Ontology (https://github.com/NVIDIA/auto-ontology), Apache License 2.0,
Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. Sophia Labs is not affiliated with NVIDIA and NVIDIA does
not endorse this work. The upstream LICENSE, OPEN_SOURCE_NOTICE and THIRD_PARTY_NOTICES files are retained unchanged.

Sophia Labs modified this software starting 2026-11-03 (fork point: upstream commit cdaf61e), under the same
Apache License 2.0. Every modified upstream file carries the comment "Modified by Sophia Labs for Nidra" below its
SPDX header; the full list is `git diff --name-only cdaf61e`. In summary:

- The catalog, glossary, SQL attributes, analyses, zones, tags, rules, groups and compilation history are stored in
  a gardend graph (the Sophia engine) as RDF, a W3C Direct Mapping of upstream's SQLAlchemy metadata, instead of
  Postgres: `auto_ontology/rdf/`, `auto_ontology/dal_rdf/`, the installer in `auto_ontology/__init__.py` and
  `auto_ontology/dal/__init__.py`.
- The vector index is gardend's own entity index (`auto_ontology/vdb/gardend.py`); no embedding endpoint is used.
- Runtime gates for running without the upstream frontend database: feature flags, chat settings, identity role,
  conversations and analytics, server and ingestion-service startup and health checks, and an external-scheduler
  switch for the ingestion service.
- Test tooling: per-lane golden captures (`dev_tools/fixtures/golden_extra/`), the write-grading harness
  (`dev_tools/fixtures/write_diff.py`, `write_cases/`), the fixture export, and `nidra/closure.sh`.
- Packaging: `compose.nidra.yaml`, `nidra/` (README, bundle script, gardend image build with ONNX Runtime, the
  Nidra service, the one-container image `Dockerfile.nidra` with `nidra/oneliner/` and its installers).

The Sophia engine (gardend) is a separate work, not included in this source, licensed under the PolyForm
Noncommercial License 1.0.0 by Sophia Labs; Nidra talks to it over HTTP. ONNX Runtime (MIT) is added to its container
image at build time. The one-container image (`Dockerfile.nidra`) also carries, each under its own license: the
gardend binary (`/usr/local/share/gardend/`), ONNX Runtime, the multilingual-e5-small model (MIT) and Node.js (MIT)
(`/usr/local/share/licenses/`), and the Sophia UI (`/opt/nidra/ui/`, its notices in `third-party/`).
