param([string]$PythonExecutable = 'python', [switch]$InstallOcrData, [switch]$InstallTranslationModels)
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$version = & $PythonExecutable -c 'import sys; print(str(sys.version_info.major)+"."+str(sys.version_info.minor))'
if ($LASTEXITCODE -ne 0 -or $version -ne '3.12') { throw 'Pass -PythonExecutable with a working Python 3.12 executable.' }
$environmentDirectory = Join-Path $projectDirectory 'backend/.venv-local'
if (-not (Test-Path -LiteralPath $environmentDirectory)) {
    & $PythonExecutable -m venv $environmentDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local environment.' }
}
$localPython = Join-Path $environmentDirectory 'Scripts/python.exe'
& $localPython -m pip install -r (Join-Path $projectDirectory 'backend/requirements-local.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location (Join-Path $projectDirectory 'frontend')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
if ($InstallOcrData) {
    $ocrDirectory = Join-Path $projectDirectory 'backend/models/tessdata'
    New-Item -ItemType Directory -Force -Path $ocrDirectory | Out-Null
    $revision = '87416418657359cb625c412a48b6e1d6d41c29bd'
    foreach ($language in @('hin','eng','osd')) {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$revision/$language.traineddata" -OutFile (Join-Path $ocrDirectory "$language.traineddata")
    }
    Write-Host 'Hindi/English language files installed. The Tesseract executable must also be installed on this computer.'
}
if ($InstallTranslationModels) {
    & $localPython -m pip install --only-binary=:all: --require-hashes -r (Join-Path $projectDirectory 'backend/requirements-translation.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Optional translation runtime installation failed.' }
    & $localPython (Join-Path $projectDirectory 'backend/tools/install_marian.py') --models-dir (Join-Path $projectDirectory 'backend/models')
    if ($LASTEXITCODE -ne 0) { throw 'Public local model setup failed. Existing originals and reviewed work are unchanged.' }
}
Write-Host 'Core runtime ready. Run ./Start-Local.ps1. Use -InstallTranslationModels for public offline draft translation models; every result requires human review.'
