param([int]$Port = 8000, [switch]$OpenBrowser, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory 'backend/.venv-local/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Run Setup-Local.ps1 once to install the local runtime.'
}
$builtIndex = Join-Path $projectDirectory 'frontend/dist/index.html'
if (-not $SkipBuild -or -not (Test-Path -LiteralPath $builtIndex)) {
    Push-Location (Join-Path $projectDirectory 'frontend')
    try {
        if (Get-Command node -ErrorAction SilentlyContinue) {
            & node node_modules/typescript/bin/tsc --noEmit
            if ($LASTEXITCODE -ne 0) { throw 'Frontend type check failed. Run Setup-Local.ps1 if dependencies are missing.' }
            & node node_modules/vite/bin/vite.js build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } elseif (Test-Path -LiteralPath $builtIndex) {
            Write-Warning 'Node.js is unavailable. Opening the existing build; install Node.js to include frontend source updates.'
        } else { throw 'Install Node.js and run Setup-Local.ps1 before starting the app.' }
    }
    finally { Pop-Location }
}
$env:RUNTIME_MODE = 'local'
$env:DATA_DIR = Join-Path $projectDirectory 'backend/local-data'
$env:MODELS_DIR = Join-Path $projectDirectory 'backend/models'
$env:ALLOWED_ORIGINS = "http://127.0.0.1:$Port,http://localhost:$Port,http://127.0.0.1:5173,http://localhost:5173"
Write-Host "PDF Reader: http://127.0.0.1:$Port"
Write-Host 'Keep this process running. Press Ctrl+C to stop. Saved work remains on disk.'
if ($OpenBrowser) { Start-Process "http://127.0.0.1:$Port" }
Push-Location (Join-Path $projectDirectory 'backend')
try { & $pythonExecutable -m uvicorn app.main:app --host 127.0.0.1 --port $Port }
finally { Pop-Location }
