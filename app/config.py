from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_user: str = "admin"
    admin_password: str = "change-me"
    secret_key: str = "change-me-to-a-long-random-string"

    # How to read AmneziaWG: docker_exec | local | demo
    awg_mode: str = "docker_exec"
    awg_container: str = "amnezia-awg2"  # MUST keep the real Amnezia container name (e.g. amnezia-awg2). Renaming it breaks the Amnezia desktop app user list.
    awg_show_cmd: str = "awg show all dump"
    awg_conf_path: str = "/opt/amnezia/awg/awg0.conf"
    awg_clients_table: str = "/opt/amnezia/awg/clientsTable"
    awg_interface: str = "awg0"

    scrape_interval_sec: int = 60  # write traffic history / quotas
    online_poll_sec: int = 30  # cheap awg show for fresh Overview
    online_threshold_sec: int = 900  # idle phone with VPN still on
    name_cache_ttl_sec: int = 300
    # Host /proc mount inside the container (docker-compose mounts /proc → /host/proc).
    host_proc_path: str = "/host/proc"
    database_path: str = "/data/awg_stats.db"
    # Calendar days (Today / DAU / quota periods) are bucketed in this zone.
    stats_timezone: str = "UTC"

    # Session cookie
    session_max_age_sec: int = 60 * 60 * 24 * 14
    session_https_only: bool = True

    # Brute-force protection on /login and password change
    login_max_attempts: int = 5
    login_lockout_sec: int = 180  # 3 minutes after 5 failed attempts


@lru_cache
def get_settings() -> Settings:
    return Settings()
