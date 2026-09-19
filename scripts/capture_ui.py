from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "ui-reference"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def capture_implemented(browser, base_url: str) -> None:
    desktop = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = desktop.new_page()
    for surface in ("library", "scans", "findings", "reports", "settings"):
        page.goto(f"{base_url}/{surface}", wait_until="networkidle")
        page.screenshot(path=OUTPUT / f"implemented-desktop-{surface}.png", full_page=False)
    desktop.close()

    tablet = browser.new_context(viewport={"width": 1024, "height": 1366}, device_scale_factor=1)
    page = tablet.new_page()
    for surface in ("library", "settings"):
        page.goto(f"{base_url}/{surface}", wait_until="networkidle")
        page.screenshot(path=OUTPUT / f"implemented-tablet-{surface}.png", full_page=False)
    tablet.close()


def isolate_prototype(page, surface: str) -> None:
    page.goto("http://127.0.0.1:54160/index.html", wait_until="domcontentloaded", timeout=15_000)
    page.locator(f'#pcMock .pc-nav button[data-view="{surface}"]').click()
    page.evaluate(
        """() => {
            const mock = document.querySelector('#pcMock');
            document.documentElement.style.background = '#f4f6f4';
            document.body.style.margin = '0';
            mock.style.position = 'fixed';
            mock.style.inset = '0';
            mock.style.zIndex = '2147483647';
            mock.style.width = '100vw';
            mock.style.height = '100vh';
            mock.style.maxWidth = 'none';
            mock.style.border = '0';
            mock.style.borderRadius = '0';
        }"""
    )


def capture_prototype(browser) -> None:
    desktop = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = desktop.new_page()
    for surface in ("library", "scans", "findings", "reports", "settings"):
        isolate_prototype(page, surface)
        page.screenshot(path=OUTPUT / f"prototype-desktop-{surface}.png", full_page=False)
    desktop.close()

    tablet = browser.new_context(viewport={"width": 1024, "height": 1366}, device_scale_factor=1)
    page = tablet.new_page()
    for surface in ("library", "settings"):
        isolate_prototype(page, surface)
        page.screenshot(path=OUTPUT / f"prototype-tablet-{surface}.png", full_page=False)
    tablet.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture prototype and implementation review screenshots.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8087")
    parser.add_argument("--skip-prototype", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(CHROME) if CHROME.exists() else None)
        if not args.skip_prototype:
            capture_prototype(browser)
        capture_implemented(browser, args.base_url)
        browser.close()


if __name__ == "__main__":
    main()
