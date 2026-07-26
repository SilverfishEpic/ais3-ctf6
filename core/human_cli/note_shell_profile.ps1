param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [Parameter(Mandatory = $true)]
    [string]$HelperScript,

    [Parameter(Mandatory = $true)]
    [string]$WorkspaceDir
)

$script:NoteRoundsFile = Join-Path $WorkspaceDir "rounds.jsonl"
$script:NoteTranscriptDir = Join-Path $WorkspaceDir "transcripts"
$script:NoteTranscriptPath = Join-Path $script:NoteTranscriptDir ("session-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")

New-Item -ItemType Directory -Force -Path $script:NoteTranscriptDir | Out-Null

$ProgressPreference = "SilentlyContinue"
$PSDefaultParameterValues["Invoke-WebRequest:UseBasicParsing"] = $true
$PSDefaultParameterValues["Invoke-RestMethod:UseBasicParsing"] = $true
Remove-Item Alias:curl -Force -ErrorAction SilentlyContinue
Remove-Item Alias:wget -Force -ErrorAction SilentlyContinue

# AutoNote state: tracks the last command for post-execution auto-note processing
$script:LastCommandForAutoNote = $null

# AutoNote toggle: set to $false to disable auto-note entirely
$script:AutoNoteEnabled = $true

# Skip count: > 0 means skip the next N auto-note evaluations
$script:AutoNoteSkipCount = 0

# ──────────────────────────────────────────────────────────
# skip / resume  — temporarily pause or re-enable auto-note
# ──────────────────────────────────────────────────────────
function global:skip {
    <#
    .SYNOPSIS
        Skip the next auto-note evaluation. Type 'skip' at the prompt;
        the following command will not trigger an auto-note update.
        Use 'resume' to cancel the skip.
    #>
    if (-not $script:AutoNoteEnabled) {
        Write-Host "[note] Auto-note is already disabled. Use 'note auto on' or 'resume' to re-enable."
        return
    }
    $script:AutoNoteSkipCount = 1
    Write-Host "[note] Next auto-note will be skipped. Type 'resume' to cancel."
}

function global:resume {
    <#
    .SYNOPSIS
        Re-enable auto-note after skip, or re-enable a disabled auto-note.
    #>
    if ($script:AutoNoteSkipCount -gt 0 -or -not $script:AutoNoteEnabled) {
        $script:AutoNoteSkipCount = 0
        if (-not $script:AutoNoteEnabled) { $script:AutoNoteEnabled = $true }
        Write-Host "[note] Auto-note resumed."
    } else {
        Write-Host "[note] Auto-note is already active."
    }
}

function Write-NoteRound {
    param(
        [string]$Type,
        [hashtable]$Data
    )

    $round = [ordered]@{
        round_id = [guid]::NewGuid().ToString()
        timestamp_utc = [DateTime]::UtcNow.ToString("o")
        type = $Type
        cwd = (Get-Location).ProviderPath
    }

    foreach ($key in $Data.Keys) {
        $round[$key] = $Data[$key]
    }

    $json = $round | ConvertTo-Json -Compress -Depth 8
    Add-Content -LiteralPath $script:NoteRoundsFile -Value $json -Encoding UTF8
}

function note {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Text
    )

    if ($Text.Count -gt 0) {
        $first = $Text[0].ToLower()
        $cwd = (Get-Location).ProviderPath

        # ── note auto on / off ─────────────────────────────────────────
        if ($first -eq 'auto' -and $Text.Count -ge 2) {
            $sub = $Text[1].ToLower()
            if ($sub -eq 'on') {
                $script:AutoNoteEnabled = $true
                $script:AutoNoteSkipCount = 0
                Write-Host "[note] Auto-note enabled."
            } elseif ($sub -eq 'off') {
                $script:AutoNoteEnabled = $false
                Write-Host "[note] Auto-note disabled. Use 'note auto on' or 'resume' to re-enable."
            } else {
                Write-Host "[note] Usage: note auto on | off"
            }
            return
        }

        # ── note auto (show status) ────────────────────────────────────
        if ($first -eq 'auto') {
            $status = if ($script:AutoNoteEnabled) { "enabled (skip count: $($script:AutoNoteSkipCount))" } else { "disabled" }
            Write-Host "[note] Auto-note is $status."
            return
        }

        # ── note query [challenge_id] [--layer X] [--section Y] ─────────
        if ($first -eq 'query') {
            $queryArgs = @("--query", "--cwd", $cwd)
            $remaining = $Text[1..($Text.Count - 1)]
            $challengeSet = $false
            $i = 0
            while ($i -lt $remaining.Count) {
                $arg = $remaining[$i]
                if ($arg -eq '--layer' -and ($i + 1) -lt $remaining.Count) {
                    $queryArgs += "--query-layer"
                    $queryArgs += $remaining[$i + 1]
                    $i += 2
                } elseif ($arg -eq '--section' -and ($i + 1) -lt $remaining.Count) {
                    $queryArgs += "--query-section"
                    $queryArgs += $remaining[$i + 1]
                    $i += 2
                } elseif ($arg -eq '--json') {
                    $queryArgs += "--json"
                    $i += 1
                } elseif (-not $challengeSet) {
                    $queryArgs += "--query-challenge"
                    $queryArgs += $arg
                    $challengeSet = $true
                    $i += 1
                } else {
                    $i += 1
                }
            }
            & $PythonExe $HelperScript @queryArgs
            return
        }

        # ── note list ────────────────────────────────────────────────────
        if ($first -eq 'list') {
            $listArgs = @("--list-challenges")
            if ($Text.Count -ge 2 -and $Text[1] -eq '--json') {
                $listArgs += "--json"
            }
            & $PythonExe $HelperScript @listArgs
            return
        }

        # ── note capture <command> ───────────────────────────────────────
        if ($first -eq 'capture') {
            if ($Text.Count -lt 2) {
                Write-Host "[capture] Usage: note capture <command>" -ForegroundColor Red
                return
            }
            $cmdText = ($Text[1..($Text.Count - 1)]) -join ' '
            $captureArgs = @("--capture", "--command-text", $cmdText, "--cwd", $cwd)
            & $PythonExe $HelperScript @captureArgs
            return
        }

        # ── note read [file_path] ────────────────────────────────────────
        if ($first -eq 'read') {
            $readArgs = @("--read", "--cwd", $cwd)
            if ($Text.Count -ge 2) {
                $readArgs += "--file-path"
                $readArgs += $Text[1]
            }
            if ($Text.Count -ge 3 -and $Text[2] -eq '--json') {
                $readArgs += "--json"
            }
            & $PythonExe $HelperScript @readArgs
            return
        }

        # ── note guess [--count N] [--seed N] ────────────────────────────
        if ($first -eq 'guess') {
            $guessArgs = @("--guess", "--cwd", $cwd)
            $remaining = $Text[1..($Text.Count - 1)]
            $i = 0
            while ($i -lt $remaining.Count) {
                $arg = $remaining[$i]
                if ($arg -eq '--count' -and ($i + 1) -lt $remaining.Count) {
                    $guessArgs += "--guess-count"
                    $guessArgs += $remaining[$i + 1]
                    $i += 2
                } elseif ($arg -eq '--seed' -and ($i + 1) -lt $remaining.Count) {
                    $guessArgs += "--guess-seed"
                    $guessArgs += $remaining[$i + 1]
                    $i += 2
                } elseif ($arg -eq '--json') {
                    $guessArgs += "--json"
                    $i += 1
                } else {
                    $i += 1
                }
            }
            & $PythonExe $HelperScript @guessArgs
            return
        }

        # ── note select [-N] ───────────────────────────────────────────
        if ($first -eq 'select') {
            $count = 10  # default
            if ($Text.Count -ge 2 -and $Text[1] -match '^-(\d+)$') {
                $count = [int]$Matches[1]
            }
            # Read recent command rounds
            $rounds = @()
            if (Test-Path $script:NoteRoundsFile) {
                $allLines = Get-Content -LiteralPath $script:NoteRoundsFile -Tail $count
                foreach ($line in $allLines) {
                    $round = $line | ConvertFrom-Json -ErrorAction SilentlyContinue
                    if ($round -and $round.type -eq 'command') {
                        $rounds += $round
                    }
                }
            }
            if ($rounds.Count -eq 0) {
                Write-Host "[note] No recent commands found."
                return
            }
            # Display numbered list (reverse so newest appears at bottom, like terminal history)
            Write-Host "`nRecent commands (newest last):" -ForegroundColor Cyan
            for ($i = 0; $i -lt $rounds.Count; $i++) {
                $num = $i + 1
                Write-Host "[$num] $($rounds[$i].command)" -ForegroundColor Yellow
            }
            Write-Host "`nSelect commands by number (e.g. '1,3,5' or '1-3' or 'all'), or '/done' to cancel:" -ForegroundColor Cyan
            $input = Read-Host "Select"
            if ($input -eq '/done' -or [string]::IsNullOrWhiteSpace($input)) {
                Write-Host "[note] Cancelled."
                return
            }
            # Parse selection
            $selected = @()
            if ($input.Trim().ToLower() -eq 'all') {
                $selected = 0..($rounds.Count - 1)
            } else {
                $parts = $input -split ',' | ForEach-Object { $_.Trim() }
                foreach ($part in $parts) {
                    if ($part -match '^(\d+)-(\d+)$') {
                        $start = [int]$Matches[1]
                        $end   = [int]$Matches[2]
                        for ($j = $start; $j -le $end; $j++) {
                            if ($j -ge 1 -and $j -le $rounds.Count) { $selected += ($j - 1) }
                        }
                    } elseif ($part -match '^\d+$') {
                        $idx = [int]$part - 1
                        if ($idx -ge 0 -and $idx -lt $rounds.Count) { $selected += $idx }
                    }
                }
                $selected = $selected | Sort-Object -Unique
            }
            if ($selected.Count -eq 0) {
                Write-Host "[note] No valid selections."; return
            }
            # Build a summary note from selected commands
            $lines = @("Overview of selected commands:")
            foreach ($idx in $selected) {
                $r = $rounds[$idx]
                $ts = if ($r.timestamp_utc) { " [$($r.timestamp_utc)]" } else { "" }
                $lines += "- $($r.command)$ts"
            }
            $noteBody = $lines -join [Environment]::NewLine
            Write-Host "`nSending note to AI..." -ForegroundColor Cyan
            $noteBody | & $PythonExe $HelperScript --send-note --note-stdin --cwd $cwd
            return
        }

        # ── Default: inline note text ──────────────────────────────────
        $noteText = ($Text -join " ").Trim()
        & $PythonExe $HelperScript --send-note --note-text $noteText --cwd $cwd
        return
    }

    Write-Host "Enter note text. Finish with '/done' on its own line."
    $lines = New-Object System.Collections.Generic.List[string]
    while ($true) {
        $line = Read-Host "note"
        if ($line -eq "/done") {
            break
        }
        $lines.Add($line)
    }

    $noteText = ($lines -join [Environment]::NewLine).Trim()
    if (-not $noteText) {
        Write-Host "[note] No note text provided."
        return
    }

    $cwd = (Get-Location).ProviderPath
    $noteText | & $PythonExe $HelperScript --send-note --note-stdin --cwd $cwd
}

if (Get-Module -ListAvailable -Name PSReadLine) {
    Import-Module PSReadLine -ErrorAction SilentlyContinue
    Set-PSReadLineOption -HistorySaveStyle SaveNothing -AddToHistoryHandler {
        param([string]$Line)

        if (-not [string]::IsNullOrWhiteSpace($Line)) {
            $trimmed = $Line.Trim()
            $lower = $trimmed.ToLower()

            # Meta-commands: these have dedicated functions; don't log or auto-note
            $metaCommands = @('skip', 'resume')
            if ($lower -in $metaCommands) {
                return $false
            }

            # note commands: log as command round (for history recall) but don't trigger auto-note
            if ($lower -eq 'note' -or $lower -like 'note *') {
                Write-NoteRound "command" @{ command = $trimmed }
                return $true
            }

            Write-NoteRound "command" @{ command = $trimmed }
            if ($script:AutoNoteEnabled) {
                $script:LastCommandForAutoNote = $trimmed
            }
        }

        return $true
    }
}

# AutoNote: evaluate a command after it completes (called from prompt).
# Uses .NET Process.Start with hidden window to avoid any visual disruption.
function Invoke-AutoNote {
    param([string]$Command)
    if (-not $Command) { return }

    # Respect skip count: decrement and return without processing
    if ($script:AutoNoteSkipCount -gt 0) {
        $script:AutoNoteSkipCount--
        return
    }

    # Quick pre-filter: skip obviously trivial commands
    $firstWord = ($Command -split '\s+')[0].ToLower()
    $trivialWords = @('cd', 'ls', 'dir', 'clear', 'cls', 'exit', 'code', 'notepad')
    if ($firstWord -in $trivialWords) { return }
    if ($Command.Length -le 2) { return }

    # Launch hidden Python process ??truly silent, no window, no job object
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = "`"$HelperScript`" --auto-note --command-text `"$Command`" --cwd `"$((Get-Location).ProviderPath)`""
    $psi.CreateNoWindow = $true
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $null = [System.Diagnostics.Process]::Start($psi)
}

# Override prompt to trigger AutoNote after command execution.
# Process.Start returns instantly ??zero delay, zero visual noise.
function global:prompt {
    if ($script:LastCommandForAutoNote) {
        $cmd = $script:LastCommandForAutoNote
        $script:LastCommandForAutoNote = $null
        Invoke-AutoNote -Command $cmd
    }
    "PS $((Get-Location).ProviderPath)> "
}

try {
    Start-Transcript -LiteralPath $script:NoteTranscriptPath -Append | Out-Null
} catch {
    Write-Warning "Could not start transcript: $($_.Exception.Message)"
}

Write-Host "Interactive note shell started."
Write-Host "AutoNote enabled: important commands are automatically recorded to rolling notes."
Write-Host "  skip           - Skip the next auto-note (temporary pause)"
Write-Host "  resume         - Re-enable auto-note after skip"
Write-Host "  note auto on   - Turn auto-note on"
Write-Host "  note auto off  - Turn auto-note off"
Write-Host "  note select -N - Pick from the last N commands to create an overview note"
Write-Host "  note query [id] [--layer fact|assumption|guessing] [--section name]"
Write-Host "                 - Query challenge memory from Memory Core"
Write-Host "  note list      - List all known challenges"
Write-Host "  note capture <cmd> - Execute command, capture output, and ingest into Memory Core"
Write-Host "  note read [path]   - Read and display state.yaml (default: current challenge)"
Write-Host "  note guess [--count N] [--seed N] - Get technique suggestions from template"
Write-Host "  note '...'     - Send a manual AI note"
Write-Host "  note           - Enter multi-line note mode (/done to finish)"
Write-Host "Command history saved to $script:NoteRoundsFile"
Write-Host "Full transcript saved to $script:NoteTranscriptPath"