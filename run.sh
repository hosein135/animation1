#!/usr/bin/env bash
# Bootstrap: curl -> Nix (25.05 flake) -> render animation into ./output
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

need_sudo() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    die "Need root/sudo to install packages, but sudo is not available."
  fi
}

# ---------------------------------------------------------------------------
# 1. Ensure curl is available (cross-distro)
# ---------------------------------------------------------------------------
install_curl() {
  log "curl not found — installing..."
  if command -v apt-get >/dev/null 2>&1; then
    need_sudo apt-get update -y
    need_sudo apt-get install -y curl
  elif command -v dnf >/dev/null 2>&1; then
    need_sudo dnf install -y curl
  elif command -v yum >/dev/null 2>&1; then
    need_sudo yum install -y curl
  elif command -v pacman >/dev/null 2>&1; then
    need_sudo pacman -Sy --noconfirm curl
  elif command -v zypper >/dev/null 2>&1; then
    need_sudo zypper --non-interactive install curl
  elif command -v apk >/dev/null 2>&1; then
    need_sudo apk add --no-cache curl
  elif command -v emerge >/dev/null 2>&1; then
    need_sudo emerge --ask=n net-misc/curl
  elif command -v xbps-install >/dev/null 2>&1; then
    need_sudo xbps-install -Sy curl
  elif command -v brew >/dev/null 2>&1; then
    brew install curl
  else
    die "Could not detect a package manager. Install curl manually, then re-run."
  fi
  command -v curl >/dev/null 2>&1 || die "curl install appeared to succeed but curl is still missing from PATH."
  ok "curl installed: $(command -v curl)"
}

ensure_curl() {
  if command -v curl >/dev/null 2>&1; then
    ok "curl is ready ($(curl --version | head -n1))"
  else
    install_curl
  fi
}

# ---------------------------------------------------------------------------
# 2. Ensure Nix is installed and usable
# ---------------------------------------------------------------------------
source_nix_profile() {
  # Multi-user (daemon) and single-user installs
  local candidates=(
    "/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh"
    "${HOME}/.nix-profile/etc/profile.d/nix.sh"
    "/etc/profile.d/nix.sh"
  )
  local f
  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then
      # shellcheck disable=SC1090
      . "$f"
      return 0
    fi
  done
  return 1
}

nix_ready() {
  source_nix_profile || true
  command -v nix >/dev/null 2>&1
}

install_nix() {
  log "Nix not found — installing (Deterministic Installer)..."
  # Official installer; --daemon preferred when available
  if [[ "$(uname -s)" == "Linux" ]] && [[ -d /run/systemd/system ]]; then
    curl -L https://nixos.org/nix/install | sh -s -- --daemon
  else
    curl -L https://nixos.org/nix/install | sh -s -- --no-daemon
  fi
  source_nix_profile || true
  command -v nix >/dev/null 2>&1 || die "Nix installed but 'nix' is not on PATH. Open a new shell or source the Nix profile, then re-run."
  ok "Nix installed: $(nix --version)"
}

configure_nix_flakes() {
  local conf_dir conf
  conf_dir="${HOME}/.config/nix"
  conf="${conf_dir}/nix.conf"
  mkdir -p "$conf_dir"
  if [[ -f "$conf" ]] && grep -q 'experimental-features' "$conf"; then
    if ! grep -Eq 'experimental-features.*(nix-command|flakes)' "$conf"; then
      warn "Appending flakes support to existing experimental-features in ${conf}"
      # Ensure both features are listed
      if ! grep -q 'nix-command' "$conf"; then
        sed -i.bak 's/experimental-features *= */&nix-command /' "$conf" 2>/dev/null \
          || printf '\nexperimental-features = nix-command flakes\n' >> "$conf"
      fi
      if ! grep -q 'flakes' "$conf"; then
        sed -i.bak 's/experimental-features *= */&flakes /' "$conf" 2>/dev/null || true
      fi
    fi
  else
    printf 'experimental-features = nix-command flakes\n' >> "$conf"
  fi
  ok "Nix flakes enabled (${conf})"
}

ensure_nix() {
  if nix_ready; then
    ok "Nix is ready ($(nix --version))"
  else
    install_nix
  fi
  configure_nix_flakes
}

# ---------------------------------------------------------------------------
# 3. Run the pinned Nix flake and generate the animation
# ---------------------------------------------------------------------------
run_pipeline() {
  log "Entering Nix flake (nixos-25.05) and generating animation..."
  mkdir -p "${ROOT}/output"

  # Flake is pinned to github:NixOS/nixpkgs/nixos-25.05
  # First run generates flake.lock automatically.
  # Extra args are forwarded to scripts/pipeline.py (e.g. --renderer gpu).
  nix run "${ROOT}#animate" \
    --option connect-timeout 60 \
    --option download-attempts 5 \
    --accept-flake-config \
    -- "$@"

  ok "Done. Outputs are in: ${ROOT}/output"
  ls -la "${ROOT}/output" || true
}

main() {
  log "Animation project bootstrap (GPU/CPU accelerated pipeline)"
  ensure_curl
  ensure_nix
  run_pipeline "$@"
}

main "$@"
