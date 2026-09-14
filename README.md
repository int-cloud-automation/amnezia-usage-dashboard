# Amnezia Usage Dashboard

A small dashboard for **AmneziaWG**: who is online, how much they transferred, history, and optional traffic quotas.

Built for a single VPS and a handful of peers. FastAPI + SQLite, one Docker Compose file, no extra monitoring stack.

![Overview](docs/screenshots/overview.png)

## What it does

- **Overview** — live online status, download/upload speed, today / 7 days / month / lifetime totals
- **Clients** — handshake, lifetime traffic, enable / disable a peer
- **History** — 7 / 30 / 90 day charts and per-client share
- **Quotas** — GB cap per day, rolling week, calendar month, or lifetime; auto-disable when exceeded

Traffic is shown from the **client's** point of view (downloaded / uploaded). “Online” means a handshake within the last 3 minutes.

## Screenshots

Demo data (`phone`, `laptop`, `tablet`, `work`) — not a real VPN.

| Login | Overview |
|---|---|
| ![Login](docs/screenshots/login.png) | ![Overview](docs/screenshots/overview.png) |

| Clients | History |
|---|---|
| ![Clients](docs/screenshots/clients.png) | ![History](docs/screenshots/history.png) |

![Quotas](docs/screenshots/quotas.png)

## Prerequisites

### Production (same VPS as the VPN)

- A Linux VPS where **[AmneziaVPN](https://github.com/amnezia-vpn/amnezia-client)** is already installed and **AmneziaWG** is running in Docker
- **Docker Engine 24+** with **Compose v2** (`docker compose version`)
- Permission to use the Docker socket (root, or a user in the `docker` group)
- The AmneziaWG container name (find it with the command below — **do not rename** that container; the Amnezia desktop app depends on the original name)
- Outbound HTTPS from the VPS (to pull images)
- A **domain name** pointed at the VPS if you want HTTPS in a browser
- A reverse proxy in front of the panel (Caddy is included). For Cloudflare orange-cloud, restrict ports 80/443 to [Cloudflare IP ranges](https://www.cloudflare.com/ips/)

AmneziaWG UDP/TCP VPN ports are not used by this dashboard and must stay reachable as they are today.

```bash
docker ps --format '{{.Names}}' | grep -iE 'awg|amnezia'
docker exec <container> awg show all dump
```

If `awg show all dump` fails, the panel cannot collect stats.

### Local demo (no VPN)

- **Python 3.12+**
- `pip` and `venv`

## Quick start (VPS)

```bash
git clone https://github.com/meledinalexander/amnezia-usage-dashboard.git
cd amnezia-usage-dashboard
cp .env.example .env
```

Edit `.env`:

1. Set a strong `ADMIN_PASSWORD` and a long random `SECRET_KEY`
2. Set `AWG_CONTAINER` to the name from `docker ps` (often `amnezia-awg2`)
3. Set `STATS_TIMEZONE` to your IANA zone if you do not want UTC day boundaries (`Europe/Moscow`, `Asia/Yekaterinburg`, …)

Edit `Caddyfile`: replace `stats.example.com` with your hostname.

```bash
docker compose up -d --build
```

Do **not** publish port `8080` to the internet. Caddy listens on 80/443; the app stays on the internal Docker network.

## Local demo (no VPN)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -r requirements.txt
export AWG_MODE=demo ADMIN_PASSWORD=demo SECRET_KEY=dev DATABASE_PATH=./data/demo.db
uvicorn app.main:app --reload --port 8080
```

On Windows PowerShell, set the same variables with `$env:AWG_MODE="demo"` (and so on).

Open http://127.0.0.1:8080 — user `admin`, password `demo`.

## Configuration

| Variable | Meaning |
|---|---|
| `ADMIN_USER` / `ADMIN_PASSWORD` | Panel login |
| `SECRET_KEY` | Session signing key |
| `AWG_MODE` | `docker_exec` (production) / `local` / `demo` |
| `AWG_CONTAINER` | AmneziaWG container name. **Do not rename** the live Amnezia container |
| `AWG_CONF_PATH` / `AWG_CLIENTS_TABLE` | Paths *inside* that container |
| `STATS_TIMEZONE` | IANA zone for “today” and quota periods (`UTC`, `Europe/Moscow`, …) |
| `ONLINE_THRESHOLD_SEC` | Handshake age that still counts as online (default 180) |

## Security notes

- Change the password and `SECRET_KEY` before the panel is reachable
- Prefer Cloudflare Access (or similar) in front of the login form
- Quotas remove the peer from the *live* interface; they do not edit `awg0.conf` on disk
- A peer you disable by hand stays disabled even if a quota would re-enable it
- The dashboard reaches Docker only through a socket proxy (`exec` / `inspect`), not a raw `docker.sock` mount into the app container

## License

MIT
