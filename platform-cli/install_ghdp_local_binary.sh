#!/usr/bin/env bash
set -euo pipefail

# Standalone installer for a pre-downloaded GHDP binary.
#
# Usage:
#   GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-amd64" bash install_ghdp_local_binary.sh
#   GHDP_MANAGED_INSTALL=1 GHDP_BINARY_PATH="$HOME/Downloads/ghdp-darwin-amd64" bash install_ghdp_local_binary.sh
#
# Env overrides:
#   GHDP_BINARY_PATH="/path/to/ghdp"  (required)
#   GHDP_INSTALL_DIR="/some/dir"      (default: first writable dir on PATH, else $HOME/.local/bin)
#   GHDP_NO_MODIFY_SHELL="1"          (skip editing rc files)
#   GHDP_MANAGED_INSTALL="1"          (persist managed install state)
#   GHDP_STAGED_BINARY_DIR="~/.ghdp/installers" (where the downloaded binary is moved before install)

DEFAULT_INSTALL_DIR="$HOME/.local/bin"
DEFAULT_STAGED_BINARY_DIR="$HOME/.ghdp/installers"
INSTALL_DIR="${GHDP_INSTALL_DIR:-}"
STAGED_BINARY_DIR="${GHDP_STAGED_BINARY_DIR:-$DEFAULT_STAGED_BINARY_DIR}"
NO_MODIFY_SHELL="${GHDP_NO_MODIFY_SHELL:-0}"
RUNTIME_ENV_PATH="${GHDP_RUNTIME_ENV_PATH:-$HOME/.ghdp/runtime.env}"

say() { printf "%s\n" "$*"; }
die() { printf "ERROR: %s\n" "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

need_cmd chmod
need_cmd mkdir
need_cmd mv
need_cmd cp
need_cmd grep

remove_quarantine_if_macos() {
  command -v xattr >/dev/null 2>&1 || return 0
  [[ "$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')" == "darwin" ]] || return 0
  local target
  for target in "$@"; do
    [[ -n "${target:-}" && -e "$target" ]] || continue
    xattr -d com.apple.quarantine "$target" 2>/dev/null || true
  done
}

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

staged_binary_path() {
  local source_name
  source_name="$(basename "${GHDP_BINARY_PATH}")"
  printf "%s" "${STAGED_BINARY_DIR}/${source_name}"
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

choose_install_dir() {
  if [[ -n "$INSTALL_DIR" ]]; then
    return 0
  fi

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

  INSTALL_DIR="$DEFAULT_INSTALL_DIR"
}

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

[[ -n "${GHDP_BINARY_PATH:-}" ]] || die "GHDP_BINARY_PATH is required."
[[ -f "${GHDP_BINARY_PATH}" ]] || die "Local GHDP binary not found: ${GHDP_BINARY_PATH}"
remove_quarantine_if_macos "${GHDP_BINARY_PATH}" "$0"

choose_install_dir

STAGED_BINARY="$(staged_binary_path)"

say "Installing ghdp from:"
say "  Source:  ${GHDP_BINARY_PATH}"
say "  Staged:  ${STAGED_BINARY}"
say "  Target:  ${INSTALL_DIR}/ghdp"
say ""

mkdir -p "$STAGED_BINARY_DIR"
mv "${GHDP_BINARY_PATH}" "${STAGED_BINARY}"
mkdir -p "$INSTALL_DIR"
cp "${STAGED_BINARY}" "${INSTALL_DIR}/ghdp"
remove_quarantine_if_macos "${STAGED_BINARY}" "${INSTALL_DIR}/ghdp"
chmod +x "${INSTALL_DIR}/ghdp"

if managed_install_enabled; then
  write_managed_install_marker
  write_install_state "managed"
else
  remove_managed_install_marker
  write_install_state "standard"
fi

export PATH="$INSTALL_DIR:$PATH"

if [[ "$NO_MODIFY_SHELL" != "1" ]] && [[ "$INSTALL_DIR" == "$DEFAULT_INSTALL_DIR" ]]; then
  SHELL_NAME="$(basename "${SHELL:-}")"
  if [[ "$SHELL_NAME" == "zsh" ]]; then
    add_path_line "$HOME/.zshrc"
    add_path_line "$HOME/.zprofile"
  elif [[ "$SHELL_NAME" == "bash" ]]; then
    add_path_line "$HOME/.bashrc"
    add_path_line "$HOME/.bash_profile"
    add_path_line "$HOME/.profile"
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
