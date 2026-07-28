param(
    [string]$ConfigPath = ".ctfmem/core-config.json",
    [string]$BaseUrl = "https://llm-api.zoolab.org/v1",
    [string]$Model = "ais3/nemotron-3-ultra-550b",
    [double]$Timeout = 300
)

# No API key is stored here. Provide AIS3_API_KEY only to the server process.
$directory = Split-Path -Parent $ConfigPath
if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
@{
    ais3_base_url = $BaseUrl
    ais3_model = $Model
    ais3_timeout = $Timeout
    runs_dir = "./runs"
    templates_dir = "./templates"
} | ConvertTo-Json | Set-Content -Encoding utf8 $ConfigPath
Write-Output "Saved non-secret Memory Core defaults to $ConfigPath."
