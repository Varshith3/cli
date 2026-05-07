param(
    [switch]$KeepWorkDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section([string]$Message) {
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Resolve-PythonExe {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        return "$($launcher.Source) -3"
    }

    throw "Python was not found on PATH."
}

function New-SmokeRepo([string]$Root, [string]$Name) {
    $repoDir = Join-Path $Root $Name
    $tfDir = Join-Path $repoDir "terraform"
    New-Item -ItemType Directory -Path $tfDir -Force | Out-Null

    @"
terraform {
  required_version = ">= 1.0.0"
}
"@ | Set-Content -Path (Join-Path $tfDir "main.tf") -Encoding utf8

    & git -C $repoDir init | Out-Null
    & git -C $repoDir config user.email "smoke@example.com"
    & git -C $repoDir config user.name "GHDP Smoke"
    & git -C $repoDir add .
    & git -C $repoDir commit -m "init smoke repo" | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create/commit smoke test repo at $repoDir"
    }

    return $repoDir
}

function Write-MockTerraform([string]$Path) {
    @"
@echo off
setlocal EnableDelayedExpansion
set "cmd=%~1"
shift

if /I "%cmd%"=="init" (
  exit /b 0
)

if /I "%cmd%"=="workspace" (
  set "sub=%~1"
  set "name=%~2"
  if /I "%sub%"=="select" (
    if /I "%name%"=="dev" exit /b 0
    exit /b 1
  )
  if /I "%sub%"=="new" (
    exit /b 0
  )
  exit /b 0
)

if /I "%cmd%"=="validate" (
  exit /b 0
)

if /I "%cmd%"=="fmt" (
  exit /b 0
)

if /I "%cmd%"=="plan" (
  set "outfile=tfplan"
  :plan_loop
  if "%~1"=="" goto plan_done
  for /f "tokens=1,2 delims==" %%A in ("%~1") do (
    if /I "%%A"=="-out" set "outfile=%%B"
  )
  shift
  goto plan_loop
  :plan_done
  type nul > "%outfile%"
  exit /b 0
)

if /I "%cmd%"=="show" (
  if "%TF_MOCK_DELETE%"=="1" (
    echo {"resource_changes":[{"address":"mock.delete_only","change":{"actions":["delete"]}}]}
    exit /b 0
  )
  if "%TF_MOCK_REPLACE%"=="1" (
    echo {"resource_changes":[{"address":"mock.replace","change":{"actions":["delete","create"]}}]}
    exit /b 0
  )
  echo {"resource_changes":[{"address":"mock.update","change":{"actions":["update"]}}]}
  exit /b 0
)

if /I "%cmd%"=="apply" (
  exit /b 0
)

echo terraform mock unsupported command: %cmd% 1>&2
exit /b 1
"@ | Set-Content -Path $Path -Encoding ascii
}

function Write-MockAws([string]$Path) {
    @"
@echo off
if /I "%~1"=="sts" if /I "%~2"=="get-caller-identity" (
  echo {"Account":"000000000000","Arn":"arn:aws:iam::000000000000:user/mock","UserId":"mock"}
  exit /b 0
)

if /I "%~1"=="sso" if /I "%~2"=="login" (
  exit /b 0
)

echo aws mock unsupported command: %* 1>&2
exit /b 1
"@ | Set-Content -Path $Path -Encoding ascii
}

function Invoke-GhdpCli {
    param(
        [Parameter(Mandatory = $true)][string]$RepoDir,
        [Parameter(Mandatory = $true)][string[]]$Args,
        [Parameter(Mandatory = $true)][string]$PythonExe
    )

    Push-Location $RepoDir
    try {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            if ($PythonExe -like "* *") {
                $parts = $PythonExe.Split(" ", 2)
                $out = & $parts[0] $parts[1] -m platform_cli.cli @Args *>&1
            } else {
                $out = & $PythonExe -m platform_cli.cli @Args *>&1
            }
            $code = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
    }
    finally {
        Pop-Location
    }

    return [PSCustomObject]@{
        ExitCode = $code
        Output   = (($out | ForEach-Object { $_.ToString() }) -join "`n")
    }
}

function Assert-Result {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][pscustomobject]$Result,
        [Parameter(Mandatory = $true)][bool]$ExpectSuccess,
        [string]$MustContainRegex
    )

    if ($ExpectSuccess -and $Result.ExitCode -ne 0) {
        throw "$Name failed unexpectedly (exit=$($Result.ExitCode)).`n$($Result.Output)"
    }

    if ((-not $ExpectSuccess) -and $Result.ExitCode -eq 0) {
        throw "$Name succeeded unexpectedly.`n$($Result.Output)"
    }

    if ($MustContainRegex) {
        if ($Result.Output -notmatch $MustContainRegex) {
            throw "$Name output did not match regex '$MustContainRegex'.`n$($Result.Output)"
        }
    }
}

$projectRoot = Split-Path -Parent $PSCommandPath
$srcPath = Join-Path $projectRoot "src"
$pythonExe = Resolve-PythonExe

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    throw "git was not found on PATH."
}

$workRoot = Join-Path $env:TEMP ("ghdp-tf-smoke-" + [Guid]::NewGuid().ToString("N"))
$mockBin = Join-Path $workRoot "mock-bin"
$fakeHome = Join-Path $workRoot "home"
$policyDir = Join-Path $workRoot "policy"
$policyPath = Join-Path $policyDir "terraform_local.json"

$origPath = $env:PATH
$origPythonPath = $env:PYTHONPATH
$origPolicyPath = $env:GHDP_TERRAFORM_POLICY_PATH
$origHome = $env:HOME
$origUserProfile = $env:USERPROFILE
$origTfMockDelete = $env:TF_MOCK_DELETE

try {
    New-Item -ItemType Directory -Path $workRoot, $mockBin, $fakeHome, $policyDir -Force | Out-Null

    Write-MockTerraform (Join-Path $mockBin "terraform.cmd")
    Write-MockAws (Join-Path $mockBin "aws.cmd")

    $policy = @{
        allowed_envs      = @("dev")
        default_tf_root   = "./terraform"
        default_region    = "us-west-2"
        dependencies      = @()
        backend           = @{
            use_lockfile = $true
        }
    }
    $policy | ConvertTo-Json -Depth 8 | Set-Content -Path $policyPath -Encoding utf8

    $env:PATH = "$mockBin;$origPath"
    if ([string]::IsNullOrWhiteSpace($origPythonPath)) {
        $env:PYTHONPATH = $srcPath
    } else {
        $env:PYTHONPATH = "$srcPath;$origPythonPath"
    }
    $env:GHDP_TERRAFORM_POLICY_PATH = $policyPath
    $env:HOME = $fakeHome
    $env:USERPROFILE = $fakeHome

    Write-Section "Smoke test 1: tf-apply success (update-only plan)"
    $repo1 = New-SmokeRepo -Root $workRoot -Name "repo-success"
    $r1 = Invoke-GhdpCli -RepoDir $repo1 -PythonExe $pythonExe -Args @(
        "tf-apply",
        "--env", "dev",
        "--account", "dpnp",
        "--backend-bucket", "smoke-bucket",
        "--backend-key", "sample-service/dev.tfstate",
        "--aws-profile", "mock",
        "--yes"
    )
    Assert-Result -Name "tf-apply success" -Result $r1 -ExpectSuccess $true -MustContainRegex "status:\s+tf-apply completed"
    Write-Host "PASS"

    Write-Section "Smoke test 2: tf-apply blocked on delete-only action"
    $repo2 = New-SmokeRepo -Root $workRoot -Name "repo-delete"
    $env:TF_MOCK_DELETE = "1"
    $r2 = Invoke-GhdpCli -RepoDir $repo2 -PythonExe $pythonExe -Args @(
        "tf-apply",
        "--env", "dev",
        "--account", "dpnp",
        "--backend-bucket", "smoke-bucket",
        "--backend-key", "sample-service/dev.tfstate",
        "--aws-profile", "mock",
        "--yes"
    )
    Assert-Result -Name "tf-apply delete block" -Result $r2 -ExpectSuccess $false -MustContainRegex "delete-only|E_TF_POLICY_DENY"
    Write-Host "PASS"
    Remove-Item Env:TF_MOCK_DELETE -ErrorAction SilentlyContinue

    Write-Section "Smoke test 3: tf-plan blocked for non-dev env"
    $repo3 = New-SmokeRepo -Root $workRoot -Name "repo-env-block"
    $r3 = Invoke-GhdpCli -RepoDir $repo3 -PythonExe $pythonExe -Args @(
        "tf-plan",
        "--env", "prod",
        "--account", "dpnp",
        "--backend-bucket", "smoke-bucket",
        "--backend-key", "sample-service/prod.tfstate",
        "--aws-profile", "mock"
    )
    Assert-Result -Name "tf-plan env block" -Result $r3 -ExpectSuccess $false -MustContainRegex "Allowed envs|env 'prod'|E_TF_POLICY_DENY"
    Write-Host "PASS"

    Write-Section "All smoke tests passed"
    Write-Host "Working dir: $workRoot"
}
finally {
    $env:PATH = $origPath
    $env:PYTHONPATH = $origPythonPath
    $env:GHDP_TERRAFORM_POLICY_PATH = $origPolicyPath
    $env:HOME = $origHome
    $env:USERPROFILE = $origUserProfile
    $env:TF_MOCK_DELETE = $origTfMockDelete

    if (-not $KeepWorkDir -and (Test-Path $workRoot)) {
        Remove-Item -Path $workRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
