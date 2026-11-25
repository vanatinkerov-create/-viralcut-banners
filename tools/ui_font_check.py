import json, os
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8770"; PID="f1bf1baf32b5"
report={"console":[],"pageerrors":[],"checks":{}}
with sync_playwright() as p:
    b=p.chromium.launch(); page=b.new_page(viewport={"width":1440,"height":960}, device_scale_factor=2)
    page.on("console", lambda m: report["console"].append(m.text) if m.type=="error" else None)
    page.on("pageerror", lambda e: report["pageerrors"].append(str(e)))
    page.goto(BASE+"/", wait_until="domcontentloaded"); page.wait_for_timeout(1200)
    report["checks"]["font_options"] = page.locator("#fontSelect option").count()
    report["checks"]["pos_options"] = page.locator("#posSelect option").count()
    report["checks"]["default_size"] = page.locator("#sizeRange").input_value()
    report["checks"]["sample_font_before"] = page.locator("#fontSample").evaluate("el => getComputedStyle(el).fontFamily")
    page.select_option("#fontSelect", "russo"); page.wait_for_timeout(300)
    report["checks"]["sample_font_after"] = page.locator("#fontSample").evaluate("el => getComputedStyle(el).fontFamily")
    page.locator("#sizeRange").evaluate("el => { el.value = 150; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    page.wait_for_timeout(150)
    report["checks"]["size_label"] = page.locator("#sizeVal").inner_text()

    page2 = b.new_page(viewport={"width": 1440, "height": 960})
    page2.on("pageerror", lambda e: report["pageerrors"].append(str(e)))
    page2.goto(f"{BASE}/#{PID}", wait_until="domcontentloaded")
    page2.wait_for_selector(".clip-card", timeout=15000); page2.wait_for_timeout(700)
    page2.locator(".clip-card .clip-media").first.click(); page2.wait_for_timeout(500)
    report["checks"]["editor_font"] = page.locator("#edFont").input_value()
    report["checks"]["editor_size"] = page.locator("#edSize").input_value()
    report["checks"]["editor_pos"] = page.locator("#edPos").input_value()
    report["checks"]["editor_offset"] = page.locator("#edOffset").input_value()
    b.close()
print(json.dumps(report, ensure_ascii=False, indent=1))
