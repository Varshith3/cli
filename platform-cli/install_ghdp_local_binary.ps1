Param(
  [string]$InstallDir = $env:GHDP_INSTALL_DIR,
  [string]$RuntimeEnvPath = $env:GHDP_RUNTIME_ENV_PATH,
  [switch]$NoPath
)

# Standalone installer for a pre-downloaded GHDP binary.
#
# Usage:
#   $env:GHDP_BINARY_PATH="$env:USERPROFILE\Downloads\ghdp-windows-amd64.exe"
#   powershell -ExecutionPolicy Bypass -File .\install_ghdp_local_binary.ps1
#
# Env overrides:
#   GHDP_BINARY_PATH="C:\path\ghdp.exe" (required)
#   GHDP_INSTALL_DIR="C:\path"          (default: %LOCALAPPDATA%\ghdp\bin)
#   GHDP_MANAGED_INSTALL="1"            (persist managed install state)
#   GHDP_STAGED_BINARY_DIR="~\.ghdp\installers" (where the downloaded binary is moved before install)

function Write-Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host $msg -ForegroundColor Green }
function Write-Err($msg)  { Write-Host $msg -ForegroundColor Red }
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
function Ensure-RuntimeEnvExists {
  if ([string]::IsNullOrWhiteSpace($RuntimeEnvPath)) {
    $RuntimeEnvPath = Join-Path $HOME ".ghdp\runtime.env"
  }

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
function Set-RuntimeEnvValue {
  param(
    [string]$Path,
    [string]$Key,
    [string]$Value
  )

  $lines = if (Test-Path $Path) { @(Get-Content -Path $Path -Encoding UTF8) } else { @() }
  $pattern = "^\s*(?:export\s+)?$([regex]::Escape($Key))="
  $updated = @()
  $replaced = $false

  foreach ($line in $lines) {
    if ($line -match $pattern) {
      $updated += "$Key=$Value"
      $replaced = $true
    } else {
      $updated += $line
    }
  }

  if (-not $replaced) {
    $updated += "$Key=$Value"
  }

  Set-Content -Path $Path -Value $updated -Encoding UTF8
}
function Write-InstallState {
  param([string]$Mode)
  $statePath = Get-InstallStatePath
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statePath) | Out-Null
  $payload = @{
    schema_version = "1.0"
    install_mode = "$Mode"
  } | ConvertTo-Json -Depth 4
  $payload | Set-Content -Path $statePath -Encoding UTF8
  try { & icacls $statePath /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null } catch {}
}
function Get-StagedBinaryDir {
  if (-not [string]::IsNullOrWhiteSpace($env:GHDP_STAGED_BINARY_DIR)) {
    return $env:GHDP_STAGED_BINARY_DIR
  }
  return Join-Path $HOME ".ghdp\installers"
}
function Get-StagedBinaryPath {
  $sourceName = Split-Path -Leaf $env:GHDP_BINARY_PATH
  return Join-Path (Get-StagedBinaryDir) $sourceName
}

if ([string]::IsNullOrWhiteSpace($env:GHDP_BINARY_PATH)) {
  Write-Err "GHDP_BINARY_PATH is required."
  exit 1
}

if (-not (Test-Path $env:GHDP_BINARY_PATH -PathType Leaf)) {
  Write-Err "Local GHDP binary not found: $($env:GHDP_BINARY_PATH)"
  exit 1
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
  $InstallDir = Join-Path $env:LOCALAPPDATA "ghdp\bin"
}
if ([string]::IsNullOrWhiteSpace($RuntimeEnvPath)) {
  $RuntimeEnvPath = Join-Path $HOME ".ghdp\runtime.env"
}

$stagedBinary = Get-StagedBinaryPath

Write-Info "Installing ghdp from:"
Write-Host "  Source:  $($env:GHDP_BINARY_PATH)"
Write-Host "  Staged:  $stagedBinary"
Write-Host "  Target:  $InstallDir\ghdp.exe"
Write-Host ""

New-Item -ItemType Directory -Force -Path (Get-StagedBinaryDir) | Out-Null
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$binPath = Join-Path $InstallDir "ghdp.exe"
Move-Item -Path $env:GHDP_BINARY_PATH -Destination $stagedBinary -Force
Copy-Item -Path $stagedBinary -Destination $binPath -Force

if (Test-ManagedInstall) {
  Write-ManagedInstallMarker
  Write-InstallState -Mode "managed"
} else {
  Remove-ManagedInstallMarker
  Write-InstallState -Mode "standard"
}

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
