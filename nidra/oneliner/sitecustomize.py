# Nidra's one-container image: keep `docker logs nidra` readable. Python imports this at start (PYTHONPATH),
# in the engine, the ingestion service and their worker processes alike. The engine logs every gardend request
# (httpx) and every health check (uvicorn.access) at INFO, and the NVIDIA endpoints client warns on every
# structured-output call to a model it does not know; none of it says anything to the person running Nidra.
# NIDRA_VERBOSE=1 keeps all of it.
import logging
import os
import warnings

if os.environ.get("NIDRA_VERBOSE", "") not in ("1", "true", "yes"):
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=UserWarning, module=r"langchain_nvidia_ai_endpoints(\..*)?$")
