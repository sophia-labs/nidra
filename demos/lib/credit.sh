#!/usr/bin/env bash
# Print the OpenRouter credit left on the key in the fork's .env (numbers only).
. "$(dirname "$0")/env.sh"
python3 - <<'PY'
import json, os, urllib.request
req = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization": "Bearer " + os.environ["DEFAULT_MODELS_API_KEY"]})
d = json.load(urllib.request.urlopen(req, timeout=20)).get("data", {})
print(f"openrouter credit: {d.get('total_credits', 0) - d.get('total_usage', 0):.4f} USD left of {d.get('total_credits')}")
PY
