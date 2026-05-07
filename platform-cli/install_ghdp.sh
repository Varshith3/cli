#!/usr/bin/env bash
set -euo pipefail

# One-step installer for GHDP CLI (binary-first)
# Public repo: downloads from /releases/download/...
# Private repo: downloads via GitHub API using GHDP_TOKEN (recommended, reliable)
#
# Env overrides:
#   GHDP_REPO="owner/repo"            (required)
#   GHDP_VERSION="latest"|"vX.Y.Z"    (default: latest)
#   GHDP_INSTALL_DIR="/some/dir"      (default: first writable dir on PATH, else $HOME/.local/bin)
#   GHDP_NO_MODIFY_SHELL="1"          (skip editing rc files)
#   GHDP_BINARY_PATH="/path/to/ghdp"  (install from a pre-downloaded local binary; skips GitHub download/auth)
#   GHDP_MANAGED_INSTALL="1"          (persist managed install state)
#   GHDP_TOKEN="ghp_..."              (optional; otherwise installer uses gh auth or PAT prompt)

REPO="${GHDP_REPO:-${GHDP_DEFAULT_REPO:-}}"
VERSION="${GHDP_VERSION:-}"
DEFAULT_INSTALL_DIR="$HOME/.local/bin"
INSTALL_DIR="${GHDP_INSTALL_DIR:-}"
NO_MODIFY_SHELL="${GHDP_NO_MODIFY_SHELL:-0}"
IS_INTERACTIVE=0
if [[ -t 0 && -t 1 ]]; then
  IS_INTERACTIVE=1
fi
RUNTIME_ENV_PATH="${GHDP_RUNTIME_ENV_PATH:-$HOME/.ghdp/runtime.env}"

say() { printf "%s\n" "$*"; }
die() { printf "ERROR: %s\n" "$*" >&2; exit 1; }
local_binary_mode() { [[ -n "${GHDP_BINARY_PATH:-}" ]]; }

remove_quarantine_if_macos() {
  command -v xattr >/dev/null 2>&1 || return 0
  [[ "$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')" == "darwin" ]] || return 0
  local target
  for target in "$@"; do
    [[ -n "${target:-}" && -e "$target" ]] || continue
    xattr -d com.apple.quarantine "$target" 2>/dev/null || true
  done
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

need_cmd uname
need_cmd chmod
need_cmd mkdir
need_cmd mv
need_cmd mktemp
need_cmd grep
need_cmd awk
need_cmd cp
if ! local_binary_mode; then
  need_cmd curl
fi

managed_install_enabled() {
  case "${GHDP_MANAGED_INSTALL:-0}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

managed_install_marker_path() {
  printf "%s" "${HOME}/.ghdp/managed-install"
}

install_state_path() {
  printf "%s" "${HOME}/.ghdp/install-state.json"
}

write_managed_install_marker() {
  local marker
  marker="$(managed_install_marker_path)"
  mkdir -p "$(dirname "$marker")"
  printf "managed\n" > "$marker"
}

remove_managed_install_marker() {
  local marker
  marker="$(managed_install_marker_path)"
  rm -f "$marker" 2>/dev/null || true
}

prompt_for_token() {
  say "GitHub token is required to download GHDP release assets."
  printf "Enter GitHub token: "
  read -r GHDP_TOKEN
}

prompt_for_version() {
  printf "Enter release tag/version [latest]: "
  read -r VERSION
  VERSION="${VERSION:-latest}"
}

resolve_token() {
  if [[ -n "${GHDP_TOKEN:-}" ]]; then
    printf "%s" "${GHDP_TOKEN}"
    return 0
  fi
  if [[ -n "${GH_TOKEN:-}" ]]; then
    printf "%s" "${GH_TOKEN}"
    return 0
  fi
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf "%s" "${GITHUB_TOKEN}"
    return 0
  fi

  if [[ "$IS_INTERACTIVE" == "1" ]]; then
    printf "Enter GitHub token: "
    read -r token
    [[ -n "${token:-}" ]] && { printf "%s" "$token"; return 0; }
  fi

  return 1
}

if [[ "$IS_INTERACTIVE" == "1" ]] && ! local_binary_mode; then
  if token="$(resolve_token)"; then
    GHDP_TOKEN="$token"
    GH_TOKEN="$token"
    GITHUB_TOKEN="$token"
    export GHDP_TOKEN GH_TOKEN GITHUB_TOKEN
  fi
  if [[ -z "${GHDP_TOKEN:-}" ]]; then
    die "GitHub token is required."
  fi
  prompt_for_version
elif ! local_binary_mode; then
  if token="$(resolve_token)"; then
    GHDP_TOKEN="$token"
    GH_TOKEN="$token"
    GITHUB_TOKEN="$token"
    export GHDP_TOKEN GH_TOKEN GITHUB_TOKEN
  fi
  if [[ -z "${GHDP_TOKEN:-}" ]]; then
    die "GHDP_TOKEN is required for non-interactive installs."
  fi
  VERSION="${VERSION:-latest}"
fi

if ! local_binary_mode && [[ -z "$REPO" ]]; then
  die "GHDP repo is not configured. Set GHDP_REPO or GHDP_DEFAULT_REPO before running the installer."
fi

ensure_runtime_env_exists() {
  local runtime_dir
  runtime_dir="$(dirname "$RUNTIME_ENV_PATH")"
  mkdir -p "$runtime_dir"
  if [[ ! -f "$RUNTIME_ENV_PATH" ]]; then
    cat > "$RUNTIME_ENV_PATH" <<'EOF'
# GHDP user runtime overrides
# Add per-user values here. These override installed defaults.
# Example:
# GHDP_DEFAULT_REPO=gh-org-data-platform/dp-tools-local-setup
EOF
    say "Created user runtime overrides file: $RUNTIME_ENV_PATH"
  fi
}

upsert_runtime_env_value() {
  local key="$1"
  local value="$2"
  touch "$RUNTIME_ENV_PATH"

  if grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}=" "$RUNTIME_ENV_PATH"; then
    env KEY="$key" VALUE="$value" perl -0pi -e 's{(?m)^[ \t]*(?:export[ \t]+)?\Q$ENV{KEY}\E=.*$}{$ENV{KEY}=$ENV{VALUE}}g' "$RUNTIME_ENV_PATH"
    return 0
  fi

  printf "%s=%s\n" "$key" "$value" >> "$RUNTIME_ENV_PATH"
}

write_install_state() {
  local mode="$1"
  local state_file
  state_file="$(install_state_path)"
  mkdir -p "$(dirname "$state_file")"
  cat > "$state_file" <<EOF
{
  "schema_version": "1.0",
  "install_mode": "$mode"
}
EOF
  chmod 600 "$state_file" 2>/dev/null || true
}

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH_RAW="$(uname -m | tr '[:upper:]' '[:lower:]')"

case "$OS" in
  darwin) OS="darwin" ;;
  linux)  OS="linux" ;;
  *) die "Unsupported OS: $OS" ;;
esac

case "$ARCH_RAW" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="amd64" ;;
  *) die "Unsupported architecture: $ARCH_RAW" ;;
esac

ASSET="ghdp-${OS}-${ARCH}"
BASE_URL="https://github.com/${REPO}/releases"

choose_install_dir() {
  # If caller provided an explicit directory, trust it.
  if [[ -n "$INSTALL_DIR" ]]; then
    return 0
  fi

  # Prefer an existing writable directory already present in PATH.
  local old_ifs="$IFS"
  IFS=':'
  for p in $PATH; do
    [[ -n "$p" ]] || continue
    [[ "$p" == "." ]] && continue
    if [[ -d "$p" && -w "$p" ]]; then
      INSTALL_DIR="$p"
      IFS="$old_ifs"
      return 0
    fi
  done
  IFS="$old_ifs"

  # Fallback for environments with no writable PATH entries.
  INSTALL_DIR="$DEFAULT_INSTALL_DIR"
}

choose_install_dir

# Normalize version/tag
if local_binary_mode; then
  TAG="local-file"
elif [[ "$VERSION" == "latest" || "$VERSION" == "" ]]; then
  TAG="latest"
else
  TAG="$VERSION"
  [[ "$TAG" =~ ^v ]] || TAG="v${TAG}"
fi

say "Installing ghdp from:"
if local_binary_mode; then
  say "  Source:  ${GHDP_BINARY_PATH}"
else
  say "  Repo:    ${REPO}"
  say "  Version: ${TAG}"
  say "  Asset:   ${ASSET}"
fi
say "  Target:  ${INSTALL_DIR}/ghdp"
say ""

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BIN_TMP="${TMP_DIR}/ghdp"
SHA_TMP="${TMP_DIR}/ghdp.sha256"

API_BASE="https://api.github.com/repos/${REPO}"
API_VERSION_HDR=(-H "X-GitHub-Api-Version: 2022-11-28")
API_JSON_HDR=(-H "Accept: application/vnd.github+json")
API_BIN_HDR=(-H "Accept: application/octet-stream")

AUTH_HDR=()
if [[ -n "${GHDP_TOKEN:-}" ]]; then
  AUTH_HDR=(-H "Authorization: Bearer ${GHDP_TOKEN}")
fi

# -------- Private repo path (recommended): GitHub API asset download --------
download_via_api_asset_id() {
  local asset_name="$1"
  local out_path="$2"

  local release_url
  if [[ "$TAG" == "latest" ]]; then
    release_url="${API_BASE}/releases/latest"
  else
    release_url="${API_BASE}/releases/tags/${TAG}"
  fi

  say "Fetching release metadata via GitHub API..."
  local rel_json
  rel_json="$(curl -fsSL -L "${AUTH_HDR[@]}" "${API_VERSION_HDR[@]}" "${API_JSON_HDR[@]}" "$release_url")" \
    || die "Failed to fetch release metadata. (Token missing/invalid or no access?)"

  # Extract asset id for the given name.
  # Handles both orders: id then name, or name then id.
  local asset_id
  asset_id="$(
  printf "%s" "$rel_json" | env ASSET_NAME="$asset_name" perl -0777 -ne '
    my $name = $ENV{"ASSET_NAME"};
    if (/"id"\s*:\s*(\d+)[^}]*"name"\s*:\s*"\Q$name\E"/s) { print $1; exit; }
    if (/"name"\s*:\s*"\Q$name\E"[^}]*"id"\s*:\s*(\d+)/s) { print $1; exit; }
  '
  )"


  if [[ -z "$asset_id" ]]; then
    say "Could not find asset named: $asset_name"
    say "Assets found in this release:"
    printf "%s" "$rel_json" | perl -0777 -ne 'while (/"name"\s*:\s*"([^"]+)"/g){print "  - $1\n";}' | head -n 200
    die "Upload/rename the binary asset to exactly: ${asset_name}"
  fi

  say "Downloading ${asset_name} via GitHub API (asset id: ${asset_id})"
  curl -fsSL -L "${AUTH_HDR[@]}" "${API_VERSION_HDR[@]}" "${API_BIN_HDR[@]}" \
    "${API_BASE}/releases/assets/${asset_id}" -o "$out_path" \
    || die "Download failed (API asset download)."
}

# -------- Public repo path: direct download --------
download_direct() {
  local dl_url="$1"
  local out_path="$2"
  say "Downloading ${dl_url}"
  curl -fsSL -L "$dl_url" -o "$out_path" || die "Download failed (direct)."
}

# Decide method:
# - If GHDP_BINARY_PATH is set -> install from local file
# - Else if GHDP_TOKEN is set -> use API (works for private and public)
# - Else -> direct browser download (works only for public)
if local_binary_mode; then
  [[ -f "${GHDP_BINARY_PATH}" ]] || die "Local GHDP binary not found: ${GHDP_BINARY_PATH}"
  remove_quarantine_if_macos "${GHDP_BINARY_PATH}" "$0"
  say "Installing from local binary: ${GHDP_BINARY_PATH}"
  cp "${GHDP_BINARY_PATH}" "$BIN_TMP"
elif [[ -n "${GHDP_TOKEN:-}" ]]; then
  download_via_api_asset_id "$ASSET" "$BIN_TMP"

  # Optional checksum by asset name if present
  if download_via_api_asset_id "${ASSET}.sha256" "$SHA_TMP" 2>/dev/null; then
    if command -v shasum >/dev/null 2>&1; then
      EXPECTED="$(awk '{print $1}' "$SHA_TMP" | head -n 1)"
      ACTUAL="$(shasum -a 256 "$BIN_TMP" | awk '{print $1}')"
      if [[ -n "$EXPECTED" && "$EXPECTED" != "$ACTUAL" ]]; then
        die "Checksum mismatch for ${ASSET}"
      fi
      say "Checksum OK"
    fi
  else
    say "Checksum asset not found (skipping verification)"
  fi
else
  # Direct URLs (public only)
  if [[ "$TAG" == "latest" ]]; then
    DL_URL="${BASE_URL}/latest/download/${ASSET}"
    SHA_URL="${BASE_URL}/latest/download/${ASSET}.sha256"
  else
    DL_URL="${BASE_URL}/download/${TAG}/${ASSET}"
    SHA_URL="${BASE_URL}/download/${TAG}/${ASSET}.sha256"
  fi

  download_direct "$DL_URL" "$BIN_TMP"

  if curl -fsSL -L "$SHA_URL" -o "$SHA_TMP" >/dev/null 2>&1; then
    if command -v shasum >/dev/null 2>&1; then
      EXPECTED="$(awk '{print $1}' "$SHA_TMP" | head -n 1)"
      ACTUAL="$(shasum -a 256 "$BIN_TMP" | awk '{print $1}')"
      if [[ -n "$EXPECTED" && "$EXPECTED" != "$ACTUAL" ]]; then
        die "Checksum mismatch for ${ASSET}"
      fi
      say "Checksum OK"
    fi
  else
    say "Checksum file not found (skipping verification)"
  fi
fi

mkdir -p "$INSTALL_DIR"
chmod +x "$BIN_TMP"
remove_quarantine_if_macos "$BIN_TMP"
mv "$BIN_TMP" "${INSTALL_DIR}/ghdp"
remove_quarantine_if_macos "${INSTALL_DIR}/ghdp"
ensure_runtime_env_exists

if managed_install_enabled; then
  write_managed_install_marker
  write_install_state "managed"
else
  remove_managed_install_marker
  write_install_state "standard"
fi

export PATH="$INSTALL_DIR:$PATH"

add_path_line() {
  local rc_file="$1"
  local line='export PATH="$HOME/.local/bin:$PATH"'
  [[ "$INSTALL_DIR" == "$DEFAULT_INSTALL_DIR" ]] || return 0
  [[ "$NO_MODIFY_SHELL" == "1" ]] && return 0
  touch "$rc_file"
  if ! grep -Fq "$line" "$rc_file"; then
    printf "\n# Added by GHDP installer\n%s\n" "$line" >> "$rc_file"
    say "Updated PATH in: $rc_file"
  fi
}

if [[ "$NO_MODIFY_SHELL" != "1" ]] && [[ "$INSTALL_DIR" == "$DEFAULT_INSTALL_DIR" ]]; then
  SHELL_NAME="$(basename "${SHELL:-}")"
  if [[ "$SHELL_NAME" == "zsh" ]]; then
    add_path_line "$HOME/.zshrc"
    add_path_line "$HOME/.zprofile"
  elif [[ "$SHELL_NAME" == "bash" ]]; then
    if [[ "$OS" == "darwin" ]]; then
      add_path_line "$HOME/.bash_profile"
      add_path_line "$HOME/.profile"
    else
      add_path_line "$HOME/.bashrc"
      add_path_line "$HOME/.profile"
    fi
  else
    add_path_line "$HOME/.zshrc"
    add_path_line "$HOME/.zprofile"
    add_path_line "$HOME/.bashrc"
    add_path_line "$HOME/.bash_profile"
    add_path_line "$HOME/.profile"
  fi
fi

say ""
say "Verifying install..."
if "${INSTALL_DIR}/ghdp" --version >/dev/null 2>&1; then
  say "Installed: $("${INSTALL_DIR}/ghdp" --version)"
else
  "${INSTALL_DIR}/ghdp" --help >/dev/null 2>&1 || true
  say "Installed ghdp (version command not available)."
fi

say ""
say "Done."
say "Run 'ghdp --help' in a new terminal when ready."
 
