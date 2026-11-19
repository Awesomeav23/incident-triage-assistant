"""Capture UI screenshots for the README (dev tool; needs the API + `npm run dev` running).

    pip install playwright==1.49.1   # uses your installed Google Chrome, no browser download
    python -m scripts.screenshots

Captures triage, triage-no-match, incidents, incident-detail, triage-dark, triage-mobile.
Costs nothing in demo mode (the default). With LLM_MODE=claude each triage is a paid call
(~2-4 cents). Note: it overwrites triage.png, which the README labels as real Claude output,
so re-run it in demo mode only if you also update that caption. duplicate.png, stats.png and
triage-demo.png were captured by hand.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://localhost:5181"
OUT = Path("docs/screenshots")


def triage(page, sample: str) -> None:
    page.goto(URL)
    page.get_by_role("button", name=sample).click()
    page.get_by_role("button", name="Triage").click()
    page.get_by_test_id("result").wait_for(timeout=90_000)
    page.wait_for_timeout(300)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        desktop = browser.new_page(viewport={"width": 1360, "height": 900})
        triage(desktop, "Redis failover")
        desktop.get_by_text("Similar past incidents").locator("..").locator("li button").first.click()
        desktop.screenshot(path=OUT / "triage.png", full_page=True)

        triage(desktop, "No match: Rust compile error")
        desktop.screenshot(path=OUT / "triage-no-match.png", full_page=True)

        desktop.goto(URL + "/incidents")
        desktop.get_by_role("cell").first.wait_for()
        desktop.screenshot(path=OUT / "incidents.png", full_page=True)
        desktop.get_by_role("row").nth(1).click()
        desktop.get_by_role("dialog").get_by_text("Root cause").wait_for()
        desktop.screenshot(path=OUT / "incident-detail.png")

        dark = browser.new_page(viewport={"width": 1360, "height": 900}, color_scheme="dark")
        triage(dark, "Expired TLS cert")
        dark.screenshot(path=OUT / "triage-dark.png", full_page=True)

        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        triage(phone, "Pod OOMKilled")
        phone.screenshot(path=OUT / "triage-mobile.png", full_page=True)
        width = phone.evaluate("document.documentElement.scrollWidth")
        print(f"mobile page width {width}px (viewport 390px) -> {'OK' if width <= 390 else 'HORIZONTAL SCROLL'}")

        browser.close()
    for f in sorted(OUT.glob("*.png")):
        print(f, f.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
