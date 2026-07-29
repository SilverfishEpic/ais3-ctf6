param(
    [string]$Challenge,
    [string]$ApiUrl,
    [string]$Actor
)

# Runs the same guided setup as POSIX; no key or model secret is persisted.
$arguments = @("setup")
if ($Challenge) { $arguments += @("--challenge", $Challenge) }
if ($ApiUrl) { $arguments += @("--api-url", $ApiUrl) }
if ($Actor) { $arguments += @("--actor", $Actor) }
& ctfmem @arguments
