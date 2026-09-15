"""Capture demo-mode screenshots for the README. Requires a running dashboard instance."""

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8080"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "login.png"), full_page=True)

        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "demo")
        page.click('button[type="submit"]')
        page.wait_for_selector("#clients-body tr")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "overview.png"), full_page=True)

        page.goto(f"{BASE}/clients", wait_until="networkidle")
        page.wait_for_selector("#body tr")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "clients.png"), full_page=True)

        page.goto(f"{BASE}/history", wait_until="networkidle")
        page.wait_for_selector("#body tr")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "history.png"), full_page=True)

        page.goto(f"{BASE}/quotas", wait_until="networkidle")
        page.wait_for_selector("#peer")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "quotas.png"), full_page=True)

        page.goto(f"{BASE}/performance", wait_until="networkidle")
        page.wait_for_selector("#htop .htop-meters")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "performance.png"), full_page=True)

        browser.close()


if __name__ == "__main__":
    main()
