param()
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$buildDirectory = Join-Path $projectDirectory 'frontend/launcher-dist'
if (-not (Test-Path -LiteralPath (Join-Path $buildDirectory 'index.html'))) {
    throw 'Build the launcher first: npm --prefix frontend run build:launcher'
}
# A new staging directory prevents old workspace artifacts from being uploaded.
$stageDirectory = Join-Path $projectDirectory ('tmp/vercel-launcher-' + [guid]::NewGuid().ToString('N'))
if ((Get-Item -LiteralPath $buildDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Launcher build directory must not be a link or junction.'
}
$items = @(Get-ChildItem -LiteralPath $buildDirectory -Force -Recurse)
if ($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
    throw 'Launcher build contains a link or junction; refusing to stage it.'
}
$files = @($items | Where-Object { -not $_.PSIsContainer })
if ($files.Count -gt 20 -or ($files | Measure-Object -Property Length -Sum).Sum -gt 2MB) {
    throw 'Launcher exceeds the expected static-file size or count.'
}
foreach ($file in $files) {
    $relative = $file.FullName.Substring($buildDirectory.Length + 1).Replace('\', '/')
    if ($relative -notmatch '^(index\.html|assets/[A-Za-z0-9_-]+\.(js|css))$') {
        throw "Unexpected launcher file: $relative"
    }
}
$staticDirectory = Join-Path $stageDirectory '.vercel/output/static'
New-Item -ItemType Directory -Path (Join-Path $staticDirectory 'assets') -Force | Out-Null
$manifest = @()
foreach ($file in $files) {
    $relative = $file.FullName.Substring($buildDirectory.Length + 1).Replace('\', '/')
    $destination = Join-Path $staticDirectory $relative
    Copy-Item -LiteralPath $file.FullName -Destination $destination
    $manifest += [ordered]@{path=$relative; bytes=$file.Length; sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-StageJson($relative, $value) {
    [IO.File]::WriteAllText((Join-Path $stageDirectory $relative), ($value | ConvertTo-Json -Depth 20), $utf8)
}
Write-StageJson '.vercel/project.json' @{
    projectId='prj_GlbirATgumB5qch3hkh1rabzhKS7'; orgId='team_A5B5fhvfGzPtqkbuLR219uxe';
    projectName='book-pdf-reader-studio'; settings=@{framework=$null}
}
Write-StageJson 'vercel.json' @{framework=$null}
Write-StageJson '.vercel/output/config.json' @{
    version=3; routes=@(
        @{src='/(.*)'; headers=@{
            'Content-Security-Policy'="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
            'X-Content-Type-Options'='nosniff'; 'Referrer-Policy'='no-referrer';
            'X-Frame-Options'='DENY'; 'Permissions-Policy'='camera=(), microphone=(), geolocation=()'
        }; continue=$true},
        @{handle='filesystem'}
    )
}
[IO.File]::WriteAllText((Join-Path $stageDirectory '.vercelignore'), "*`n!vercel.json`n", $utf8)
# Keep audit records outside the upload directory.
$manifestFile = $stageDirectory + '.manifest.json'
[IO.File]::WriteAllText($manifestFile, ($manifest | ConvertTo-Json -Depth 5), $utf8)
[ordered]@{stage=$stageDirectory; manifest=$manifestFile; files=$files.Count; bytes=($files | Measure-Object -Property Length -Sum).Sum} | ConvertTo-Json
