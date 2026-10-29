param([switch]$Check)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = 'docs/context/merkle.json'
$supportFiles = @(
    'AGENTS.md',
    'docs/context/REPO_MAP.md',
    'docs/context/DECISIONS.md',
    'docs/forward-observations.md',
    'docs/trading-product-brief.md',
    'docs/dhan-api-key-callbacks.md',
    'data/config/reliance-observation-2026-10-27.json',
    'data/config/reliance-observation-2026-10-28.json',
    'web/server/trading-service.mjs',
    'web/server/trading-service.test.mjs',
    'web/server/dhan-account.mjs',
    'web/server/dhan-account.test.mjs',
    'web/src/TradingWorkspace.tsx',
    'web/src/TradingDemo.tsx',
    'rust/crates/quant-telemetry/src/bin/telemetry_local.rs',
    'src/quant/acquisition/forward_observations.py',
    'src/quant/apps/forward_observations.py',
    'src/quant/apps/dhan_candle_availability.py',
    'src/quant/apps/dhan_quote_preflight.py',
    'src/quant/apps/cockpit_cache.py',
    'src/quant/apps/dhan_account_cache.py',
    'tests/test_forward_observations.py',
    'tests/test_dhan_candle_availability.py',
    'tests/test_dhan_quote_preflight.py',
    'tests/test_cockpit_cache.py',
    'tests/test_dhan_account_cache.py',
    'tests/test_dhan_oauth_callback.py',
    'tools/start-cockpit.ps1',
    'tools/dhan_oauth_callback.py',
    'tools/repo-context.ps1'
)
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-TextHash([string]$Value) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash($utf8.GetBytes($Value))
        return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    }
    finally { $algorithm.Dispose() }
}

function Get-OrdinalNames($Values) {
    [string[]]$names = @($Values)
    [Array]::Sort($names, [StringComparer]::Ordinal)
    return $names
}

$tracked = @(& git -C $repoRoot -c core.quotePath=false ls-files --cached)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inventory Git-tracked files.' }
$paths = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
foreach ($path in @($tracked) + $supportFiles) {
    if ($path.StartsWith('"')) { throw "Git-escaped filename is unsupported: $path" }
    if ($path -eq $manifestPath) { continue }
    $absolute = Join-Path $repoRoot $path
    if (Test-Path -LiteralPath $absolute -PathType Leaf) {
        $item = Get-Item -LiteralPath $absolute -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Symlink/reparse point is unsupported: $path"
        }
        [void]$paths.Add($path)
    }
    elseif (Test-Path -LiteralPath $absolute) {
        throw "Expected a regular file, found another entry type: $path"
    }
}

$files = [ordered]@{}
$treeEntries = @{ '.' = @{} }
foreach ($path in (Get-OrdinalNames $paths)) {
    $absolute = Join-Path $repoRoot $path
    $contentHash = (Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash.ToLowerInvariant()
    $blobHash = Get-TextHash ("blob`0" + $contentHash)
    $files[$path] = [ordered]@{
        content_sha256 = $contentHash
        sha256 = $blobHash
    }
    $parts = $path.Split('/')
    $parent = '.'
    for ($i = 0; $i -lt $parts.Length - 1; $i++) {
        $child = if ($parent -eq '.') { $parts[$i] } else { "$parent/$($parts[$i])" }
        if (-not $treeEntries.ContainsKey($child)) { $treeEntries[$child] = @{} }
        $treeEntries[$parent][$parts[$i]] = @{ kind = 'tree'; path = $child }
        $parent = $child
    }
    $treeEntries[$parent][$parts[-1]] = @{ kind = 'blob'; path = $path }
}

$computedTrees = @{}
# Children are always longer paths than their parents.
foreach ($directory in ($treeEntries.Keys | Sort-Object { $_.Length } -Descending)) {
    $payload = New-Object System.Text.StringBuilder
    [void]$payload.Append("tree`0")
    $children = @(
        foreach ($name in (Get-OrdinalNames $treeEntries[$directory].Keys)) {
            $entry = $treeEntries[$directory][$name]
            $hash = if ($entry.kind -eq 'blob') {
                $files[$entry.path].sha256
            } else {
                $computedTrees[$entry.path].sha256
            }
            [void]$payload.Append("$($entry.kind)`0$name`0$hash`n")
            [ordered]@{ name = $name; kind = $entry.kind; sha256 = $hash }
        }
    )
    $computedTrees[$directory] = [ordered]@{
        sha256 = Get-TextHash $payload.ToString()
        children = $children
    }
}
$directories = [ordered]@{}
foreach ($directory in (Get-OrdinalNames $computedTrees.Keys)) {
    $directories[$directory] = $computedTrees[$directory]
}
$manifest = [ordered]@{
    schema_version = 1
    algorithm = 'SHA-256; UTF-8; ordinal child-name order; lowercase hex'
    blob_rule = 'SHA256(UTF8("blob\0" + content_sha256))'
    tree_rule = 'SHA256(UTF8("tree\0" + concat(kind + "\0" + name + "\0" + sha256 + "\n")))'
    scope = 'Existing Git-tracked working files plus explicit supportFiles; excludes this manifest'
    root_sha256 = $computedTrees['.'].sha256
    file_count = $files.Count
    files = $files
    directories = $directories
}
$json = ($manifest | ConvertTo-Json -Depth 20) + "`n"
$destination = Join-Path $repoRoot $manifestPath

if ($Check) {
    if (-not (Test-Path -LiteralPath $destination)) {
        Write-Output 'Context index missing. Run this script without -Check to create it.'
        exit 1
    }
    $previousText = [IO.File]::ReadAllText($destination, $utf8)
    if ($previousText -ceq $json) {
        Write-Output "Context index current: $($files.Count) files; root $($manifest.root_sha256)"
        exit 0
    }
    $previous = $previousText | ConvertFrom-Json
    Write-Output 'Context index stale. Inspect changed files and update summaries before refreshing.'
    $oldFiles = @{}
    foreach ($property in $previous.files.PSObject.Properties) {
        $oldFiles[$property.Name] = $property.Value.sha256
    }
    foreach ($path in (Get-OrdinalNames $files.Keys)) {
        if (-not $oldFiles.ContainsKey($path)) { Write-Output "ADDED $path" }
        elseif ($oldFiles[$path] -cne $files[$path].sha256) { Write-Output "CHANGED $path" }
    }
    foreach ($path in (Get-OrdinalNames $oldFiles.Keys)) {
        if (-not $files.Contains($path)) { Write-Output "REMOVED $path" }
    }
    $oldTrees = @{}
    foreach ($property in $previous.directories.PSObject.Properties) {
        $oldTrees[$property.Name] = $property.Value.sha256
    }
    foreach ($directory in (Get-OrdinalNames $directories.Keys)) {
        if (-not $oldTrees.ContainsKey($directory) -or
            $oldTrees[$directory] -cne $directories[$directory].sha256) {
            Write-Output "BRANCH $directory"
        }
    }
    foreach ($directory in (Get-OrdinalNames $oldTrees.Keys)) {
        if (-not $directories.Contains($directory)) { Write-Output "REMOVED BRANCH $directory" }
    }
    Write-Output "Current root: $($manifest.root_sha256)"
    exit 1
}

[IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
[IO.File]::WriteAllText($destination, $json, $utf8)
Write-Output "Indexed $($files.Count) files; root $($manifest.root_sha256)"
