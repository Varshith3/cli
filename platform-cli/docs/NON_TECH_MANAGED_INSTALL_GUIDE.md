# GHDP Non-Technical Install Guide (Windows + macOS)

Use this when you are **not** building locally and only need GHDP working.

## 1) What You Need

Ask the platform team to share these files:

| Platform | Required files |
|---|---|
| Windows | `ghdp-windows-amd64.exe`, `install_ghdp.ps1` |
| macOS Apple Silicon (M1/M2/M3) | `ghdp-darwin-arm64`, `install_ghdp.sh` |
| macOS Intel | `ghdp-darwin-amd64`, `install_ghdp.sh` |

Save all files in your **Downloads** folder.

## 2) Pick the Right macOS Binary

If you are on macOS and not sure which file to use:

```bash
uname -m
```

| Output | Use this binary |
|---|---|
| `arm64` | `ghdp-darwin-arm64` |
| `x86_64` | `ghdp-darwin-amd64` |

## 3) Install on Windows

Open **PowerShell** and run:

```powershell
cd $env:USERPROFILE\Downloads

$env:GHDP_MANAGED_INSTALL="1"
$env:GHDP_BINARY_PATH="$env:USERPROFILE\Downloads\ghdp-windows-amd64.exe"
$env:GHDP_REPO="gh-org-data-platform/dp-tools-local-setup"

powershell -ExecutionPolicy Bypass -File .\install_ghdp.ps1
ghdp --version
```

## 4) Install on macOS

Open **Terminal** and run:

Important:
- Use `bash` (do **not** use `sh`).
- Make sure files exist in `~/Downloads` before running install.

Quick check:

```bash
cd "$HOME/Downloads"
ls -l install_ghdp.sh ghdp-darwin-arm64 ghdp-darwin-amd64
```

For Apple Silicon:

```bash
cd "$HOME/Downloads"
chmod +x install_ghdp.sh
chmod +x ghdp-darwin-arm64

GHDP_MANAGED_INSTALL=1 \
GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-arm64" \
GHDP_REPO="gh-org-data-platform/dp-tools-local-setup" \
bash "$HOME/Downloads/install_ghdp.sh"

ghdp --version
```

For Intel:

```bash
cd "$HOME/Downloads"
chmod +x install_ghdp.sh
chmod +x ghdp-darwin-amd64

GHDP_MANAGED_INSTALL=1 \
GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-amd64" \
GHDP_REPO="gh-org-data-platform/dp-tools-local-setup" \
bash "$HOME/Downloads/install_ghdp.sh"

ghdp --version
```

## 5) Quick Verification

Run:

```bash
ghdp --version
```

You should see a GHDP version value (for example `ghdp 1.x.x ...`).

## 6) First Tool Setup

After install, run:

```bash
ghdp tools install
```

If prompted for identity text, enter your GH username or Guardant ID.

## 7) Fix Wrong AWS Profile (if selected by mistake)

Run AWS SSO setup again with the correct profile:

```bash
ghdp aws sso
```

## 8) Launch Claude with Profile Pick

Use this command to pick/change AWS profile for the current Claude session:

```bash
ghdp claude-launch --pick-profile
```

## 9) Change Athena Workgroup (if needed)

If your team asks you to change Athena workgroup:

```bash
ghdp config claude-athena-workgroup --value <your_workgroup_name>
```

Verify:

```bash
ghdp config get claude-athena-workgroup
```

## 10) If Something Fails

Use these checks before raising a ticket:

| Check | Command |
|---|---|
| GHDP installed | `ghdp --version` |
| GHDP health | `ghdp doctor` |
| See detailed tool install errors | `ghdp tools install --refresh-toolset --debug-install` |

If there is an error, share the full command output with the platform team.
