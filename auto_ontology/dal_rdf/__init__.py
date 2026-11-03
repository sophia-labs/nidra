# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal`` reads, one module per DAL module.

A port keeps the DAL function's name, signature and return shape exactly and
reads the catalog through SPARQL (``auto_ontology.rdf.client``). The graded
oracle is ``auto_ontology/dal/tests/golden/dal_reads.json``. Helpers shared by
the ports live in ``_common.py``.
"""
