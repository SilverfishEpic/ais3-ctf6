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

CTF Memory starts an isolated child zsh; it never modifies your `.zshrc`, does not add command
hooks, and does not proxy terminal I/O. Start Memory Core, set the one-time local client defaults,
then enter the temporary CTF shell:

```bash
ctfmem config set --challenge web-01 --actor hacker
ctfmem shell-init --shell zsh
```

The child first loads your normal zsh configuration, then adds each short command only when the
name is unclaimed. A colliding alias, function, or command remains yours. Use `exit` to return to
the outer shell. This keeps your native completion, plugins, history, keybindings, environment,
and terminal window intact. Use shortcuts such as `cap curl -I http://target`, `note 'port 80 open'`,
`mem --facts`, and `ask 'what is confirmed?'`. Administrative operations remain available as
`ctfmem` commands, for example `ctfmem /status`,
`ctfmem /diff on`, `ctfmem /note <text>`, and `ctfmem /memory`.
Global settings may appear before or after the command: `ctfmem /memory --challenge web-01`.
For the shortest one-off form, `ctfmem web-01/memory` means the same thing.
Agent runs remain explicit through `ctfmem agent run` and `ctfmem agent stop`. Bash cannot
safely interpret a bare `/status` as anything other than an absolute path without intercepting
its input loop, so the `ctfmem /…` form is intentional. The sourced integration provides
`cap COMMAND ...` as the short, explicit form of `ctfmem capture -- COMMAND ...`; it records
only commands you prefix with `cap` and never observes other terminal activity. Once the command
ends, it saves the result locally and returns the prompt while a detached, stdin-free child makes
one initial delivery attempt. Use `ctfmem capture --wait -- COMMAND ...` only when a script needs
to wait for Memory Core's response.

Earlier versions of `shell-init` wrote a marked block into `~/.zshrc`. It is never installed now;
run `ctfmem shell-init --remove` once if you want to remove that legacy block.
Diff is opt-in and prints a git-style ANSI summary directly on stdout. Preferences are stored in
`.ctfmem/<challenge>/shell-session.json`; no terminal transcript is written.

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
