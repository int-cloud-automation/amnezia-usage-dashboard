# Agent install playbook — Amnezia Usage Dashboard

You are an AI installer. **Install and start** this dashboard on the Linux host you already have shell access to. Do not stop at explaining. Prefer the automated script over hand-editing files.

Repo: https://github.com/int-cloud-automation/amnezia-usage-dashboard

## Hard rules

1. **Never rename** any existing Amnezia / AmneziaWG Docker container. Point `AWG_CONTAINER` at the name that already exists.
2. **Never publish** the dashboard to the public internet unless the user gave a hostname or explicitly asked for public HTTPS. Default is **LAN / VPN-only**.
3. **Never commit** `.env`. Show the generated password **once** at the end so the user can log in.
4. Do not change AmneziaWG VPN ports, `awg0.conf`, or peer keys.
5. Do not `docker compose down` unrelated Amnezia stacks.

## 0. Mode

| User said | Mode |
|---|---|
| hostname / domain / Cloudflare / HTTPS / public VPS | `public` |
| LAN, local network, home, no internet, VPN-only | `lan` |
| nothing | **`lan`** |

## 1. Preconditions (fail fast)

```bash
uname -s   # must be Linux
docker version
docker compose version
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
```

If Docker is missing, install the distro’s Docker Engine + Compose plugin, then continue.

Confirm AmneziaWG is readable (do not create a VPN container):

```bash
docker ps --format '{{.Names}}' | grep -iE 'awg|amnezia' || true
docker exec CONTAINER awg show all dump
```

If `awg show all dump` fails, **stop** and tell the user. Do not guess another VPN stack.

## 2. Get the code and run install.sh

```bash
INSTALL_DIR=/opt/amnezia-usage-dashboard
if [ ! -w /opt ]; then INSTALL_DIR="$HOME/amnezia-usage-dashboard"; fi

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  sudo mkdir -p "$INSTALL_DIR"
  sudo git clone https://github.com/int-cloud-automation/amnezia-usage-dashboard.git "$INSTALL_DIR"
  sudo chown -R "$(id -un):$(id -gn)" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
chmod +x install.sh
```

If `git` is missing: install it (`apt-get install -y git` or distro equivalent).

### LAN (default)

```bash
./install.sh --lan
```

### Public HTTPS

```bash
./install.sh --public --hostname USER_HOSTNAME
```

`install.sh` finds the AmneziaWG container, writes `.env`, starts Compose, and prints URL + password. Prefer that over manually editing files.

Only fall back to the manual steps in `README.md` if the script fails.

## 3. Verify

```bash
docker compose ps
docker logs awg-stats --tail 50
```

Expect `Application startup complete` and no repeating `poll failed`.

LAN:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/login
```

Must be `200`.

## 4. Tell the user (required)

Repeat the script summary clearly:

- URL(s) to open
- Username `admin` and password (from the script output / `.env`)
- That the Amnezia container was **not** renamed
- How to stop (LAN vs public) — stopping this stack does **not** stop AmneziaWG

## Troubleshooting

| Symptom | What to do |
|---|---|
| `awg show` not found | Wrong container. List `docker ps` again; do not rename. |
| Empty client names | `clientsTable` path wrong; inspect volumes inside the Amnezia container. |
| Login works then immediately logged out | LAN + `SESSION_HTTPS_ONLY=true` → set `false`, recreate. |
| `Cannot connect to Docker daemon` | Root or `docker` group, then retry. |
| Port 8080 already used | `ss -lntp \| grep 8080`; change host port in `docker-compose.lan.yml`. |
| Compose unknown | Use `docker compose` (plugin), not legacy `docker-compose`, unless that is all that exists. |
| Performance CPU empty | Confirm `/proc:/host/proc:ro` is mounted (included in `docker-compose.yml`). |

## Out of scope

Do not install AmneziaVPN itself. Do not migrate peers. Do not open the VPN UDP port through Cloudflare.
