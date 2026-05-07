# GHDP Internal Binary Install Guide

This guide is for users who will install `ghdp` from a pre-downloaded binary provided through an internal location such as SharePoint, an internal wiki, or a shared drive.

You do not need to build GHDP locally.

You do not need to know how to fetch a GitHub token just to install GHDP.

## Before You Start

You need two things:

1. The correct GHDP binary for your machine
2. A local-binary installer script:
   - `install_ghdp_local_binary.sh` for macOS / Linux
   - `install_ghdp_local_binary.ps1` for Windows

The installer takes care of:

- moving the downloaded binary into a GHDP-owned folder under `~/.ghdp/installers`
- copying the binary into the install location
- updating `PATH` when needed
- verifying the install
- creating `~/.ghdp/managed-install` when company-managed mode is enabled
- launching `ghdp` once so the welcome banner is shown
- applying the default local scheduler setup with `ghdp schedule apply --auto-approve`

## Which File Should You Download?

Download the binary from the internal distribution location for your operating system:

- macOS Apple Silicon: `ghdp-darwin-arm64`
- macOS Intel: `ghdp-darwin-amd64`
- Windows: `ghdp-windows-amd64.exe`

If you are not sure whether your Mac is Apple Silicon or Intel, open Terminal and run:

```bash
uname -m
```

Use:

- `arm64` → download `ghdp-darwin-arm64`
- `x86_64` → download `ghdp-darwin-amd64`

## Recommended Download Location

Download the binary into your `Downloads` folder.

Examples:

- macOS Apple Silicon: `~/Downloads/ghdp-darwin-arm64`
- macOS Intel: `~/Downloads/ghdp-darwin-amd64`
- Windows: `C:\Users\<your-user>\Downloads\ghdp-windows-amd64.exe`

## macOS / Linux Install

### Standard install

1. Download the correct binary into `Downloads`
2. Open Terminal
3. Download the installer script `install_ghdp_local_binary.sh`
4. Go to `Downloads`:

```bash
cd "$HOME/Downloads"
```

5. Make the installer executable:

```bash
chmod +x install_ghdp_local_binary.sh
```

6. Run the installer using the downloaded binary

For Apple Silicon:

```bash
GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-arm64" bash install_ghdp_local_binary.sh
```

For Intel:

```bash
GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-amd64" bash install_ghdp_local_binary.sh
```

7. Verify installation:

```bash
ghdp --version
```

### Company-managed install

Use this flow when the installation should use the company-managed GitHub access path later.

For Apple Silicon:

```bash
GHDP_MANAGED_INSTALL=1 GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-arm64" bash install_ghdp_local_binary.sh
```

For Intel:

```bash
GHDP_MANAGED_INSTALL=1 GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-amd64" bash install_ghdp_local_binary.sh
```

Verify:

```bash
ghdp --version
ls -l ~/.ghdp/managed-install
```

## Windows Install

### Standard install

1. Download `ghdp-windows-amd64.exe` into `Downloads`
2. Open PowerShell
3. Download the installer script `install_ghdp_local_binary.ps1`
4. Open PowerShell in the `Downloads` folder
5. Run:

```powershell
$env:GHDP_BINARY_PATH="$env:USERPROFILE\Downloads\ghdp-windows-amd64.exe"
powershell -ExecutionPolicy Bypass -File .\install_ghdp_local_binary.ps1
```

6. Verify:

```powershell
ghdp --version
```

### Company-managed install

Use this flow when the installation should use the company-managed GitHub access path later.

```powershell
$env:GHDP_MANAGED_INSTALL="1"
$env:GHDP_BINARY_PATH="$env:USERPROFILE\Downloads\ghdp-windows-amd64.exe"
powershell -ExecutionPolicy Bypass -File .\install_ghdp_local_binary.ps1
```

Verify:

```powershell
ghdp --version
```

## What Happens During Install

The installer:

1. Reads the local binary from `GHDP_BINARY_PATH`
2. Moves it into a GHDP-owned folder under `~/.ghdp/installers`
3. Copies it into the install location
4. Makes it executable if needed
5. Updates `PATH` if needed
6. Verifies the install
7. If `GHDP_MANAGED_INSTALL=1` is set, creates:

```text
~/.ghdp/managed-install
```

8. Launches `ghdp` once so the welcome banner is shown
9. Runs `ghdp schedule apply --auto-approve`

## What To Do If Installation Fails

Check these first:

- Is the binary downloaded completely?
- Did you point `GHDP_BINARY_PATH` to the correct file?
- Did you use the right binary for your machine?
- Did you run the installer from the `platform-cli` folder?

Useful checks:

macOS / Linux:

```bash
ls -l "$HOME/Downloads/ghdp-darwin-arm64"
command -v ghdp
ghdp --version
```

Windows:

```powershell
Get-Item "$env:USERPROFILE\Downloads\ghdp-windows-amd64.exe"
Get-Command ghdp -ErrorAction SilentlyContinue
ghdp --version
```

## Summary

Use the internal binary install flow when:

- the binary has already been downloaded from SharePoint or another internal location
- you want a simpler install process
- you do not want users to deal with GitHub token setup during installation

Use `GHDP_MANAGED_INSTALL=1` only when the installation should also enable the company-managed GitHub authentication path for later GHDP operations.
