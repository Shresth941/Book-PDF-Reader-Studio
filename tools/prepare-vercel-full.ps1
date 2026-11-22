param(
    [string]$FrontendBuildDirectory = 'frontend/dist'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Assert-ProjectPath([string]$path) {
    $absolute = [IO.Path]::GetFullPath($path)
    if (-not $absolute.StartsWith($projectDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A deployment source must be inside this project.'
    }
    $cursor = Get-Item -LiteralPath $absolute -Force
    while ($cursor -and $cursor.FullName.Length -ge $projectDirectory.Length) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Links and junctions cannot enter the deployment: $($cursor.FullName)"
        }
        $cursor = $cursor.Parent
        if (-not $cursor) { break }
    }
    return $absolute
}

function Get-SafeFiles([string]$directory) {
    $directory = Assert-ProjectPath $directory
    $items = @(Get-ChildItem -LiteralPath $directory -Force -Recurse)
    if ($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
        throw "Source tree contains a link or junction: $directory"
    }
    return @($items | Where-Object { -not $_.PSIsContainer })
}

$appDirectory = Assert-ProjectPath (Join-Path $projectDirectory 'backend/app')
$templateDirectory = Assert-ProjectPath (Join-Path $projectDirectory 'hosting/vercel')
$modelsDirectory = Assert-ProjectPath (Join-Path $projectDirectory 'backend/models')
if (-not [IO.Path]::IsPathRooted($FrontendBuildDirectory)) {
    $FrontendBuildDirectory = Join-Path $projectDirectory $FrontendBuildDirectory
}
$buildDirectory = Assert-ProjectPath $FrontendBuildDirectory
if (-not (Test-Path -LiteralPath (Join-Path $buildDirectory 'index.html'))) {
    throw 'Build the hosted frontend first and pass its output directory. This script does not build or test.'
}
foreach ($required in @('hosting/app.py', 'hosting/queue_worker.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $appDirectory $required))) {
        throw "Hosted entrypoint is missing: app/$required"
    }
}
$appFiles = @(Get-SafeFiles $appDirectory | Where-Object { $_.Extension -eq '.py' })
if ($appFiles.Count -gt 1000 -or ($appFiles | Measure-Object Length -Sum).Sum -gt 20MB) {
    throw 'Backend code exceeds the staging file or size bound.'
}
$frontendFiles = @(Get-SafeFiles $buildDirectory)
if ($frontendFiles.Count -gt 100 -or ($frontendFiles | Measure-Object Length -Sum).Sum -gt 25MB) {
    throw 'Frontend assets exceed the staging file or size bound.'
}
foreach ($file in $frontendFiles) {
    $relative = $file.FullName.Substring($buildDirectory.Length + 1).Replace('\', '/')
    if ($relative -notmatch '^(index\.html|pdf-reader-icon\.svg|assets/[A-Za-z0-9_.-]+\.(js|css|svg|woff2?))$') {
        throw "Unexpected frontend artifact; refusing upload: $relative"
    }
}
# A new stage is used on every invocation. No existing folder is removed.
$stageDirectory = Join-Path $projectDirectory ('tmp/vercel-full-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stageDirectory -Force | Out-Null
$manifest = New-Object 'System.Collections.Generic.List[object]'
function Record-StageFile([string]$relative) {
    $destination = Join-Path $stageDirectory $relative
    $file = Get-Item -LiteralPath $destination
    $manifest.Add([ordered]@{path=$relative.Replace('\', '/'); bytes=$file.Length; sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()})
}
function Copy-StageFile([string]$source, [string]$relative) {
    $source = Assert-ProjectPath $source
    if ($relative -match '(^|[\\/])\.\.([\\/]|$)' -or [IO.Path]::IsPathRooted($relative)) {
        throw 'Invalid relative staging path.'
    }
    $destination = Join-Path $stageDirectory $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
    Record-StageFile $relative
}
function Write-StageJson([string]$relative, $value) {
    $destination = Join-Path $stageDirectory $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    [IO.File]::WriteAllText($destination, ($value | ConvertTo-Json -Depth 20), $utf8)
    Record-StageFile $relative
}
foreach ($file in $appFiles) {
    $relative = $file.FullName.Substring($appDirectory.Length + 1)
    Copy-StageFile $file.FullName ('app/' + $relative)
}
foreach ($file in $frontendFiles) {
    $relative = $file.FullName.Substring($buildDirectory.Length + 1)
    Copy-StageFile $file.FullName ('public/' + $relative)
}
$modelNames = @('ATTRIBUTION.txt', 'config.json', 'LICENSE', 'manifest.json', 'model.bin',
    'postprocess.sh', 'preprocess.sh', 'README.md', 'shared_vocabulary.json', 'source.spm', 'target.spm')
foreach ($direction in @('en-hi', 'hi-en')) {
    $modelDirectory = Join-Path $modelsDirectory ('marian/' + $direction)
    $modelFiles = @(Get-SafeFiles $modelDirectory)
    if ($modelFiles.Count -ne $modelNames.Count -or ($modelFiles | Measure-Object Length -Sum).Sum -gt 100MB) {
        throw "Unexpected converted model size/file count: $direction"
    }
    $modelManifest = Get-Content -LiteralPath (Join-Path $modelDirectory 'manifest.json') -Raw | ConvertFrom-Json
    foreach ($file in $modelFiles) {
        if ($file.Name -notin $modelNames -or $file.DirectoryName -ne $modelDirectory) {
            throw "Unexpected model content: $direction/$($file.Name)"
        }
        if ($file.Name -ne 'manifest.json') {
            $expected = $modelManifest.files.PSObject.Properties[$file.Name].Value
            $actual = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            if (-not $expected -or $expected -ne $actual) { throw "Model manifest mismatch: $direction/$($file.Name)" }
        }
        Copy-StageFile $file.FullName ('models/marian/' + $direction + '/' + $file.Name)
    }
}
$tessdataDirectory = Join-Path $modelsDirectory 'tessdata'
$tessFiles = @(Get-SafeFiles $tessdataDirectory)
if ($tessFiles.Count -ne 4) { throw 'Unexpected OCR language-data file count.' }
$tessManifest = Get-Content -LiteralPath (Join-Path $tessdataDirectory 'manifest.json') -Raw | ConvertFrom-Json
$tessHashes = @{}
foreach ($item in $tessManifest.files) {
    $tessHashes[[IO.Path]::GetFileName($item.Path)] = $item.Hash.ToLowerInvariant()
}
foreach ($name in @('eng.traineddata', 'hin.traineddata', 'osd.traineddata')) {
    $source = Join-Path $tessdataDirectory $name
    $actual = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $tessHashes[$name]) { throw "OCR language-data manifest mismatch: $name" }
    Copy-StageFile $source ('models/tessdata/' + $name)
}
# The original tessdata manifest has a personal absolute Windows path. Retain
# provenance and hashes while deliberately removing that machine-specific path.
Write-StageJson 'models/tessdata/manifest.json' @{
    source=$tessManifest.source; revision=$tessManifest.revision; files=$tessHashes
}
foreach ($name in @('pyproject.toml', 'vercel.json', 'package.json', '.python-version', 'build_native.py', 'linux-wheel-provenance.json')) {
    Copy-StageFile (Join-Path $templateDirectory $name) $name
}
# A readable direct-pins file accompanies pyproject; it intentionally makes no
# claim to be a full transitive lock. Vercel reads pyproject dependencies.
$pins = @(Get-Content -LiteralPath (Join-Path $templateDirectory 'pyproject.toml') | ForEach-Object {
    if ($_ -match '^\s+"([^"\s]+==[^"\s]+)",$') { $Matches[1] }
})
[IO.File]::WriteAllText((Join-Path $stageDirectory 'requirements.txt'), (($pins -join "`n") + "`n"), $utf8)
Record-StageFile 'requirements.txt'
Write-StageJson '.vercel/project.json' @{
    projectId='prj_GlbirATgumB5qch3hkh1rabzhKS7'; orgId='team_A5B5fhvfGzPtqkbuLR219uxe';
    projectName='book-pdf-reader-studio'; settings=@{framework='fastapi'; nodeVersion='24.x'}
}
$ignore = @('.env*', '**/.env*', '**/__pycache__/**', '**/*.pyc', '**/*.sqlite*', '**/*.db', '**/originals/**', '**/backups/**', '**/tests/**', '**/test-fixtures/**', '.git/**', '.venv/**')
[IO.File]::WriteAllText((Join-Path $stageDirectory '.vercelignore'), (($ignore -join "`n") + "`n"), $utf8)
Record-StageFile '.vercelignore'
if (($manifest | Measure-Object bytes -Sum).Sum -gt 250MB) {
    throw 'Sanitized source stage exceeds 250 MB; nothing has been deployed.'
}
# Audit records stay outside the upload directory and contain relative paths.
$manifestFile = $stageDirectory + '.manifest.json'
[IO.File]::WriteAllText($manifestFile, ($manifest | ConvertTo-Json -Depth 5), $utf8)
[ordered]@{stage=$stageDirectory; manifest=$manifestFile; files=$manifest.Count; bytes=($manifest | Measure-Object bytes -Sum).Sum;
    deployed=$false; note='Source assembly only. Set hosted secrets, enable Fluid/large-function support, and deploy this directory only.'} | ConvertTo-Json
