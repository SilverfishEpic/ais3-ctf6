# AIS3 CTF Shared Memory System v2

This repository contains the v2 shared-memory architecture for CTF workflows.

Memory Core is the authoritative service. Humans and external agents are clients that
talk to Memory Core through the frozen HTTP/domain contract under `spec/`.

## Branch Status

```text
main
  ├── Memory Core            # integrated
  ├── Human CLI (`ctfmem`)    # integrated
  └── Agent Bridge           # integrated
```

Historical component branches were squash-integrated and archived. New work should branch
from current `main` and must keep `spec/` and Memory Core as the authoritative HTTP/domain
contract.

## Current Components

- `memory_core/` - canonical Memory Core implementation.
- `human_client/` - Human CLI package; exposes the `ctfmem` console command.
- `human_client/web/` - local single-user web UI; exposes the `ctfmem-web` console command.
- `agent_bridge/` - Agent Bridge package; owns Claude adapter, loop behavior, and memory skill.
- `spec/` - normative protocol, OpenAPI, and JSON schemas.
- `templates/` - curated CTF category catalogs used by Memory Core guessing.
- `scripts/memctl.py` - developer/admin helper for exercising Memory Core.
- `docs/` - maintained documentation index, architecture notes, contracts, and ADRs.

Memory Core is the only semantic-memory writer. Human CLI and Agent Bridge reach it
through the public HTTP/OpenAPI contract; neither client writes `state.yaml` or
`journal.jsonl` directly.

## Human CLI: start here

`ctfmem` is the human-facing client for Memory Core. This is the complete Linux/zsh workflow
for a new CTF session. It intentionally uses ordinary shell commands: there is no terminal
proxy, no background observation of every command, and no persistent shell modification.

### 1. Prepare the project shell

From the repository root, install the development environment once, then activate it in the
shell from which you will start the CTF session:

```bash
uv sync --extra dev
source .venv/bin/activate
ctfmem --help
```

Activating the venv matters: the temporary zsh created later inherits this `PATH`, so its short
commands can still find `ctfmem`. If direct `ctfmem` is not wanted, the long form
`uv run ctfmem ...` also works, but is deliberately not used for the high-frequency examples
below.

### 2. Start Memory Core

In a separate terminal, initialise the non-secret Core configuration if needed, then start the
local server. The API key belongs only to this server process; never put it in CLI config or a
shell profile.

```bash
./scripts/memory-core-init.sh
AIS3_API_KEY='set-this-only-in-the-server-environment' \
  uv run python -m memory_core --host 127.0.0.1 --port 8000
export $(grep -v '^#' .env | xargs)
```

If the Core is already running, skip this step. The CLI default URL is
`http://127.0.0.1:8000`.

### 3. Create and select a challenge

For a first session, create the challenge, then let `setup` ask the four ordinary client
questions one by one. Press Enter to accept the displayed defaults (including the local Core URL).
`setup` writes only project-local, non-secret defaults to `.ctfmem/client-config.json`, which is
git-ignored.

```bash
ctfmem challenge create web-01 --title 'Web 01' --template web
ctfmem setup
```

At the prompts, use these values for this example:

```text
Memory Core URL [http://127.0.0.1:8000]: <press Enter>
Challenge ID: web-01
Operator name [human]: hacker
HTTP timeout (seconds) [30.0]: <press Enter>
Agent workspace (optional): <press Enter>
```

Confirm what was saved:

```bash
ctfmem config show
```

After becoming familiar with the fields, the same configuration can be set without prompts,
which is useful for repeatable setup or scripts:

```bash
ctfmem config set --challenge web-01 --actor hacker --api-url http://127.0.0.1:8000
ctfmem config show
```

To work on another challenge for one command, add `--challenge other-id`; command-line options
override the saved defaults.

### 4. Enter the short-command shell

Run this from the activated shell:

```bash
ctfmem shell-init
```

It opens a **temporary nested zsh**. Your normal `.zshrc` is loaded into that child first, then
CTF shortcuts are created only when their names are unclaimed. Nothing is written to `.zshrc`.
Type `exit` when the CTF session ends to return to the outer shell.

The shortcuts are the primary daily interface:

| Short command | Full command | Use it for |
| --- | --- | --- |
| `cap CMD ...` | `ctfmem /capture -- CMD ...` | run one finishing external command and retain its result |
| `note TEXT` | `ctfmem /note TEXT` | record an observation in plain language |
| `mem ...` | `ctfmem /memory ...` | deterministic Fact/Assumption lookup |
| `ask QUESTION ...` | `ctfmem /ask QUESTION ...` | semantic question to Memory Core |
| `add ...` | `ctfmem /add ...` | add a Fact or Assumption, interactively or with fields |
| `edit ...` | `ctfmem /edit ...` | make an explicit whiteboard mutation |
| `guess ...` | `ctfmem /guess ...` | show template-backed techniques |
| `try ...` | `ctfmem /try ...` | mark a technique as tried |
| `ctf ...` | `ctfmem ...` | short namespaced escape hatch for less-common commands |

If a name already belongs to your alias, function, or command, `shell-init` prints a collision
warning and leaves it alone. Use the full `ctfmem ...` command in that case. The legacy cleanup
command below is only for old releases that previously edited `.zshrc`:

```bash
ctfmem shell-init --remove
```

## Web UI quick start

`ctfmem-web` serves the same operations as a local page, bound to `127.0.0.1` and
unauthenticated. After completing `ctfmem setup` above and starting Memory Core, launch it with
the short console command:

```bash
ctfmem-web
```

It uses the same `.ctfmem/client-config.json` defaults as `ctfmem` (including the Core URL,
challenge, and actor) and serves the page at `http://127.0.0.1:8787`. Override the Core URL or
web port only when needed:

```bash
ctfmem-web --api-url http://127.0.0.1:8000 --port 8787
```

As with `ctfmem`, run it after `source .venv/bin/activate`; otherwise use
`uv run ctfmem-web` from the repository root.

Adding `--agent-workspace <dir>` also lets the page start `ctfmem agent` runs, by launching
the CLI as a child process. Without that flag the capability is off entirely.

See [`docs/human-webui.md`](docs/human-webui.md), plus
[`docs/adr/0001-human-web-ui.md`](docs/adr/0001-human-web-ui.md) for why this exists despite
`web GUI` being on the non-goal list, and
[`docs/adr/0002-web-agent-runs.md`](docs/adr/0002-web-agent-runs.md) for the agent decision.

## Native shell integration

This section is a practical session playbook. Run it after the four setup steps above, inside the
temporary zsh. The short commands are not aliases for arbitrary shell automation; each one maps
to one explicit `ctfmem` operation.

### A. Capture a command that finishes

Use `cap` for a non-interactive external command such as `curl`, `nmap`, or `checksec`:

```bash
cap curl -i http://target/
cap nmap -sV target
```

`cap` displays the command's normal stdout/stderr and returns the child program's exit code. Once
the program exits, it saves the complete raw output (including non-UTF-8 bytes) in the local
pending queue and immediately gives the terminal back. A detached, stdin-free worker then makes
one delivery attempt to Memory Core, so a slow Core does not block your prompt.

Check or explicitly retry a retained result only when needed:

```bash
ctf capture-pending list
ctf capture-pending retry PENDING_ID
```

The worker never auto-retries: after a network failure it cannot know whether the server received
the first request, and blindly replaying could duplicate a record. A script that genuinely needs
the ingest result before continuing can opt in to waiting:

```bash
ctfmem capture --wait -- nmap -sV target
```

`cap` is intentionally **not autocapture**. It cannot record commands typed outside `cap`, and it
does not record later terminal input. Do not use it for interactive/prompt-driven programs such
as `gdb` or `sqlmap`; run those normally and record the meaningful outcome with `note` or `add`.
Capturing every command's output would require intercepting the shell's stdout/stderr and would
change TTY behaviour; capturing interactive stdin additionally needs a terminal proxy/PTY. Those
trade-offs are deliberately not hidden behind this workflow.

### B. Record what you learned

Use `note` for an unstructured observation. It is the fastest way to leave context for the next
person or query; Memory Core decides how to summarise it.

```bash
note 'The login page accepts only alphanumeric credentials in client-side JavaScript.'
note 'curl showed a Big5 page; inspect checkUserAccessTo next.'
```

Use `add` when you know whether the item is a confirmed **Fact** or an unconfirmed
**Assumption**, and want to supply the Web UI-equivalent fields. With no fields, `add` asks for
them one at a time in an interactive terminal.

```bash
add fact --text 'The /cloud/ endpoint serves a login page' \
  --section recon --reason 'observed with curl' --evidence I-0003 --tag web

add assumption --text 'The authentication request may be vulnerable to SQL injection' \
  --section attack_surface --reason 'input handling has not been tested' --tag sqli
```

`--evidence` and `--tag` may each be repeated. `reason` is the rationale for an explicit
whiteboard change; it is not a substitute for raw command output.

### C. Find a known Fact or Assumption immediately

`mem` is deterministic: it reads the current whiteboard and does not ask a model to formulate an
answer. Prefer it when you need exact, inspectable information.

```bash
# Browse a layer.
mem --facts
mem --assumptions

# Look up a stable item ID (case-insensitive).
mem fact F001
mem assumption A003

# Search items by ID, text, tag, or evidence ID.
mem fact --find cloud
mem assumption --find 'SQL injection'

# Narrow the lookup. Repeated --tag values must all be present.
mem fact --tag web --section recon
mem assumption --tag sqli --tag login
```

An unknown ID returns a not-found error; a search with no matches prints `No matching ... items.`
and succeeds. `mem fact F001` is the right command when an answer refers to `F001` and you need
to verify the underlying record rather than trust a summary.

### D. Ask a semantic question without losing the prompt

`ask` is for questions such as a situation summary or a comparison across records. It uses the
Memory Core query path, so it may wait for the configured model. Add `&` in zsh when you want the
prompt back immediately:

```bash
ask 'What is confirmed about the login endpoint?' --fact &
ask 'Which assumptions should be tested next?' --assumption &
```

When the answer arrives, it includes references such as `F001`; use `mem fact F001` to inspect
the source item. Use `jobs`, `fg`, and normal zsh job control to manage background questions.

### E. Work through the technique catalog

`guess` only displays techniques from the active challenge template; it never runs a scan or
attack. `try` records that a named technique was attempted and why.

```bash
guess --count 3
try WEB-SSTI --note 'Template markers were rendered literally' \
  --reason 'manual probe on the login form'
```

### F. Make a deliberate correction

Use `edit` for an explicit mutation of an existing whiteboard item. In an interactive shell it
prompts for omitted revision/reason fields; scripts should provide them explicitly. For example:

```bash
edit promote A003 --reason 'A reproducible payload confirmed the hypothesis'
edit replace F001 --text 'The /cloud/ endpoint redirects to /cloud/login' \
  --reason 'verified with curl -I'
```

For less frequent operations, retain the `ctf` prefix rather than taking common shell command
names. For example:

```bash
ctf /template set web --reason 'confirmed service type'
ctf /challenge list
ctf /memory --facts --section recon
```

Outside the temporary zsh, use the same interface with the `ctfmem` prefix, for example
`ctfmem /memory fact F001` or `ctfmem capture -- curl -i http://target/`.

## License

This project uses the **Smoking Elephant Slop Public License v1.2**
(SESPL-1.2): a custom WTFPL/Unlicense-inspired public-domain dedication,
WTFPL-style fallback, and patent non-assertion / automatic grant.

The elephant has not reviewed the code. Neither has the patent office.

This is **not** formal `WTFPL OR Unlicense` dual licensing, nor an
OSI-approved or SPDX-listed license. Custom identifier: `LicenseRef-SESPL-1.2`.

See [LICENSE](LICENSE) and [docs/license.md](docs/license.md).

## Local Verification

Use Python 3.12.

```bash
uv run --python 3.12 --extra dev pytest
uv run --python 3.12 --extra dev ruff check .
uv run --python 3.12 --extra dev mypy memory_core human_client agent_bridge
uv run --python 3.12 --extra dev python scripts/gen_spec.py --check
```

Live AIS3 checks are opt-in and must keep credentials only in the Memory Core process
environment.
