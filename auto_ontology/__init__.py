# Sophia Labs fork: with CATALOG_READ_BACKEND=rdf the catalog is read from and
# written to a gardend graph; the ports are installed here, before any module
# of the package can be mid-import (see auto_ontology/rdf/README.md).
# Modified by Sophia Labs for Nidra, 2026-11-03 (see NIDRA-NOTICE.md).

from auto_ontology.rdf.patch import install as _install_rdf_backend  # noqa: E402

_install_rdf_backend()
