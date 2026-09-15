#!/usr/bin/env bash
# Install Amnezia Usage Dashboard next to an existing AmneziaWG container.
# Default: LAN / VPN-only (port 8080). Use --public for HTTPS via Caddy.
set -euo pipefail

MODE="lan"
HOSTNAME=""
REPO_URL="https://github.com/int-cloud-automation/amnezia-usage-dashboard.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--lan|--public] [--hostname NAME]

  --lan                 HTTP on port 8080 (default). For home/office LAN or VPN clients.
  --public              HTTPS via Caddy on 80/443. Requires --hostname.
  --hostname NAME       Public DNS name (e.g. stats.example.com). Required with --public.

Run this on the same Linux host where AmneziaWG already runs in Docker.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lan) MODE="lan"; shift ;;
    --public) MODE="public"; shift ;;
    --hostname)
      HOSTNAME="${2:-}"
      if [[ -z "$HOSTNAME" ]]; then
        echo "error: --hostname needs a value" >&2
        exit 1
      fi
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$MODE" == "public" && -z "$HOSTNAME" ]]; then
  echo "error: --public requires --hostname" >&2
  exit 1
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' not found" >&2
    exit 1
  }
}

need_cmd docker
if ! docker compose version >/dev/null 2>&1; then
  echo "error: Docker Compose v2 plugin required (docker compose version)" >&2
  exit 1
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "error: install on the Linux Amnezia host (this OS is $(uname -s))" >&2
  exit 1
fi

# Work from the repo directory (cloned tree or this script's location).
INSTALL_DIR="$SCRIPT_DIR"
if [[ ! -f "$INSTALL_DIR/docker-compose.yml" ]]; then
  INSTALL_DIR="/opt/amnezia-usage-dashboard"
  if [[ ! -w /opt ]]; then
    INSTALL_DIR="$HOME/amnezia-usage-dashboard"
  fi
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
  else
    need_cmd git
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [[ -w "$(dirname "$INSTALL_DIR")" ]]; then
      git clone "$REPO_URL" "$INSTALL_DIR"
    else
      sudo mkdir -p "$INSTALL_DIR"
      sudo git clone "$REPO_URL" "$INSTALL_DIR"
      sudo chown -R "$(id -un):$(id -gn)" "$INSTALL_DIR"
    fi
  fi
fi
cd "$INSTALL_DIR"

echo "==> Looking for AmneziaWG container"
mapfile -t CANDIDATES < <(docker ps --format '{{.Names}}' | grep -iE 'awg|amnezia' || true)
if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  echo "error: no running container matching awg|amnezia. Install AmneziaVPN first." >&2
  exit 1
fi

AWG_CONTAINER=""
for name in "${CANDIDATES[@]}"; do
  if docker exec "$name" awg show all dump >/dev/null 2>&1; then
    AWG_CONTAINER="$name"
    break
  fi
done

if [[ -z "$AWG_CONTAINER" ]]; then
  echo "error: found containers (${CANDIDATES[*]}) but none answer 'awg show all dump'." >&2
  echo "Do not rename Amnezia containers. Fix AmneziaWG, then re-run." >&2
  exit 1
fi

echo "    using container: $AWG_CONTAINER (not renamed)"

AWG_CONF_PATH="/opt/amnezia/awg/awg0.conf"
AWG_CLIENTS_TABLE="/opt/amnezia/awg/clientsTable"
if ! docker exec "$AWG_CONTAINER" test -f "$AWG_CONF_PATH"; then
  if docker exec "$AWG_CONTAINER" test -f /opt/amnezia/awg/wg0.conf; then
    AWG_CONF_PATH="/opt/amnezia/awg/wg0.conf"
  else
    echo "warning: $AWG_CONF_PATH missing inside container — peer enable/disable may fail"
  fi
fi
if ! docker exec "$AWG_CONTAINER" test -f "$AWG_CLIENTS_TABLE"; then
  echo "warning: $AWG_CLIENTS_TABLE missing — client names may show as short keys"
fi

STATS_TIMEZONE="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
if [[ -z "$STATS_TIMEZONE" ]]; then
  STATS_TIMEZONE="UTC"
fi

echo "==> Writing .env"
NEW_PASSWORD=""
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    # portable in-place replace without relying on GNU sed -i backup quirks
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
      BEGIN { done=0 }
      $0 ~ ("^" k "=") { print k "=" v; done=1; next }
      { print }
      END { if (!done) print k "=" v }
    ' .env >"$tmp"
    mv "$tmp" .env
  else
    printf '%s=%s\n' "$key" "$value" >>.env
  fi
}

# Keep existing secrets if they look set; otherwise generate.
current_pw="$(grep -E '^ADMIN_PASSWORD=' .env | head -1 | cut -d= -f2- || true)"
current_sk="$(grep -E '^SECRET_KEY=' .env | head -1 | cut -d= -f2- || true)"

if [[ -z "$current_pw" || "$current_pw" == "change-me-strong-password" || "$current_pw" == "change-me" ]]; then
  need_cmd openssl
  NEW_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  set_env ADMIN_PASSWORD "$NEW_PASSWORD"
else
  NEW_PASSWORD=""  # already configured — do not reprint
fi

if [[ -z "$current_sk" || "$current_sk" == "replace-with-long-random-string" || "$current_sk" == "change-me-to-a-long-random-string" ]]; then
  need_cmd openssl
  set_env SECRET_KEY "$(openssl rand -hex 32)"
fi

set_env ADMIN_USER "admin"
set_env AWG_MODE "docker_exec"
set_env AWG_CONTAINER "$AWG_CONTAINER"
set_env AWG_SHOW_CMD "awg show all dump"
set_env AWG_CONF_PATH "$AWG_CONF_PATH"
set_env AWG_CLIENTS_TABLE "$AWG_CLIENTS_TABLE"
set_env AWG_INTERFACE "awg0"
set_env STATS_TIMEZONE "$STATS_TIMEZONE"
set_env HOST_PROC_PATH "/host/proc"

if [[ "$MODE" == "lan" ]]; then
  set_env SESSION_HTTPS_ONLY "false"
else
  set_env SESSION_HTTPS_ONLY "true"
  if [[ -f Caddyfile ]]; then
    if grep -q 'stats.example.com' Caddyfile; then
      sed -i.bak "s/stats\\.example\\.com/${HOSTNAME}/g" Caddyfile
      rm -f Caddyfile.bak
    fi
  fi
fi

chmod 600 .env

echo "==> Starting containers ($MODE)"
if [[ "$MODE" == "lan" ]]; then
  docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build
else
  docker compose up -d --build
fi

echo "==> Waiting for app startup"
ok=0
for _ in $(seq 1 45); do
  if docker logs awg-stats 2>&1 | grep -q 'Application startup complete'; then
    ok=1
    break
  fi
  sleep 1
done

if [[ "$MODE" == "lan" && "$ok" -eq 1 ]]; then
  code="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/login 2>/dev/null || true)"
  if [[ "$code" != "200" ]]; then
    echo "warning: login page returned HTTP ${code:-none}. Check: docker logs awg-stats --tail 80" >&2
  fi
elif [[ "$ok" -ne 1 ]]; then
  echo "warning: startup not confirmed yet. Check: docker logs awg-stats --tail 80" >&2
fi

# Discover addresses for the summary (best-effort).
LAN_IPS=()
if command -v hostname >/dev/null 2>&1; then
  # shellcheck disable=SC2207
  LAN_IPS=($(hostname -I 2>/dev/null || true))
fi
WG_IP=""
for ip in "${LAN_IPS[@]}"; do
  # Common AmneziaWG server address on the host; confirm in your client config if unsure.
  if [[ "$ip" == 10.8.* ]]; then
    WG_IP="$ip"
    break
  fi
done

echo
echo "=============================="
echo " Amnezia Usage Dashboard ready"
echo "=============================="
echo "Mode:            $MODE"
echo "Install dir:     $INSTALL_DIR"
echo "AmneziaWG:       $AWG_CONTAINER (unchanged)"
echo "Timezone:        $STATS_TIMEZONE"
echo "Username:        admin"
if [[ -n "$NEW_PASSWORD" ]]; then
  echo "Password:        $NEW_PASSWORD"
else
  echo "Password:        (unchanged — see .env ADMIN_PASSWORD)"
fi
echo
if [[ "$MODE" == "lan" ]]; then
  echo "Open from this machine:  http://127.0.0.1:8080"
  for ip in "${LAN_IPS[@]}"; do
    [[ "$ip" == 127.* ]] && continue
    echo "Open from LAN/VPN IP:    http://${ip}:8080"
  done
  if [[ -n "$WG_IP" ]]; then
    echo "Likely VPN URL:          http://${WG_IP}:8080"
  fi
  echo
  echo "Do not forward port 8080 from the public internet."
  echo "Stop later: docker compose -f docker-compose.yml -f docker-compose.lan.yml down"
else
  echo "Open: https://${HOSTNAME}"
  echo "DNS for ${HOSTNAME} must point at this VPS."
  echo "Stop later: docker compose down"
fi
echo
echo "Stopping this stack does NOT stop AmneziaWG."
