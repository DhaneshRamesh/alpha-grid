param([switch]$NoBrowser, [switch]$Offline, [switch]$Online)

# One foreground supervisor for the existing loopback services. Ctrl+C stops
# only the processes started by this invocation. Online is read-only Quote-17.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$started = @()
$previousPythonPath = $env:PYTHONPATH
$previousDhanToken = $env:DHAN_ACCESS_TOKEN
$previousDhanApiKey = $env:DHAN_API_KEY
$previousDhanApiSecret = $env:DHAN_API_SECRET
$previousDhanClientId = $env:DHAN_CLIENT_ID
$previousBootstrap = $env:ALPHA_GRID_UI_BOOTSTRAP_TOKEN
if ($Offline -and $Online) { throw 'Offline and Online are mutually exclusive' }

function Start-LocalGateway([bool]$ReadOnlyFeed, [int]$Attempt) {
  $gatewayArgs = @('--security-id', '2885', '--approval-ledger', '.local/paper-approvals.qp', '--stop-file', $stopFile)
  $env:PYTHONPATH = 'src'
  try {
    $env:ALPHA_GRID_UI_SESSION_SECRET = & .\.venv\Scripts\python.exe -c "from pathlib import Path; from quant.acquisition.dhan import _private_directory, _verify_windows_private_acl; p=Path('data/.secrets/cockpit-session-key'); _private_directory(p.parent); _verify_windows_private_acl(p); print(p.read_text(encoding='ascii'))"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($env:ALPHA_GRID_UI_SESSION_SECRET)) { throw 'Private cockpit session key unavailable' }
    if ($ReadOnlyFeed) {
      $env:DHAN_CLIENT_ID = & .\.venv\Scripts\python.exe -c "from pathlib import Path; from quant.acquisition.dhan import _verify_windows_private_acl; p=Path('data/.secrets/dhan-client-id'); _verify_windows_private_acl(p); print(p.read_text(encoding='ascii').strip())"
      if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($env:DHAN_CLIENT_ID)) { throw 'Private Dhan client identity unavailable' }
      $tokenPath = 'data/.secrets/dhan-access-token'
      $metadataPath = 'data/.secrets/dhan-access-token.meta.json'
      & .\.venv\Scripts\python.exe -c "from pathlib import Path; from quant.acquisition.dhan import _verify_windows_private_acl; [_verify_windows_private_acl(Path(p)) for p in ('data/.secrets/dhan-access-token','data/.secrets/dhan-access-token.meta.json')]"
      if ($LASTEXITCODE -ne 0) { throw 'Private Dhan token storage unavailable' }
      $tokenMetadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
      $env:DHAN_ACCESS_TOKEN = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $tokenPath)).Trim()
      $digest = [Security.Cryptography.SHA256]::Create()
      try { $tokenHash = ([BitConverter]::ToString($digest.ComputeHash([Text.Encoding]::UTF8.GetBytes($env:DHAN_ACCESS_TOKEN)))).Replace('-', '').ToLowerInvariant() } finally { $digest.Dispose() }
      if ($tokenMetadata.schema_version -ne 'dhan-consent-token-v1' -or $tokenHash -ne $tokenMetadata.token_sha256 -or [DateTimeOffset]::Parse($tokenMetadata.expires_at_utc) -le [DateTimeOffset]::UtcNow.AddMinutes(1)) { throw 'Private Dhan token expired or metadata invalid' }
      $journal = (Resolve-Path -LiteralPath 'data/.secrets/quote-journal/reliance-quote-2885.qj').Path
      $calendar = "data/config/reliance-observation-$(Get-Date -Format 'yyyy-MM-dd').json"
      if (-not (Test-Path -LiteralPath $calendar -PathType Leaf)) { throw 'Dated observation calendar unavailable' }
      $gatewayArgs += @('--online', '--dhan-quote-readonly', '--quote-journal', $journal,
        '--instrument-master', 'data/instruments/dhan-scrip-master-2026-10-24.csv',
        '--master-manifest', 'data/instruments/dhan-scrip-master-2026-10-24.manifest.json',
        '--calendar', $calendar)
      $env:ALPHA_GRID_ENABLE_DHAN_QUOTE = '1'
    }
    $errorLog = Join-Path $logs "gateway-$tag-$Attempt.err.log"
    $process = Start-Process -FilePath $rust -ArgumentList $gatewayArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "gateway-$tag-$Attempt.out.log") -RedirectStandardError $errorLog -PassThru
    return @{ Process = $process; ErrorLog = $errorLog }
  } finally {
    Remove-Item Env:ALPHA_GRID_UI_SESSION_SECRET, Env:DHAN_CLIENT_ID, Env:DHAN_ACCESS_TOKEN, Env:ALPHA_GRID_ENABLE_DHAN_QUOTE -ErrorAction SilentlyContinue
  }
}

function Last-FeedStage($WebSession) {
  if (-not $WebSession) { return '' }
  try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/snapshot' -WebSession $WebSession -UseBasicParsing -TimeoutSec 2
    $snapshot = $response.Content | ConvertFrom-Json
    if ($snapshot.payload.journal_blocked) { return 'STOPPED:STORAGE_FAILED' }
    $events = @($snapshot.payload.audit | Where-Object { $_.kind -eq 'FEED_LIFECYCLE' -and $_.detail -match '^STOPPED:[A-Z0-9_]+$' })
    if ($events.Count -eq 0) { return '' }
    return $events[$events.Count - 1].detail
  } catch { return '' }
}
Push-Location $root
try {
  $rust = (Resolve-Path -LiteralPath 'rust/target/debug/telemetry_local.exe').Path
  $node = (Resolve-Path -LiteralPath '.local/toolchains/node-v24.22.0-win-x64/node.exe').Path
  $vite = (Resolve-Path -LiteralPath 'web/node_modules/vite/bin/vite.js').Path
  $trade = (Resolve-Path -LiteralPath 'web/server/trading-service.mjs').Path
  foreach ($port in @(8765, 8767, 5173)) {
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
      throw "Local port $port is already in use; no duplicate Alpha Grid services started"
    }
  }
  $logs = Join-Path $root '.local/alpha-grid-logs'
  New-Item -ItemType Directory -Path $logs -Force | Out-Null
  $tag = "$PID-$(Get-Date -Format 'yyyyMMddHHmmss')"
  $stopFile = Join-Path $logs "stop-$tag.flag"

  # The account service reads only the ACL-checked private token file. Vite
  # receives no Dhan credential; Rust receives it only for Online mode.
  Remove-Item Env:DHAN_ACCESS_TOKEN, Env:DHAN_API_KEY, Env:DHAN_API_SECRET, Env:DHAN_CLIENT_ID -ErrorAction SilentlyContinue
  $env:PYTHONPATH = 'src'
  # The offline cache reader also needs this identity to verify saved responses.
  # The client ID is passed only to the account service, never to Vite.
  $env:DHAN_CLIENT_ID = & .\.venv\Scripts\python.exe -c "from pathlib import Path; from quant.acquisition.dhan import _verify_windows_private_acl; p=Path('data/.secrets/dhan-client-id'); _verify_windows_private_acl(p); print(p.read_text(encoding='ascii').strip())"
  if ($LASTEXITCODE -ne 0) { Remove-Item Env:DHAN_CLIENT_ID -ErrorAction SilentlyContinue }
  $env:ALPHA_GRID_UI_SESSION_SECRET = & .\.venv\Scripts\python.exe -c "from pathlib import Path; from quant.acquisition.dhan import _private_directory, _verify_windows_private_acl; p=Path('data/.secrets/cockpit-session-key'); _private_directory(p.parent); _verify_windows_private_acl(p); print(p.read_text(encoding='ascii'))"
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($env:ALPHA_GRID_UI_SESSION_SECRET)) {
    throw 'Private cockpit session key unavailable'
  }
  $sessionSecret = $env:ALPHA_GRID_UI_SESSION_SECRET
  $bootstrapBytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bootstrapBytes) } finally { $rng.Dispose() }
  $bootstrap = ([BitConverter]::ToString($bootstrapBytes)).Replace('-', '').ToLowerInvariant()
  [Array]::Clear($bootstrapBytes, 0, $bootstrapBytes.Length)
  $env:ALPHA_GRID_UI_BOOTSTRAP_TOKEN = $bootstrap
  $env:ALPHA_GRID_ACCOUNT_SYNC = '1'
  if ($Offline) { $env:ALPHA_GRID_ACCOUNT_OFFLINE = '1' }
  if ($Online) { $env:ALPHA_GRID_ONLINE_MODE = '1' }
  $started += Start-Process -FilePath $node -ArgumentList @($trade) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "trade-$tag.out.log") -RedirectStandardError (Join-Path $logs "trade-$tag.err.log") -PassThru
  Remove-Item Env:ALPHA_GRID_ACCOUNT_SYNC, Env:ALPHA_GRID_ACCOUNT_OFFLINE, Env:ALPHA_GRID_ONLINE_MODE, Env:DHAN_CLIENT_ID, Env:ALPHA_GRID_UI_BOOTSTRAP_TOKEN, Env:ALPHA_GRID_UI_SESSION_SECRET -ErrorAction SilentlyContinue
  $started += Start-Process -FilePath $node -ArgumentList @($vite, '--host', '127.0.0.1', '--port', '5173', '--strictPort') -WorkingDirectory (Join-Path $root 'web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "web-$tag.out.log") -RedirectStandardError (Join-Path $logs "web-$tag.err.log") -PassThru

  $attempt = 0
  $gateway = $null
  try {
    if ($Online) {
      $attempt = 1
      $gateway = Start-LocalGateway $true $attempt
    } else {
      $gateway = Start-LocalGateway $false 0
    }
  } catch {
    if (-not $Online) { throw }
    Write-Warning 'Online preflight blocked; starting CACHE_ONLY gateway. Check private local diagnostics.'
    $gateway = Start-LocalGateway $false 0
    $attempt = 0
  }
  $started += $gateway.Process

  $healthy = @{}
  $checks = @{
    web = 'http://127.0.0.1:5173/'
    settings = 'http://127.0.0.1:8767/trade/status'
    gateway = 'http://127.0.0.1:8765/api/health'
  }
  for ($readinessAttempt = 0; $readinessAttempt -lt 40; $readinessAttempt++) {
    foreach ($name in $checks.Keys) {
      if ($healthy[$name]) { continue }
      try {
        $response = Invoke-WebRequest -Uri $checks[$name] -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $healthy[$name] = $true }
      } catch { }
    }
    if ($healthy['web'] -and $healthy['settings'] -and $healthy['gateway']) { break }
    if ($started[1].HasExited) { break }
    Start-Sleep -Milliseconds 250
  }
  if (-not $healthy['web']) { throw "Cockpit frontend failed to start; see $logs" }
  foreach ($name in @('gateway', 'settings')) {
    if (-not $healthy[$name]) { Write-Warning "$name unavailable; the cockpit will show disconnected/unavailable. See $logs" }
  }
  $localWebSession = $null
  if ($Online -and $healthy['gateway']) {
    try {
      $sessionResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/session' -Method Post -Headers @{ Origin = 'http://127.0.0.1:5173'; Authorization = "Bearer $sessionSecret" } -SessionVariable localWebSession -UseBasicParsing -TimeoutSec 2
      if ($sessionResponse.StatusCode -ne 204) { throw 'Local session unavailable' }
    } catch { Write-Warning 'Local gateway status authentication unavailable; automatic feed restart disabled.' }
  }
  if ($Online) {
    Write-Host 'Alpha Grid: http://127.0.0.1:5173/ (read-only ONLINE requested; NO TRADE and broker orders disabled)'
  } elseif ($Offline) {
    Write-Host 'Alpha Grid: http://127.0.0.1:5173/ (DATA_ONLY / NO TRADE; verified local cache only)'
  } else {
    Write-Host 'Alpha Grid: http://127.0.0.1:5173/ (DATA_ONLY / NO TRADE; approved read-only account refresh only when credentials pass)'
  }
  Write-Host 'Press Ctrl+C here to stop the services started by this launcher.'
  if (-not $NoBrowser) { Start-Process ("http://127.0.0.1:5173/#bootstrap=" + $bootstrap) | Out-Null }
  $bootstrap = $null
  $handledStage = ''
  $transient = @('STOPPED:DNS_FAILED', 'STOPPED:TCP_FAILED', 'STOPPED:CONNECT_TIMED_OUT', 'STOPPED:TRANSPORT_EOF', 'STOPPED:SOCKET_FAILED', 'STOPPED:IDLE_TIMED_OUT')
  while (-not $started[1].HasExited) {
    if ($Online -and $attempt -gt 0) {
      $accountStatus = $null
      try {
        $accountStatus = (Invoke-WebRequest -Uri 'http://127.0.0.1:8767/trade/status' -UseBasicParsing -TimeoutSec 2).Content | ConvertFrom-Json
      } catch { }
      if ($accountStatus.account -eq 'REAUTHENTICATION_REQUIRED') {
        Write-Warning 'Dhan account authentication requires renewal; stopping read-only feed.'
        New-Item -ItemType File -Path $stopFile -Force | Out-Null
        $gateway.Process.WaitForExit(5000) | Out-Null
        if (-not $gateway.Process.HasExited) { throw 'Read-only gateway did not stop after authentication failure' }
        Remove-Item -LiteralPath $stopFile -ErrorAction SilentlyContinue
        $gateway = Start-LocalGateway $false 0
        $started += $gateway.Process
        $attempt = 0
        continue
      }
      $stage = Last-FeedStage $localWebSession
      if ($stage -and $stage -ne $handledStage) {
        $handledStage = $stage
        if ($transient -contains $stage -and $attempt -lt 3 -and (Get-Date).TimeOfDay -lt [TimeSpan]::Parse('15:14:00')) {
          Write-Warning "Read-only feed $stage; one bounded fresh-preflight restart follows."
          New-Item -ItemType File -Path $stopFile -Force | Out-Null
          $gateway.Process.WaitForExit(5000) | Out-Null
          if (-not $gateway.Process.HasExited) {
            Stop-Process -Id $gateway.Process.Id -ErrorAction SilentlyContinue
            $gateway.Process.WaitForExit(5000) | Out-Null
          }
          if (-not $gateway.Process.HasExited) { throw 'Previous gateway did not stop; no duplicate process started' }
          Remove-Item -LiteralPath $stopFile -ErrorAction SilentlyContinue
          $backoffSeconds = @(2, 5)[$attempt - 1]
          Start-Sleep -Seconds $backoffSeconds
          try {
            $attempt += 1
            $gateway = Start-LocalGateway $true $attempt
            $started += $gateway.Process
            $handledStage = ''
          } catch {
            Write-Warning 'Fresh online preflight blocked; no further feed attempt.'
            $gateway = Start-LocalGateway $false 0
            $started += $gateway.Process
            $attempt = 0
          }
        }
      }
      if ($gateway.Process.HasExited) {
        Write-Warning 'Read-only feed gateway exited; no unverified reconnect.'
        $gateway = Start-LocalGateway $false 0
        $started += $gateway.Process
        $attempt = 0
      }
    }
    Start-Sleep -Seconds 1
  }
  throw "Cockpit frontend stopped; see $logs"
} finally {
  Remove-Item Env:ALPHA_GRID_UI_SESSION_SECRET -ErrorAction SilentlyContinue
  $env:ALPHA_GRID_UI_BOOTSTRAP_TOKEN = $previousBootstrap
  Remove-Item Env:ALPHA_GRID_ACCOUNT_SYNC -ErrorAction SilentlyContinue
  Remove-Item Env:ALPHA_GRID_ACCOUNT_OFFLINE -ErrorAction SilentlyContinue
  Remove-Item Env:ALPHA_GRID_ONLINE_MODE -ErrorAction SilentlyContinue
  $env:DHAN_ACCESS_TOKEN = $previousDhanToken
  $env:DHAN_API_KEY = $previousDhanApiKey
  $env:DHAN_API_SECRET = $previousDhanApiSecret
  $env:DHAN_CLIENT_ID = $previousDhanClientId
  $env:PYTHONPATH = $previousPythonPath
  $sessionSecret = $null
  $localWebSession = $null
  if ($stopFile) { New-Item -ItemType File -Path $stopFile -Force -ErrorAction SilentlyContinue | Out-Null }
  if ($gateway -and -not $gateway.Process.HasExited) { $gateway.Process.WaitForExit(5000) | Out-Null }
  foreach ($process in $started) {
    if ($process -and -not $process.HasExited) {
      Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
      $process.WaitForExit(5000) | Out-Null
    }
  }
  if ($stopFile) { Remove-Item -LiteralPath $stopFile -ErrorAction SilentlyContinue }
  Pop-Location
}
