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
- `docs/` - maintained documentation index, architecture notes, contracts, and roadmap.

Memory Core is the only semantic-memory writer. Human CLI and Agent Bridge reach it
through the public HTTP/OpenAPI contract; neither client writes `state.yaml` or
`journal.jsonl` directly.

## Human CLI quick start

`ctfmem` is the human-facing HTTP client for Memory Core. The most reliable invocation is
through `uv run`:

```bash
uv run ctfmem --help
```

If you install the package with `uv pip install -e .`, the executable is installed inside
the project virtual environment. Use `source .venv/bin/activate` on Linux/macOS or
`.\.venv\Scripts\Activate.ps1` on Windows PowerShell before calling `ctfmem` directly.

Full installation and Linux/Windows usage notes are in
[`docs/human-cli.md`](docs/human-cli.md).

## Web UI quick start

`ctfmem-web` serves the same operations as a local page, bound to `127.0.0.1` and
unauthenticated. Start Memory Core first, then:

```bash
python -m human_client.web --api-url http://127.0.0.1:8777
```

Adding `--agent-workspace <dir>` also lets the page start `ctfmem agent` runs, by launching
the CLI as a child process. Without that flag the capability is off entirely.

See [`docs/human-webui.md`](docs/human-webui.md), plus
[`docs/adr/0001-human-web-ui.md`](docs/adr/0001-human-web-ui.md) for why this exists despite
`web GUI` being on the non-goal list, and
[`docs/adr/0002-web-agent-runs.md`](docs/adr/0002-web-agent-runs.md) for the agent decision.

## Native shell integration

`ctfmem shell-init` starts an isolated, temporary zsh for a CTF session. It never modifies your
`.zshrc`, adds no command hooks, and does not proxy terminal I/O. Set the local defaults once,
then enter that shell:

```bash
ctfmem config set --challenge web-01 --actor hacker
ctfmem shell-init
```

The child loads your usual zsh configuration first, then adds a shortcut only if its name is not
already an alias, function, or command. A collision always leaves your existing command intact.
Use `exit` to return to the outer shell; its configuration is untouched.

```bash
# Run a non-interactive command and retain stdout/stderr/exit code.
cap nmap -sV target

# Add a human observation or inspect the whiteboard.
note 'port 80 redirects to HTTPS'
mem --facts
mem fact F001
mem assumption --find 'SQL injection'
mem fact --tag web --section target

# Ask for a semantic summary; append & when you do not want to wait at the prompt.
ask 'what is confirmed?' --fact &

# Less frequent commands remain namespaced.
ctf capture-pending list
ctf /template set web --reason 'confirmed service type'
exit
```

Available shortcuts, when unclaimed, are `ctf`, `cap`, `note`, `mem`, `ask`, `add`, `edit`,
`guess`, and `try`. `cap COMMAND ...` is explicit capture only: it does not observe unrelated
commands or terminal input. When the command ends, `cap` durably queues its raw stdout/stderr and
returns the prompt; a detached, stdin-free process makes one initial delivery attempt to Memory
Core. If delivery fails, use `ctf capture-pending list` and explicitly retry the retained result.
For a script that must wait for the ingest response, use:

```bash
ctfmem capture --wait -- nmap -sV target
```

Use `mem` for immediate, deterministic Fact/Assumption lookup. `mem fact F001` and
`mem assumption A003` look up one stable ID. `--find` performs a case-insensitive substring match
over IDs, item text, tags, and evidence IDs; `--tag` matches an exact tag and may be repeated.
The existing `mem --facts`, `mem --assumptions`, and `--section` forms remain available. Use
`ask` only when you want a natural-language answer; it calls the Core query path and can wait for
the model, so zsh job control (`ask ... &`) is the non-blocking form.

There is currently no autocapture mode. Recording arbitrary command output would require
intercepting the child shell's stdout/stderr, which changes TTY behaviour for some programs;
recording later interactive stdin additionally requires a terminal proxy/PTY. `gdb` and `sqlmap`
therefore remain outside the scope of `cap`'s capture guarantee.

Earlier releases wrote a marked block into `~/.zshrc`. New `shell-init` never installs it; run
`ctfmem shell-init --remove` once to remove an existing legacy block.

## License

This project is licensed under the Smoking Elephant Slop Public License,
Version 1.1.

SESPL-1.1 is a custom permissive license. It is not OSI-approved and is not
listed in the SPDX License List. For tooling purposes, use:

```text
LicenseRef-SESPL-1.1
```

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
