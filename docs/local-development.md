# Local development stack

Use [`scripts/ctfmem-dev.sh`](../scripts/ctfmem-dev.sh) to avoid repeatedly exporting the
same local settings and starting Memory Core and the Web UI separately.

```bash
scripts/ctfmem-dev.sh init
# Edit the created .env once. It is ignored by Git.
scripts/ctfmem-dev.sh up
```

The script loads the operator-owned `.env`, starts Memory Core at `CTFMEM_CORE_PORT` (default
`8777`), waits for its OpenAPI endpoint, then starts the Web UI at `CTFMEM_WEB_PORT` (default
`8787`). `CTFMEM_SUMMARIZER_MODE=fake` is deterministic and offline. With `ais3`, the script
requires `AIS3_API_KEY` and uses `AIS3_BASE_URL` and `AIS3_SUMMARIZER_MODEL` unchanged.

`init` also runs the existing `scripts/memory-core-init.sh`, which writes Core's non-secret
defaults to `.ctfmem/core-config.json`. That file deliberately has higher precedence than the
environment for the Core's URL/model, so after changing `AIS3_BASE_URL`,
`AIS3_SUMMARIZER_MODEL`, or `AIS3_TIMEOUT` in `.env`, run `scripts/ctfmem-dev.sh init` once
more. It never stores `AIS3_API_KEY`.

When `CTFMEM_AUTO_CREATE_CHALLENGE=1`, it creates the configured `CTFMEM_CHALLENGE` only when
that challenge is absent. The optional agent arguments are derived from the `CTFMEM_AGENT_*`
variables in `.env`; they are still passed explicitly to `ctfmem-web`, whose CLI does not read
those variables itself.

## LiteLLM proxy

Memory Core already uses only OpenAI-compatible `POST /chat/completions`, so LiteLLM can sit in
front of AIS3 or another compatible upstream without changing project code. Start from
[`scripts/litellm-summarizer.example.yaml`](../scripts/litellm-summarizer.example.yaml), keep
the edited file and all credentials outside Git, and start LiteLLM on localhost.

Then point Memory Core at the proxy:

```bash
AIS3_BASE_URL=http://127.0.0.1:4000/v1
AIS3_API_KEY="$LITELLM_MASTER_KEY"
AIS3_SUMMARIZER_MODEL=ctfmem-summarizer
CTFMEM_SUMMARIZER_MODE=ais3
scripts/ctfmem-dev.sh init
```

LiteLLM is only for the Memory Core summarizer in this setup. It does not make the Claude Agent
SDK run non-Claude models; that still requires a separate provider adapter.
