# shellcheck shell=bash
#
# note_shell_profile.sh — inject ``note`` / ``skip`` / ``resume`` shell
#                         functions and auto-note hook into bash or zsh.
#
# This script is sourced by a shell launched with --rcfile (bash) or
# --rcs (zsh).  It first loads the user's normal rc file, then sets up
# everything needed for the note workflow.
#
# Environment variables injected by the Python launcher:
#   NOTE_PYTHON_EXE     — path to the Python interpreter
#   NOTE_HELPER_SCRIPT  — path to main.py
#   NOTE_WORKSPACE_DIR  — project root
# -------------------------------------------------------------------------

# ---- Guard: only bash / zsh -------------------------------------------
if [ -z "${BASH_VERSION-}" ] && [ -z "${ZSH_VERSION-}" ]; then
  return 0
fi

# ---- Source the user's normal rc file first ----------------------------
if [ -n "${BASH_VERSION-}" ]; then
  # bash --rcfile replaces the normal rc, so we do it ourselves
  for _rc in "$HOME/.bashrc" "$HOME/.bash_profile"; do
    [ -f "$_rc" ] && source "$_rc" && break
  done
  unset _rc
elif [ -n "${ZSH_VERSION-}" ]; then
  # zsh --rcs similarly overrides .zshrc; restore it
  [ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc"
fi

# ---- Configuration defaults -------------------------------------------
: "${NOTE_PYTHON_EXE:=python3}"
: "${NOTE_HELPER_SCRIPT:=}"
: "${NOTE_WORKSPACE_DIR:=.}"

_NOTE_ROUNDS_FILE="$NOTE_WORKSPACE_DIR/rounds.jsonl"
_NOTE_SKIP_COUNT=0
_NOTE_AUTO_ENABLED=1
_NOTE_LAST_CMD=""

# ---- Utility: write a JSON round to the rounds file -------------------
_note_write_round() {
  local type_="$1" cmd_="$2"
  local round_id ts cwd
  round_id=$(python3 -c "import uuid,datetime,timezone; print(uuid.uuid4())" 2>/dev/null || echo "unknown-$$-$(date +%s)")
  ts=$(date -u +"%Y-%m-%dT%H:%M:%S.%NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
  cwd=$(pwd 2>/dev/null || echo "$NOTE_WORKSPACE_DIR")

  # Build a single-line JSON fragment for the command field
  local cmd_json
  cmd_json=$(printf '%s' "$cmd_" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo "\"$cmd_\"")

  printf '{"round_id":"%s","timestamp_utc":"%s","type":"%s","cwd":"%s","command":%s}\n' \
    "$round_id" "$ts" "$type_" "$cwd" "$cmd_json" >> "$_NOTE_ROUNDS_FILE"
}

# ---- note function ----------------------------------------------------
note() {
  local cwd
  cwd=$(pwd 2>/dev/null || echo "$NOTE_WORKSPACE_DIR")

  if [ $# -eq 0 ]; then
    # Multi-line mode
    echo "Enter note text. Finish with '/done' on its own line."
    local lines="" line
    while IFS= read -r line; do
      if [ "$line" = "/done" ]; then break; fi
      lines="$lines$line"$'\n'
    done
    local note_text
    note_text=$(printf '%s' "$lines" | sed '/^$/d')
    if [ -n "$note_text" ]; then
      printf '%s' "$note_text" | "$NOTE_PYTHON_EXE" "$NOTE_HELPER_SCRIPT" \
        --send-note --note-stdin --cwd "$cwd"
    else
      echo "[note] No note text provided." >&2
    fi
    return
  fi

  local first
  first=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')

  # note auto on | off | (status)
  if [ "$first" = "auto" ]; then
    if [ $# -ge 2 ]; then
      local sub
      sub=$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')
      if [ "$sub" = "on" ]; then
        _NOTE_AUTO_ENABLED=1
        _NOTE_SKIP_COUNT=0
        echo "[note] Auto-note enabled."
      elif [ "$sub" = "off" ]; then
        _NOTE_AUTO_ENABLED=0
        echo "[note] Auto-note disabled. Use 'note auto on' or 'resume' to re-enable."
      else
        echo "[note] Usage: note auto on | off" >&2
      fi
    else
      if [ "$_NOTE_AUTO_ENABLED" -eq 1 ]; then
        echo "[note] Auto-note is enabled (skip count: $_NOTE_SKIP_COUNT)."
      else
        echo "[note] Auto-note is disabled."
      fi
    fi
    return
  fi

  # note select [-N]
  if [ "$first" = "select" ]; then
    local count=10
    if [ $# -ge 2 ] && printf '%s' "$2" | grep -qE '^-?[0-9]+$'; then
      count=${2#-}  # strip leading dash
    fi
    _note_select "$count" "$cwd"
    return
  fi

  # Default: inline note text
  local note_text
  note_text=$(printf '%s' "$*")
  "$NOTE_PYTHON_EXE" "$NOTE_HELPER_SCRIPT" \
    --send-note --note-text "$note_text" --cwd "$cwd"
}

# ---- note select implementation (interactive) -------------------------
_note_select() {
  local count=$1 cwd=$2
  local rounds=() line

  if [ ! -f "$_NOTE_ROUNDS_FILE" ]; then
    echo "[note] No recent commands found." >&2
    return
  fi

  # Read last N command rounds
  while IFS= read -r line; do
    rounds+=("$line")
  done < <(grep '"type":"command"' "$_NOTE_ROUNDS_FILE" 2>/dev/null | tail -n "$count")

  if [ ${#rounds[@]} -eq 0 ]; then
    echo "[note] No recent commands found." >&2
    return
  fi

  echo ""
  echo "Recent commands (newest last):"
  local i r cmd_text
  for i in "${!rounds[@]}"; do
    cmd_text=$(printf '%s' "${rounds[$i]}" | python3 -c "
import sys,json
try: d=json.loads(sys.stdin.read()); print(d.get('command','?'))
except: print('?')
" 2>/dev/null || echo "?")
    printf "[%d] %s\n" $((i+1)) "$cmd_text"
  done

  echo ""
  echo "Select commands by number (e.g. '1,3,5' or '1-3' or 'all'), or '/done' to cancel:"
  read -r input

  if [ "$input" = "/done" ] || [ -z "$input" ]; then
    echo "[note] Cancelled." >&2
    return
  fi

  # Parse selection
  local -a selected=()
  if printf '%s' "$input" | grep -qi '^all$'; then
    selected=( $(seq 0 $((${#rounds[@]} - 1))) )
  else
    # Split on comma
    local old_ifs="$IFS" part start end j
    IFS=','
    for part in $input; do
      part=$(printf '%s' "$part" | xargs)
      if printf '%s' "$part" | grep -qE '^[0-9]+-[0-9]+$'; then
        start=${part%-*}
        end=${part#*-}
        for j in $(seq "$start" "$end"); do
          if [ "$j" -ge 1 ] && [ "$j" -le "${#rounds[@]}" ]; then
            selected+=($((j-1)))
          fi
        done
      elif printf '%s' "$part" | grep -qE '^[0-9]+$'; then
        if [ "$part" -ge 1 ] && [ "$part" -le "${#rounds[@]}" ]; then
          selected+=($((part-1)))
        fi
      fi
    done
    IFS="$old_ifs"
  fi

  if [ ${#selected[@]} -eq 0 ]; then
    echo "[note] No valid selections." >&2
    return
  fi

  # Build note from selected commands
  local note_body="Overview of selected commands:"
  local idx r_ ts_
  for idx in "${selected[@]}"; do
    r_="${rounds[$idx]}"
    ts_=$(printf '%s' "$r_" | python3 -c "
import sys,json
try: d=json.loads(sys.stdin.read()); print(d.get('timestamp_utc',''))
except: print('')
" 2>/dev/null || echo "")
    cmd_text=$(printf '%s' "$r_" | python3 -c "
import sys,json
try: d=json.loads(sys.stdin.read()); print(d.get('command','?'))
except: print('?')
" 2>/dev/null || echo "?")
    if [ -n "$ts_" ]; then
      note_body="$note_body"$'\n'"- $cmd_text [$ts_]"
    else
      note_body="$note_body"$'\n'"- $cmd_text"
    fi
  done

  echo ""
  echo "Sending note to AI..." >&2
  printf '%s' "$note_body" | "$NOTE_PYTHON_EXE" "$NOTE_HELPER_SCRIPT" \
    --send-note --note-stdin --cwd "$cwd"
}

# ---- skip / resume ----------------------------------------------------
skip() {
  if [ "$_NOTE_AUTO_ENABLED" -eq 0 ]; then
    echo "[note] Auto-note is already disabled. Use 'note auto on' or 'resume' to re-enable." >&2
    return
  fi
  _NOTE_SKIP_COUNT=1
  echo "[note] Next auto-note will be skipped. Type 'resume' to cancel."
}

resume() {
  if [ "$_NOTE_SKIP_COUNT" -gt 0 ] || [ "$_NOTE_AUTO_ENABLED" -eq 0 ]; then
    _NOTE_SKIP_COUNT=0
    _NOTE_AUTO_ENABLED=1
    echo "[note] Auto-note resumed."
  else
    echo "[note] Auto-note is already active."
  fi
}

# ---- Auto-note: capture command after execution -----------------------
#
# bash: PROMPT_COMMAND runs before every prompt.  We compare the last
#       history entry to what we already processed.
# zsh:  precmd is the equivalent hook.
#
_note_auto_eval() {
  local cmd="$1"
  [ -z "$cmd" ] && return

  # Skip if auto-note is disabled
  [ "$_NOTE_AUTO_ENABLED" -eq 0 ] && return

  # Skip count
  if [ "$_NOTE_SKIP_COUNT" -gt 0 ]; then
    _NOTE_SKIP_COUNT=$((_NOTE_SKIP_COUNT - 1))
    return
  fi

  # Quick pre-filter: skip obviously trivial commands
  local first_word
  first_word=$(printf '%s' "$cmd" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')
  case "$first_word" in
    cd|ls|dir|clear|cls|exit|code|notepad|which|man|pwd|echo)
      return ;;
  esac
  [ ${#cmd} -le 2 ] && return

  # Log the command round
  _note_write_round "command" "$cmd"

  # Launch Python auto-note in background (fire-and-forget)
  if [ -n "$NOTE_HELPER_SCRIPT" ]; then
    "$NOTE_PYTHON_EXE" "$NOTE_HELPER_SCRIPT" \
      --auto-note --command-text "$cmd" --cwd "$(pwd)" >/dev/null 2>&1 &
  fi
}

_note_capture_and_eval() {
  local current
  current=$(HISTTIMEFORMAT= history 1 2>/dev/null | sed 's/^[[:space:]]*[0-9]*[[:space:]]*//' || echo "")
  # Trim whitespace
  # shellcheck disable=SC2001
  current=$(printf '%s' "$current" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

  # Only process if it's a new command
  if [ -n "$current" ] && [ "$current" != "$_NOTE_LAST_CMD" ]; then
    _NOTE_LAST_CMD="$current"
    _note_auto_eval "$current"
  fi
}

if [ -n "${BASH_VERSION-}" ]; then
  # For bash: we capture in PROMPT_COMMAND
  _NOTE_BASE_PROMPT_COMMAND="${PROMPT_COMMAND:-}"
  PROMPT_COMMAND="_note_capture_and_eval; ${_NOTE_BASE_PROMPT_COMMAND}"
  unset _NOTE_BASE_PROMPT_COMMAND
fi

if [ -n "${ZSH_VERSION-}" ]; then
  # For zsh: use precmd hook
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd _note_capture_and_eval
fi

# ---- Start transcript (script(1) equivalent is tricky; skip for now) --

# ---- Greeting ---------------------------------------------------------
echo "Interactive note shell started."
echo "AutoNote enabled: important commands are automatically recorded to rolling notes."
echo "  skip           - Skip the next auto-note (temporary pause)"
echo "  resume         - Re-enable auto-note after skip"
echo "  note auto on   - Turn auto-note on"
echo "  note auto off  - Turn auto-note off"
echo "  note select -N - Pick from the last N commands to create an overview note"
echo "  note '...'     - Send a manual AI note"
echo "  note           - Enter multi-line note mode (/done to finish)"
echo "Command history saved to $_NOTE_ROUNDS_FILE"
