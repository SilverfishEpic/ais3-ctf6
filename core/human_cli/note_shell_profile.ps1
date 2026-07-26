param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [Parameter(Mandatory = $true)]
    [string]$HelperScript,

    [Parameter(Mandatory = $true)]
    [string]$WorkspaceDir
)

function note {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Text
    )

    $cwd = (Get-Location).ProviderPath
    if ($Text.Count -gt 0) {
        $noteText = ($Text -join " ").Trim()
        & $PythonExe $HelperScript --send-note --note-text $noteText --cwd $cwd
        return
    }

    Write-Host "Enter note text. Finish with '/done' on its own line."
    $lines = New-Object System.Collections.Generic.List[string]
    while ($true) {
        $line = Read-Host "note"
        if ($line -eq "/done") { break }
        $lines.Add($line)
    }

    $noteText = ($lines -join [Environment]::NewLine).Trim()
    if (-not $noteText) {
        Write-Host "[note] No note text provided."
        return
    }

    $noteText | & $PythonExe $HelperScript --send-note --note-stdin --cwd $cwd
}

Write-Host "[note] Memory Core note command loaded."
