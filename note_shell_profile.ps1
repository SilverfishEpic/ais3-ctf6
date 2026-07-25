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
        $noteText = ($Text -join " ").Trim()
        $cwd = (Get-Location).ProviderPath
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
            Write-NoteRound "command" @{ command = $Line }
            $script:LastCommandForAutoNote = $Line
        }

        return $true
    }
}

# AutoNote: evaluate a command after it completes (called from prompt).
# Uses .NET Process.Start with hidden window to avoid any visual disruption.
function Invoke-AutoNote {
    param([string]$Command)
    if (-not $Command) { return }

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
Write-Host "Use 'note ...' or 'note' for manual AI notes."
Write-Host "Command history saved to $script:NoteRoundsFile"
Write-Host "Full transcript saved to $script:NoteTranscriptPath"
