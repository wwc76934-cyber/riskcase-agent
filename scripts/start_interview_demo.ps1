param([ValidateRange(1024,65535)][int]$Port = 8777)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$runner = Join-Path $PSScriptRoot 'run_project.ps1'
Push-Location -LiteralPath $repoRoot
try {
    Write-Host "Interview demo: http://127.0.0.1:$Port/investigations/page"
    Write-Host 'Synthetic data, scripted control, real MCP, no paid model requests.'
    Write-Host 'Local token: runs/interview-workbench/auth.token (not a model API key).'
    & $runner -m app.cli.workbench --port $Port --directory runs/interview-workbench --demo-data runs/interview-demo-data
    exit $LASTEXITCODE
}
finally { Pop-Location }
