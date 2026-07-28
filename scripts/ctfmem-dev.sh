#!/usr/bin/env sh
# Bootstrap and run the local Memory Core + Web UI development stack.
#
# Usage:
#   scripts/ctfmem-dev.sh init  # create .env from .env.example once, without overwriting it
#   scripts/ctfmem-dev.sh up    # load .env, start Memory Core, then run ctfmem-web
set -eu

command_name=${1:-up}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=${CTFMEM_ENV_FILE:-"$project_dir/.env"}

usage() {
    printf '%s\n' 'usage: scripts/ctfmem-dev.sh [init|up]'
}

case "$command_name" in
    init)
        if [ -e "$env_file" ]; then
            printf '%s\n' ".env already exists; preserved $env_file"
        else
            umask 077
            cp "$project_dir/.env.example" "$env_file"
            printf '%s\n' "created $env_file; set AIS3_API_KEY only when using ais3 mode"
        fi
        # Keep the Core's persisted, non-secret settings aligned with the local .env.
        # Its initializer deliberately never stores AIS3_API_KEY.
        set -a
        # shellcheck disable=SC1090
        . "$env_file"
        set +a
        (cd "$project_dir" && sh scripts/memory-core-init.sh)
        mkdir -p "$project_dir/.ctfmem"
        ;;
    up)
        if [ ! -f "$env_file" ]; then
            printf '%s\n' "missing $env_file; run scripts/ctfmem-dev.sh init first" >&2
            exit 2
        fi

        # .env is an operator-owned local shell file; never source an untrusted file.
        set -a
        # shellcheck disable=SC1090
        . "$env_file"
        set +a

        cd "$project_dir"
        core_port=${CTFMEM_CORE_PORT:-8777}
        web_port=${CTFMEM_WEB_PORT:-8787}
        api_url=${CTFMEM_API_URL:-"http://127.0.0.1:$core_port"}
        mode=${CTFMEM_SUMMARIZER_MODE:-fake}
        core_config=${MEMORY_CORE_CONFIG_PATH:-.ctfmem/core-config.json}
        workspace=${CTFMEM_AGENT_WORKSPACE:-}
        runtime_dir=${CTFMEM_AGENT_RUNTIME_DIR:-./.ctfmem/web-agent}

        case "$mode" in
            fake)
                uv run --python 3.12 python -m memory_core --config "$core_config" \
                    --fake-summarizer --port "$core_port" &
                ;;
            ais3)
                if [ -z "${AIS3_API_KEY:-}" ]; then
                    printf '%s\n' 'AIS3_API_KEY is required when CTFMEM_SUMMARIZER_MODE=ais3' >&2
                    exit 2
                fi
                uv run --python 3.12 python -m memory_core --config "$core_config" \
                    --port "$core_port" &
                ;;
            *)
                printf '%s\n' 'CTFMEM_SUMMARIZER_MODE must be fake or ais3' >&2
                exit 2
                ;;
        esac
        core_pid=$!

        stop_core() {
            if kill -0 "$core_pid" 2>/dev/null; then
                kill "$core_pid" 2>/dev/null || true
                wait "$core_pid" 2>/dev/null || true
            fi
        }
        trap stop_core EXIT INT TERM

        attempts=0
        until curl --fail --silent "$api_url/openapi.json" >/dev/null 2>&1; do
            if ! kill -0 "$core_pid" 2>/dev/null; then
                wait "$core_pid"
                exit $?
            fi
            attempts=$((attempts + 1))
            if [ "$attempts" -ge 40 ]; then
                printf '%s\n' "Memory Core did not become ready at $api_url" >&2
                exit 1
            fi
            sleep 0.25
        done

        if [ "${CTFMEM_AUTO_CREATE_CHALLENGE:-0}" = 1 ]; then
            challenge=${CTFMEM_CHALLENGE:?CTFMEM_CHALLENGE is required for auto-create}
            if ! uv run --python 3.12 ctfmem --api-url "$api_url" challenge show "$challenge" \
                >/dev/null 2>&1; then
                uv run --python 3.12 ctfmem --api-url "$api_url" challenge create "$challenge" \
                    --title "${CTFMEM_CHALLENGE_TITLE:-$challenge}" \
                    --template "${CTFMEM_CHALLENGE_TEMPLATE:-generic}"
            fi
        fi

        if [ -n "$workspace" ]; then
            if [ ! -d "$workspace" ]; then
                printf '%s\n' "CTFMEM_AGENT_WORKSPACE is not a directory: $workspace" >&2
                exit 2
            fi
            uv run --python 3.12 ctfmem-web --api-url "$api_url" --port "$web_port" \
                --agent-workspace "$workspace" --agent-runtime-dir "$runtime_dir" \
                --agent-max-turns "${CTFMEM_AGENT_MAX_TURNS:-8}" \
                --agent-max-cost-usd "${CTFMEM_AGENT_MAX_COST_USD:-2}" \
                --agent-memory-policies "${CTFMEM_AGENT_MEMORY_POLICIES:-facts-assumptions}" \
                --agent-fake-agents "${CTFMEM_AGENT_FAKE_AGENTS:-multi_tool,sandbox_denied}"
        else
            uv run --python 3.12 ctfmem-web --api-url "$api_url" --port "$web_port"
        fi
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
