#!/usr/bin/env sh
# Run the guided, non-secret ctfmem setup. Source is not required.
set -eu

exec ctfmem setup "$@"
