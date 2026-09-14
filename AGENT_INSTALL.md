# Agent install playbook — Amnezia Usage Dashboard

You are an AI installer. Your job is to **install and start** this dashboard on the machine you already have shell access to. Do not stop at explaining. Run the commands. Prefer discovery over asking.

Repo: https://github.com/meledinalexander/amnezia-usage-dashboard

## Hard rules

1. **Never rename** any existing Amnezia / AmneziaWG Docker container. The desktop app keys off the original name. Point `AWG_CONTAINER` at the name that already exists.
2. **Never publish** the dashboard to the public internet unless the user gave a hostname or explicitly asked for public HTTPS. Default is **LAN / VPN-only**.
3. **Never commit** `.env`. Do not print generated passwords in chat logs if you can avoid it; show them once at the end so the user can log in.
4. Do not change AmneziaWG VPN ports, `awg0.conf`, or peer keys.
5. Do not `docker compose down` unrelated Amnezia stacks.

## 0. Mode

If the user did not choose:

| User said | Mode |
|---|---|
| hostname / domain / Cloudflare / HTTPS / VPS public | `public` |
| LAN, local network, home, no internet, VPN-only | `lan` |
| nothing | **`lan`** |

## 1. Preconditions (fail fast)

Need Linux, Docker Engine, Compose v2, and a running AmneziaWG container.

```bash
uname -s
docker version
docker compose version
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
```

If Docker is missing, install the distro’s Docker Engine + Compose plugin, then continue.

Find the AmneziaWG container (do not create one):

```bash
docker ps --format '{{.Names}}' | grep -iE 'awg|amnezia' || true
```

Pick the container that answers this (usually `amnezia-awg2` or similar):

```bash
docker exec CONTAINER awg show all dump
docker exec CONTAINER test -f /opt/amnezia/awg/awg0.conf && echo conf_ok
docker exec CONTAINER test -f /opt/amnezia/awg/clientsTable && echo table_ok
```

If `awg show all dump` fails, stop and tell the user AmneziaWG is not readable. Do not guess another VPN stack.

Timezone: `timedatectl show -p Timezone --value 2>/dev/null || echo UTC`

## 2. Get the code

```bash
# Prefer /opt, fall back to $HOME
INSTALL_DIR=/opt/amnezia-usage-dashboard
if [ ! -w /opt ]; then INSTALL_DIR="$HOME/amnezia-usage-dashboard"; fi

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  sudo mkdir -p "$INSTALL_DIR"
  sudo git clone https://github.com/meledinalexander/amnezia-usage-dashboard.git "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
sudo chown -R "$(id -un):$(id -gn)" "$INSTALL_DIR" 2>/dev/null || true
```

If `git` is missing: `apt-get update && apt-get install -y git` (or the distro equivalent).

## 3. Write `.env`

```bash
cp -n .env.example .env
```

Generate secrets (do not use the placeholders):

```bash
ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
SECRET_KEY="$(openssl rand -hex 32)"
```

Set at least:

```
ADMIN_USER=admin
ADMIN_PASSWORD=<generated>
SECRET_KEY=<generated>
AWG_MODE=docker_exec
AWG_CONTAINER=<name from step 1>
AWG_SHOW_CMD=awg show all dump
AWG_CONF_PATH=/opt/amnezia/awg/awg0.conf
AWG_CLIENTS_TABLE=/opt/amnezia/awg/clientsTable
AWG_INTERFACE=awg0
STATS_TIMEZONE=<from timedatectl, else UTC>
```

If `awg0.conf` / `clientsTable` are missing, `docker inspect CONTAINER` and look for the mounted Amnezia data path; update the two paths. Typical alternate: `/opt/amnezia/awg/wg0.conf`.

`chmod 600 .env`

### LAN mode

```
SESSION_HTTPS_ONLY=false
```

### Public mode

```
SESSION_HTTPS_ONLY=true
```

In `Caddyfile` replace `stats.example.com` with the user’s hostname (both `http://` and `https://` lines).

## 4. Start

### LAN (default)

```bash
docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build
```

Do **not** start Caddy. Port `8080` is published.

If the host has a public NIC **and** a private NIC, bind 8080 to the private IP only. Discover it with `hostname -I` (skip `127.0.0.1` and the public address). Then in `docker-compose.lan.yml`:

```yaml
ports:
  - "<LAN_IP>:8080:8080"
```

`<LAN_IP>` is **this machine’s** address on the home/office network, not an example from the docs. Recreate: `docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d`.

Firewall: allow 8080 only from private ranges (adjust to the real LAN):

```bash
# example, Debian/Ubuntu ufw
ufw allow from 192.168.0.0/16 to any port 8080 proto tcp
ufw allow from 10.0.0.0/8 to any port 8080 proto tcp
```

Do not `ufw allow 8080/tcp` from anywhere.

VPN-only access: users already connected to AmneziaWG open `http://<wg-server-ip>:8080` (often `10.8.1.1` — confirm from `awg show` / client config).

### Public mode

```bash
docker compose up -d --build
```

Do not add `8080:8080` on `awg-stats`. Caddy should listen on 80/443. If Cloudflare orange-cloud is in use, restrict 80/443 to Cloudflare IPs; do not expose the origin on a raw public IP without that.

## 5. Verify

```bash
docker compose ps
docker logs awg-stats --tail 50
```

Expect `Application startup complete` and no repeating `poll failed`.

LAN check:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/login
```

Must be `200`. Then:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -c /tmp/aud.jar -b /tmp/aud.jar \
  -X POST http://127.0.0.1:8080/login \
  --data-urlencode "username=admin" \
  --data-urlencode "password=$ADMIN_PASSWORD"
curl -sS -o /dev/null -w '%{http_code}\n' -b /tmp/aud.jar http://127.0.0.1:8080/api/overview
rm -f /tmp/aud.jar
```

Login POST → `303`, overview → `200`. If overview is `401` on LAN, `SESSION_HTTPS_ONLY` is still true.

## 6. Tell the user (required)

Print once:

- URL(s): LAN `http://<LAN_IP>:8080` and/or VPN `http://<wg-ip>:8080` and/or public `https://<hostname>`
- Username `admin` and the generated password
- That the Amnezia container was **not** renamed
- How to stop: `docker compose -f docker-compose.yml -f docker-compose.lan.yml down` (LAN) or `docker compose down` (public) — this does not stop AmneziaWG

## Troubleshooting

| Symptom | What to do |
|---|---|
| `awg show` not found | Wrong container. List `docker ps` again; do not rename. |
| Empty client names | `clientsTable` path is wrong; inspect volumes. |
| Login works then immediately logged out | LAN + `SESSION_HTTPS_ONLY=true` → set false, recreate. |
| `Cannot connect to Docker daemon` | Run as root or add the user to `docker`, retry. |
| Port 8080 already used | Find with `ss -lntp \| grep 8080`; pick another host port in the lan compose file (`<LAN_IP>:8081:8080`). |
| Compose command unknown | Use `docker compose` (plugin), not legacy `docker-compose` unless that is what exists. |

## Out of scope

Do not install AmneziaVPN itself. Do not migrate peers. Do not open the VPN UDP port through Cloudflare.
