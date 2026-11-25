"""Проверка интерфейса в headless Chromium: ошибки JS, ключевые элементы, вёрстка."""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/test/out"
OUT.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("VIRALCUT_URL", "http://127.0.0.1:8765")
PROJECT = sys.argv[1] if len(sys.argv) > 1 else ""

report = {"console_errors": [], "page_errors": [], "checks": {}}
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 920}, device_scale_factor=2)
    page.on("console", lambda m: report["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: report["page_errors"].append(str(e)))

    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    report["checks"]["hero_title"] = page.locator("h1.hero-title").inner_text()[:80]
    report["checks"]["mode_buttons"] = page.locator(".seg[data-group='mode'] button").count()
    report["checks"]["swatches"] = page.locator("#accents .swatch").count()

    # пустая ссылка -> тост об ошибке, без запуска
    page.click("#startBtn")
    page.wait_for_timeout(250)
    report["checks"]["empty_url_toast"] = page.locator("#toasts .toast").count()

    page.screenshot(path=str(OUT / "ui_home.png"), full_page=True)

    if PROJECT:
        page.goto(f"{BASE}/#{PROJECT}", wait_until="domcontentloaded")
        page.wait_for_selector(".clip-card", timeout=20000)
        page.wait_for_timeout(900)
        report["checks"]["cards"] = page.locator(".clip-card").count()
        report["checks"]["videos"] = page.locator(".clip-card video").count()
        report["checks"]["downloads"] = page.locator("a[download]").count()
        report["checks"]["zip_link"] = page.locator("a[href$='/zip']").count()
        report["checks"]["title"] = page.locator(".project-head h2").inner_text()[:80]
        page.screenshot(path=str(OUT / "ui_project.png"), full_page=True)

        # редактор
        page.locator(".clip-card .clip-media").first.click()
        page.wait_for_timeout(500)
        report["checks"]["modal_open"] = page.locator(".modal-panel").is_visible()
        report["checks"]["editor_range"] = page.locator("#edStart").count()
        page.screenshot(path=str(OUT / "ui_editor.png"))
        page.locator("#modal .modal-head [data-close]").click()
        page.wait_for_timeout(250)
        report["checks"]["modal_closed"] = not page.locator(".modal-panel").is_visible()

        # мобильная вёрстка
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        report["checks"]["mobile_overflow_px"] = overflow
        page.screenshot(path=str(OUT / "ui_mobile.png"), full_page=True)

    browser.close()

print(json.dumps(report, ensure_ascii=False, indent=1))
