$PythonArgs = @($args)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$baseAvailable = $false
$cfg = Join-Path $projectRoot '.venv\pyvenv.cfg'
if (Test-Path -LiteralPath $cfg) {
    $entry = Get-Content -LiteralPath $cfg | Where-Object { $_ -like 'executable = *' } | Select-Object -First 1
    if ($entry) { $baseAvailable = Test-Path -LiteralPath ($entry.Substring(13).Trim()) }
}
if ($baseAvailable) {
    try {
        $probe = & $localPython -c 'import sys; print(sys.version_info[:2])' 2>$null
        $baseAvailable = ($LASTEXITCODE -eq 0)
    } catch { $baseAvailable = $false }
}
$priorPath = $env:PYTHONPATH
try {
    $addons = Join-Path $projectRoot '.venv-service-addons'
    if (Test-Path -LiteralPath $addons) { $env:PYTHONPATH = $addons + [IO.Path]::PathSeparator + $priorPath }
    if ((Test-Path -LiteralPath $localPython) -and $baseAvailable) {
        & $localPython -X utf8 -B @PythonArgs
    } else {
        $runtime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        $packages = Join-Path $projectRoot '.venv\Lib\site-packages'
        if (!(Test-Path -LiteralPath $runtime) -or !(Test-Path -LiteralPath $packages)) { throw 'Python unavailable; recreate the project environment from the documented dependencies.' }
        $env:PYTHONPATH = $packages + [IO.Path]::PathSeparator + $env:PYTHONPATH
        & $runtime -X utf8 -B @PythonArgs
    }
    $resultCode = $LASTEXITCODE
} finally { $env:PYTHONPATH = $priorPath }
exit $resultCode
