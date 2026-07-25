"""Shell profile detection and launch helpers.

Determines which shell the user prefers and knows how to inject the
``note`` profile (the ``note``, ``skip``, ``resume`` shell functions +
auto-note hook) regardless of platform.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Shell-kind constants
# ──────────────────────────────────────────────────────────────────────

SHELL_PWSH = "pwsh"
SHELL_POWERSHELL = "powershell"
SHELL_BASH = "bash"
SHELL_ZSH = "zsh"


# ──────────────────────────────────────────────────────────────────────
# Profile metadata
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShellProfile:
    """Describes how to launch a shell with the note profile injected."""

    #: Executable name as it appears on PATH (e.g. "pwsh", "bash")
    exe_name: str
    #: Filename of the profile asset inside ``core/human_cli/``
    profile_asset: str

    @property
    def is_powershell(self) -> bool:
        return self.exe_name in (SHELL_POWERSHELL, SHELL_PWSH)


# ── Registry ──────────────────────────────────────────────────────────

_PROFILES: dict[str, ShellProfile] = {
    SHELL_PWSH: ShellProfile(exe_name="pwsh", profile_asset="note_shell_profile.ps1"),
    SHELL_POWERSHELL: ShellProfile(exe_name="powershell", profile_asset="note_shell_profile.ps1"),
    SHELL_BASH: ShellProfile(exe_name="bash", profile_asset="note_shell_profile.sh"),
    SHELL_ZSH: ShellProfile(exe_name="zsh", profile_asset="note_shell_profile.sh"),
}

# OS → ordered fallback list (tried left-to-right if $SHELL is empty)
_FALLBACK_ORDER: dict[str, tuple[str, ...]] = {
    "Linux": (SHELL_BASH, SHELL_ZSH),
    "Darwin": (SHELL_ZSH, SHELL_BASH),
    "Windows": (SHELL_PWSH, SHELL_POWERSHELL, SHELL_BASH),
}


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def detect_shell(shell_hint: str | None = None) -> ShellProfile | None:
    """Return the best-matching *ShellProfile* for the current environment.

    Priority:
    1. *shell_hint* — explicit override (e.g. from ``--shell`` CLI arg)
    2. ``$SHELL`` environment variable
    3. OS-appropriate fallback (first one found on ``PATH``)
    """
    candidates: list[str] = []

    if shell_hint:
        candidates.append(shell_hint)

    env_shell = os.environ.get("SHELL")
    if env_shell:
        candidates.append(Path(env_shell).stem)

    system = platform.system()
    candidates.extend(_FALLBACK_ORDER.get(system, (SHELL_BASH,)))

    seen: set[str] = set()
    for name in candidates:
        key = _normalise(name)
        if key in seen:
            continue
        seen.add(key)

        profile = _PROFILES.get(key) or _PROFILES.get(name)
        if profile is not None and shutil.which(profile.exe_name):
            return profile

    return None


def resolve_profile_path(profile: ShellProfile, script_dir: Path) -> Path:
    """Return the absolute path to the profile asset file."""
    return script_dir / "core" / "human_cli" / profile.profile_asset


def build_launch_cmd(
    profile: ShellProfile,
    profile_path: Path,
    helper_script: Path,
    workspace_dir: Path,
    python_exe: str,
) -> tuple[list[str], dict[str, str]]:
    """Build ``(command_args, extra_env)`` for ``subprocess.call``.

    The caller should merge *extra_env* into the subprocess environment.
    All profiles receive ``NOTE_PYTHON_EXE``, ``NOTE_HELPER_SCRIPT`` and
    ``NOTE_WORKSPACE_DIR`` so the shell profile script knows where the
    Python helper lives.
    """
    env = {
        "NOTE_PYTHON_EXE": python_exe,
        "NOTE_HELPER_SCRIPT": str(helper_script.resolve()),
        "NOTE_WORKSPACE_DIR": str(workspace_dir),
    }

    if profile.is_powershell:
        source_cmd = (
            f". '{profile_path}' "
            f"-PythonExe '{python_exe}' "
            f"-HelperScript '{helper_script.resolve()}' "
            f"-WorkspaceDir '{workspace_dir}'"
        )
        args = [
            profile.exe_name,
            "-NoLogo",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            source_cmd,
        ]
        return args, env

    if profile.exe_name == SHELL_BASH:
        return [profile.exe_name, "--rcfile", str(profile_path)], env

    if profile.exe_name == SHELL_ZSH:
        return [profile.exe_name, "--rcs", str(profile_path)], env

    # Fallback: just launch the shell bare
    return [profile.exe_name], env


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────

def _normalise(name: str) -> str:
    """Strip path and extension from a shell name."""
    return Path(name).stem.lower()
