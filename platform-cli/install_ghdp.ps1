Param(
  [string]$Repo = $env:GHDP_REPO,
  [string]$Version = $env:GHDP_VERSION,
  [string]$InstallDir = $env:GHDP_INSTALL_DIR,
  [string]$RuntimeEnvPath = $env:GHDP_RUNTIME_ENV_PATH,
  [switch]$NoPath
)

# One-step installer for GHDP CLI (binary-first, no pipx required)
# Downloads the correct binary from GitHub Releases and installs it to:
#   %LOCALAPPDATA%\ghdp\bin\ghdp.exe   (default)
#
# Env overrides:
#   GHDP_REPO="org/repo"            (required)
#   GHDP_VERSION="latest"|"vX.Y.Z"  (default: latest)
#   GHDP_INSTALL_DIR="C:\path"      (default: %LOCALAPPDATA%\ghdp\bin)
#   GHDP_BINARY_PATH="C:\path\ghdp.exe" (install from a pre-downloaded local binary; skips GitHub download/auth)
#   GHDP_MANAGED_INSTALL="1"        (persist managed install state)

function Write-Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Ok($msg)   { Write-Host $msg -ForegroundColor Green }
function Write-Err($msg)  { Write-Host $msg -ForegroundColor Red }
function Test-LocalBinaryMode { return -not [string]::IsNullOrWhiteSpace($env:GHDP_BINARY_PATH) }
function Test-ManagedInstall {
  $value = "$env:GHDP_MANAGED_INSTALL".ToLower()
  return @("1","true","yes","on") -contains $value
}
function Get-ManagedInstallMarkerPath {
  return Join-Path $HOME ".ghdp\managed-install"
}
function Write-ManagedInstallMarker {
  $marker = Get-ManagedInstallMarkerPath
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $marker) | Out-Null
  Set-Content -Path $marker -Value "managed"
}
function Remove-ManagedInstallMarker {
  $marker = Get-ManagedInstallMarkerPath
  if (Test-Path $marker -PathType Leaf) {
    Remove-Item -Force $marker -ErrorAction SilentlyContinue
  }
}
function Get-InstallStatePath {
  return Join-Path $HOME ".ghdp\install-state.json"
}
function Write-InstallState {
  param([string]$Mode)
  $statePath = Get-InstallStatePath
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statePath) | Out-Null
  $payload = @{
    schema_version = "1.0"
    install_mode = "$Mode"
  } | ConvertTo-Json -Depth 4
  try {
    $payload | Set-Content -Path $statePath -Encoding UTF8 -Force
  } catch {
    # Best effort recovery for previously locked ACL/protected files.
    try {
      if (Test-Path $statePath -PathType Leaf) {
        attrib -R $statePath 2>$null
        & icacls $statePath /grant:r "$($env:USERNAME):(F)" 2>$null | Out-Null
        Remove-Item -Path $statePath -Force -ErrorAction SilentlyContinue
      }
      $payload | Set-Content -Path $statePath -Encoding UTF8 -Force
    } catch {
      Write-Warn "Could not persist install state at $statePath. Continuing install."
      return
    }
  }
  try {
    & icacls $statePath /inheritance:r /grant:r "$($env:USERNAME):(R)" 2>$null | Out-Null
  } catch {
    # Best-effort only.
  }
}

if ([string]::IsNullOrWhiteSpace($Version)) { $Version = "latest" }
if ((-not (Test-LocalBinaryMode)) -and [Environment]::UserInteractive) {
  $versionInput = Read-Host "Enter release tag/version [latest]"
  if ([string]::IsNullOrWhiteSpace($versionInput)) {
    $Version = "latest"
  } else {
    $Version = $versionInput
  }
} elseif ((-not (Test-LocalBinaryMode)) -and [string]::IsNullOrWhiteSpace($Version)) {
  $Version = "latest"
}

if ([string]::IsNullOrWhiteSpace($Repo)) { $Repo = $env:GHDP_DEFAULT_REPO }
if ((-not (Test-LocalBinaryMode)) -and [string]::IsNullOrWhiteSpace($Repo)) {
  Write-Err "GHDP_REPO is required."
  exit 1
}

function Resolve-GitHubToken {
  if (-not [string]::IsNullOrWhiteSpace($env:GHDP_TOKEN)) { return $env:GHDP_TOKEN.Trim() }
  if (-not [string]::IsNullOrWhiteSpace($env:GH_TOKEN)) { return $env:GH_TOKEN.Trim() }
  if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_TOKEN)) { return $env:GITHUB_TOKEN.Trim() }

  if ([Environment]::UserInteractive) {
    $prompted = Read-Host "Enter GitHub token"
    if (-not [string]::IsNullOrWhiteSpace($prompted)) { return $prompted.Trim() }
  }

  return $null
}

if ((-not (Test-LocalBinaryMode)) -and [string]::IsNullOrWhiteSpace($env:GHDP_TOKEN)) {
  $resolvedToken = Resolve-GitHubToken
  if (-not [string]::IsNullOrWhiteSpace($resolvedToken)) {
    $env:GHDP_TOKEN = $resolvedToken
    $env:GH_TOKEN = $resolvedToken
    $env:GITHUB_TOKEN = $resolvedToken
  }
}

if ((-not (Test-LocalBinaryMode)) -and [string]::IsNullOrWhiteSpace($env:GHDP_TOKEN)) {
  Write-Err "GitHub token is required."
  exit 1
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
  $InstallDir = Join-Path $env:LOCALAPPDATA "ghdp\bin"
}
if ([string]::IsNullOrWhiteSpace($RuntimeEnvPath)) {
  $RuntimeEnvPath = Join-Path $HOME ".ghdp\runtime.env"
}

$asset = "ghdp-windows-amd64.exe"
$assetSha = "$asset.sha256"

if (Test-LocalBinaryMode) {
  $tag = "local-file"
} elseif ($Version -eq "latest" -or [string]::IsNullOrWhiteSpace($Version)) {
  $tag = "latest"
} else {
  $tag = $Version
  if (-not $tag.StartsWith("v")) { $tag = "v$tag" }
}

$apiBase = "https://api.github.com/repos/$Repo"
$headers = @{
  Authorization = "token $($env:GHDP_TOKEN)"
  Accept = "application/vnd.github+json"
  "X-GitHub-Api-Version" = "2022-11-28"
}

Write-Info "Installing ghdp from:"
if (Test-LocalBinaryMode) {
  Write-Host "  Source:  $($env:GHDP_BINARY_PATH)"
} else {
  Write-Host "  Repo:    $Repo"
  Write-Host "  Version: $Version"
  Write-Host "  Asset:   $asset"
}
Write-Host "  Target:  $InstallDir\ghdp.exe"
Write-Host ""

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$tmp = New-TemporaryFile
$tmpSha = New-TemporaryFile
$binPath = Join-Path $InstallDir "ghdp.exe"

function Get-ReleaseMetadata {
  param(
    [string]$ApiBase,
    [string]$Tag,
    [hashtable]$Headers
  )

  $releaseUrl = if ($Tag -eq "latest") {
    "$ApiBase/releases/latest"
  } else {
    "$ApiBase/releases/tags/$Tag"
  }

  Write-Info "Fetching release metadata via GitHub API..."
  try {
    return Invoke-RestMethod -Headers $Headers -Uri $releaseUrl
  } catch {
    Write-Err "Failed to fetch release metadata: $($_.Exception.Message)"
    exit 1
  }
}

function Get-AssetId {
  param(
    $Release,
    [string]$AssetName
  )

  return ($Release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1).id
}

function Download-ReleaseAsset {
  param(
    [string]$ApiBase,
    [hashtable]$Headers,
    [int64]$AssetId,
    [string]$AssetName,
    [string]$OutFile
  )

  $downloadHeaders = @{
    Authorization = $Headers.Authorization
    Accept = "application/octet-stream"
    "X-GitHub-Api-Version" = $Headers."X-GitHub-Api-Version"
  }

  Write-Info "Downloading $AssetName via GitHub API..."
  try {
    Invoke-WebRequest -UseBasicParsing -Headers $downloadHeaders -Uri "$ApiBase/releases/assets/$AssetId" -OutFile $OutFile
  } catch {
    throw "Download failed for ${AssetName}: $($_.Exception.Message)"
  }
}

function Ensure-RuntimeEnvExists {
  $runtimeDir = Split-Path -Parent $RuntimeEnvPath
  New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

  if (Test-Path $RuntimeEnvPath) {
    return
  }

  @(
    "# GHDP user runtime overrides"
    "# Add per-user values here. These override installed defaults."
    "# Example:"
    "# GHDP_DEFAULT_REPO=gh-org-data-platform/dp-tools-local-setup"
  ) | Set-Content -Path $RuntimeEnvPath -Encoding UTF8

  Write-Info "Created user runtime overrides file: $RuntimeEnvPath"
}

if (Test-LocalBinaryMode) {
  if (-not (Test-Path $env:GHDP_BINARY_PATH -PathType Leaf)) {
    Write-Err "Local GHDP binary not found: $($env:GHDP_BINARY_PATH)"
    exit 1
  }
  Copy-Item -Path $env:GHDP_BINARY_PATH -Destination $tmp.FullName -Force
} else {
  $release = Get-ReleaseMetadata -ApiBase $apiBase -Tag $tag -Headers $headers
  $assetId = Get-AssetId -Release $release -AssetName $asset
  if (-not $assetId) {
    Write-Err "Asset not found in release: $asset"
    exit 1
  }

  try {
    Download-ReleaseAsset -ApiBase $apiBase -Headers $headers -AssetId $assetId -AssetName $asset -OutFile $tmp.FullName
  } catch {
    Write-Err $_.Exception.Message
    exit 1
  }

  # Optional checksum verification (won't fail install if missing)
  $checksumOk = $false
  try {
    $shaAssetId = Get-AssetId -Release $release -AssetName $assetSha
    if ($shaAssetId) {
      Download-ReleaseAsset -ApiBase $apiBase -Headers $headers -AssetId $shaAssetId -AssetName $assetSha -OutFile $tmpSha.FullName
      $expected = (Get-Content $tmpSha.FullName | Select-Object -First 1).Split(' ')[0].Trim()
      if (-not [string]::IsNullOrWhiteSpace($expected)) {
        $actual = (Get-FileHash -Algorithm SHA256 -Path $tmp.FullName).Hash.ToLower()
        if ($expected.ToLower() -ne $actual) {
          Write-Err "Checksum mismatch for $asset"
          exit 1
        }
        $checksumOk = $true
      }
    }
  } catch {
    # ignore
  }

  if ($checksumOk) { Write-Ok "Checksum OK" } else { Write-Warn "Checksum file not found (skipping verification)" }
}

Move-Item -Force -Path $tmp.FullName -Destination $binPath
Ensure-RuntimeEnvExists

if (Test-ManagedInstall) {
  Write-ManagedInstallMarker
  Write-InstallState -Mode "managed"
} else {
  Remove-ManagedInstallMarker
  Write-InstallState -Mode "standard"
}

# Add to PATH (User) + update session PATH immediately unless -NoPath
if (-not $NoPath) {
  $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($null -eq $currentUserPath) { $currentUserPath = "" }

  $needsAdd = -not ($currentUserPath.Split(';') | Where-Object { $_ -eq $InstallDir })
  if ($needsAdd) {
    $newUserPath = ($currentUserPath.TrimEnd(';') + ";" + $InstallDir).Trim(';')
    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
    Write-Ok "Added to User PATH: $InstallDir"
  } else {
    Write-Ok "User PATH already contains: $InstallDir"
  }

  # Update current session too
  if (-not ($env:Path.Split(';') | Where-Object { $_ -eq $InstallDir })) {
    $env:Path = "$InstallDir;$env:Path"
  }
}

Write-Host ""
Write-Info "Verifying install..."
try {
  & $binPath --version | Out-Null
  Write-Ok "Installed: $(& $binPath --version)"
} catch {
  try { & $binPath --help | Out-Null } catch {}
  Write-Ok "Installed ghdp (version command not available)."
}

Write-Host ""
Write-Ok "Done."
Write-Info "Run 'ghdp --help' in a new terminal when ready."
