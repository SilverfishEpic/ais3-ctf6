#!/usr/bin/env sh
# Create non-secret Memory Core defaults. AIS3_API_KEY is deliberately never written here.
set -eu

config_path=.ctfmem/core-config.json
model=${1:-ais3/nemotron-3-ultra-550b}
base_url=${2:-https://llm-api.zoolab.org/v1}
config_dir=$(dirname "$config_path")
mkdir -p "$config_dir"
MEMORY_CORE_CONFIG_PATH="$config_path" MEMORY_CORE_MODEL="$model" MEMORY_CORE_BASE_URL="$base_url" python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MEMORY_CORE_CONFIG_PATH"])
settings = {
    "ais3_base_url": os.environ["MEMORY_CORE_BASE_URL"],
    "ais3_model": os.environ["MEMORY_CORE_MODEL"],
    "ais3_timeout": 300,
    "runs_dir": "./runs",
    "templates_dir": "./templates",
}
path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
PY
printf '%s\n' "Saved non-secret Memory Core defaults to $config_path. Set AIS3_API_KEY only for the server process."
