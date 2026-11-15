"""Takes screenshots of a website on desktop and on mobile, the way a visitor sees it.

The Web agent starts this file as its own process, so the browser never blocks the panel.
Try it by hand:   python capture.py shots https://example.com
Check it works:   python capture.py --check
It needs Playwright:   pip install playwright   (it drives the Edge or Chrome already on this computer)
"""

import base64
import json
import sys
from pathlib import Path

DEVICES = {
    "desktop": {"viewport": {"width": 1280, "height": 800}, "device_scale_factor": 1},
    "mobile": {
        "viewport": {"width": 390, "height": 780}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True,
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15"
                      " (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    },
}
# How many page pixels go into each stretch of the overview. Stretches are saved at half size:
# enough to judge layout, photos, colors and spacing, and cheap to send to the model.
STRETCH = {"desktop": 2800, "mobile": 1560}
HOME_STRETCHES = 3
JPEG_QUALITY = 72

# Scroll down slowly so images that load "when you get there" are really there, then go back up.
SCROLL_THROUGH = """async () => {
  const pause = (ms) => new Promise((done) => setTimeout(done, ms));
  const end = Math.min(document.documentElement.scrollHeight, 16000);
  for (let y = 0; y < end; y += window.innerHeight) { window.scrollTo(0, y); await pause(110); }
  window.scrollTo(0, 0);
  await pause(350);
}"""

# Facts you cannot get from a picture: real sizes, font names, and whether the page spills sideways.
PAGE_FACTS = """() => {
  const doc = document.documentElement;
  const seen = (el) => { const box = el.getBoundingClientRect(); return box.width > 0 && box.height > 0; };
  const font = (el) => el ? getComputedStyle(el).fontFamily.split(',')[0].replace(/["']/g, '').trim() : '';
  const size = (el) => el ? Math.round(parseFloat(getComputedStyle(el).fontSize)) : 0;
  const h1 = document.querySelector('h1');
  const paragraph = document.querySelector('main p, article p, p') || document.body;
  const texts = [...document.querySelectorAll('p, li, a, span, label, td, small, button')].filter(seen).slice(0, 3000);
  const smallTexts = texts.filter((el) => el.innerText && el.innerText.trim().length > 2 && parseFloat(getComputedStyle(el).fontSize) < 12).length;
  const pinned = [...document.querySelectorAll('body *')].slice(0, 6000).filter((el) => {
    const position = getComputedStyle(el).position;
    return (position === 'fixed' || position === 'sticky') && seen(el);
  });
  return {
    page_height: doc.scrollHeight,
    spills_sideways: doc.scrollWidth > window.innerWidth + 2,
    heading_font: font(h1), heading_size: size(h1),
    text_font: font(paragraph), text_size: size(paragraph),
    small_texts: smallTexts,
    cookie_notice: pinned.some((el) => /cookie/i.test(el.innerText || '')),
  };
}"""


def launch(playwright):
    """Playwright's own Chromium if it was installed; otherwise the Edge or Chrome already on this computer."""
    for channel in (None, "msedge", "chrome"):
        try:
            browser = playwright.chromium.launch(channel=channel) if channel else playwright.chromium.launch()
            return browser, channel or "chromium"
        except Exception:
            continue
    raise RuntimeError("No encontré ningún navegador. Instala Edge o Chrome, o ejecuta: playwright install chromium")


def shoot(session, path: Path, device: str, top: int, height: int, scale: float) -> dict:
    """Save one rectangle of the page as a JPEG. It can be below what the window shows."""
    width = DEVICES[device]["viewport"]["width"]
    picture = session.send("Page.captureScreenshot", {
        "format": "jpeg", "quality": JPEG_QUALITY, "captureBeyondViewport": True,
        "clip": {"x": 0, "y": top, "width": width, "height": height, "scale": scale},
    })
    path.write_bytes(base64.b64decode(picture["data"]))
    pixels = DEVICES[device]["device_scale_factor"] * scale
    return {"file": path.name, "device": device, "from": top, "to": top + height,
            "width": round(width * pixels), "height": round(height * pixels)}


def capture_page(browser, url: str, index: int, device: str, folder: Path) -> dict:
    """Open one page on one device, look at it, and save its pictures."""
    result = {"url": url, "device": device, "shots": [], "facts": {}, "error": ""}
    context = browser.new_context(ignore_https_errors=True, locale="es-ES", **DEVICES[device])
    try:
        page = context.new_page()
        console_errors, failed_requests = [], []
        page.on("console", lambda message: console_errors.append(message.text[:200]) if message.type == "error" else None)
        page.on("response", lambda response: failed_requests.append(f"{response.status} {response.url[:160]}")
                if response.status >= 400 else None)
        page.goto(url, wait_until="load", timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=4_000)
        except Exception:
            pass  # some pages never go quiet (chats, video): what has loaded is enough
        page.evaluate(SCROLL_THROUGH)
        facts = page.evaluate(PAGE_FACTS)
        facts["console_errors"] = console_errors[:5]
        facts["failed_requests"] = failed_requests[:8]
        result["facts"] = facts

        session = context.new_cdp_session(page)
        screen = DEVICES[device]["viewport"]["height"]
        if index == 0:  # the homepage also gets its first screen in full detail
            result["shots"].append(shoot(session, folder / f"p{index}-{device}-top.jpg", device, 0, screen, 1) | {"part": "top"})
        stretches = HOME_STRETCHES if index == 0 else 1
        for number in range(stretches):
            top = number * STRETCH[device]
            height = min(STRETCH[device], facts["page_height"] - top)
            if height < 200 and number > 0:
                break
            if number == 0:
                height = max(height, screen)  # a very short page still fills one screen
            shot = shoot(session, folder / f"p{index}-{device}-{number + 1}.jpg", device, top, height, 0.5)
            result["shots"].append(shot | {"part": str(number + 1)})
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        context.close()
    return result


def main(arguments: list[str]) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"ok": False, "error": "missing_playwright"}))
        return 3

    with sync_playwright() as playwright:
        try:
            browser, name = launch(playwright)
        except RuntimeError as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        try:
            if arguments == ["--check"]:
                print(json.dumps({"ok": True, "browser": name}))
                return 0
            folder = Path(arguments[0])
            folder.mkdir(parents=True, exist_ok=True)
            report = {"ok": True, "browser": name, "pages": []}
            for index, url in enumerate(arguments[1:]):
                for device in DEVICES:
                    report["pages"].append(capture_page(browser, url, index, device, folder))
                    # Saved after every page: if the time runs out, what is done is not lost.
                    (folder / "capture.json").write_text(json.dumps(report), encoding="utf-8")
            print(json.dumps({"ok": True, "browser": name, "pages": len(report["pages"])}))
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2 or (sys.argv[1] != "--check" and len(sys.argv) < 3):
        print("Usage: python capture.py FOLDER URL [URL ...]   or   python capture.py --check")
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
