"""Takes screenshots of a website on desktop and on mobile, the way a visitor sees it.
On the phone it also taps the menu button of the homepage, to check that the menu really opens.

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
# Picture pixels per page pixel. The first screen is saved sharp, to read the typography.
# The long stretches are saved smaller: enough to judge layout, photos, colors and spacing,
# and cheap to send to the model.
DETAIL = {"desktop": 1, "mobile": 2}
OVERVIEW = {"desktop": 0.5, "mobile": 1}
# How many page pixels go into each stretch.
STRETCH = {"desktop": 2800, "mobile": 1560}
HOME_STRETCHES = 3
JPEG_QUALITY = 72

# Walk down the whole page like a visitor, so everything that loads "when you get there" is loaded.
# Then wait for the images and go back up.
WALK_THE_PAGE = """async () => {
  const pause = (ms) => new Promise((done) => setTimeout(done, ms));
  for (const image of document.images) image.loading = 'eager';
  const step = Math.round(window.innerHeight * 0.6);
  for (let y = 0; y < Math.min(document.documentElement.scrollHeight, 16000); y += step) {
    window.scrollTo({ top: y, behavior: 'instant' });
    await pause(140);
  }
  const loading = [...document.images].filter((image) => !image.complete).map((image) => new Promise((done) => {
    image.addEventListener('load', done);
    image.addEventListener('error', done);
  }));
  await Promise.race([Promise.all(loading), pause(6000)]);
  window.scrollTo({ top: 0, behavior: 'instant' });
  await pause(900);
}"""

SCROLL_TO = "(top) => window.scrollTo({ top, behavior: 'instant' })"

# Things pinned to the screen (a menu bar, a chat button) would repeat in every screen of a long picture.
# After the first screen they are hidden. Big pinned blocks are left alone: they may hold the page itself.
HIDE_PINNED = """() => {
  const limit = window.innerHeight * 0.35;
  for (const element of document.querySelectorAll('body *')) {
    if (getComputedStyle(element).position === 'fixed' && element.getBoundingClientRect().height < limit) {
      element.style.setProperty('visibility', 'hidden', 'important');
    }
  }
}"""

# What a visitor can really see and touch on the screen right now. The scripts below start with this.
# Something only counts when it has a size, is not transparent, and nothing is lying on top of it.
ON_SCREEN = r"""
  const onScreen = (el) => {
    const box = el.getClientRects()[0];
    if (!box || box.width < 2 || box.height < 2) return null;
    const x = box.left + box.width / 2, y = box.top + box.height / 2;
    if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) return null;
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return null;
    const front = document.elementFromPoint(x, y);
    return { x: Math.round(x), y: Math.round(y), width: box.width, height: box.height,
             covered: !(front && (front === el || el.contains(front))) };
  };
  const words = (el) => (el.innerText || '').replace(/\s+/g, ' ').trim();
  const linksOnScreen = () => [...document.querySelectorAll('a[href]')].filter((link) => {
    const spot = onScreen(link);
    return spot && !spot.covered && !/^(tel:|mailto:|javascript:)|wa\.me\/|whatsapp\.com/i.test(link.href);
  });
  const MENU_AREA = 'header, nav, [role="navigation"], [role="banner"], [class*="nav" i], [class*="menu" i],'
                  + ' [class*="header" i], [id*="nav" i], [id*="menu" i], [id*="header" i]';
  // Menu links: inside a header or menu, with words, and leading somewhere else (the logo leads to this same page).
  const here = location.href.split('#')[0];
  const menuLinks = () => [...new Set(linksOnScreen()
    .filter((link) => link.closest(MENU_AREA) && (link.hash || link.href.split('#')[0] !== here))
    .map(words).filter((text) => text.length >= 3))].slice(0, 12);
"""

# Facts you cannot get from a picture: real sizes, font names, and whether the page spills sideways.
PAGE_FACTS = "() => {" + ON_SCREEN + """
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
    menu_links: menuLinks(),
  };
}"""

# A cookie notice can lie on top of the menu button. Say yes to it, as most visitors do.
ACCEPT_COOKIES = r"""() => {
  const yes = /^(aceptar|acepto|permitir|entendido|de acuerdo|vale|ok|accept|allow|agree|i agree|got it)\b/i;
  for (const el of document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]')) {
    const text = (el.innerText || el.value || '').trim();
    if (!text || text.length > 40 || !yes.test(text) || (el.checkVisibility && !el.checkVisibility())) continue;
    for (let box = el.parentElement, up = 0; box && up < 6; box = box.parentElement, up++) {
      const around = box.innerText || '';
      if (around.length < 1500 && /cookie/i.test(around + ' ' + box.id + ' ' + box.getAttribute('class'))) {
        el.click();
        return true;
      }
    }
  }
  return false;
}"""

# On a phone the menu usually hides behind a button. This lists the menu links already in sight
# and the things that could be that button: first the ones whose name says so ("menu", "toggle", three bars…),
# then small drawings with no words near the top. It also remembers every link in sight, to tell later which are new.
MENU_BUTTONS = "() => {" + ON_SCREEN + r"""
  const HINT = /men[uú]|naveg|navigation|hamburg|burger|toggle|offcanvas|drawer|bars/i;
  const OTHER_JOB = new RegExp('lang|idioma|search|busca|carrito|cesta|cookie|consent|privac|login|cuenta|account|chat'
                               + '|whatsapp|compartir|carousel|swiper|slick|\\b(cart|share|prev|next|play|pause|mute)\\b', 'i');
  window.__atelierLinks = new Set(linksOnScreen());
  window.__atelierScroll = window.scrollY;
  const buttons = [];
  const consider = (el) => {
    const spot = onScreen(el);
    if (!spot || spot.width > 240 || spot.height > 140) return;            // a button, not a whole block
    if (buttons.some((other) => Math.abs(other.x - spot.x) < 12 && Math.abs(other.y - spot.y) < 12)) return;
    const href = el.tagName === 'A' ? (el.getAttribute('href') || '').trim() : '';
    if (href && !/^(#|javascript:)/i.test(href)) return;                  // a real link: it goes to another page
    const text = words(el);
    const name = (text.length > 2 && text) || el.getAttribute('aria-label') || el.title || text;
    const about = [el.id, el.getAttribute('class'), el.getAttribute('aria-controls'), el.getAttribute('data-target'),
                   el.getAttribute('data-bs-target'), el.getAttribute('for'), href, name].join(' ');
    if (OTHER_JOB.test(about)) return;
    const named = HINT.test(about) || HINT.test(el.innerHTML.slice(0, 400)) || (el.hasAttribute('aria-expanded') && spot.y < 170);
    if (href.length > 2 && !named) return;                                 // a link to a section of this same page
    const drawing = text.length <= 2 && spot.width <= 96 && spot.height <= 96 && spot.y < 170;
    if (!named && !drawing) return;
    buttons.push({ x: spot.x, y: spot.y, named, covered: spot.covered, label: name.replace(/\s+/g, ' ').slice(0, 40) });
  };
  document.querySelectorAll('button, summary, [role="button"], label[for], a, [onclick], [aria-expanded], [aria-controls]')
          .forEach(consider);
  // Things with no button tag that still invite a tap: the cursor turns into a hand over them.
  for (const el of [...document.querySelectorAll('body *')].slice(0, 4000)) {
    if (getComputedStyle(el).cursor === 'pointer' && getComputedStyle(el.parentElement).cursor !== 'pointer'
        && !el.closest('a[href], button')) consider(el);
  }
  const order = (button) => (button.named ? (button.covered ? 1 : 0) : 2);
  buttons.sort((a, b) => order(a) - order(b) || a.y - b.y);
  return { links: menuLinks(), buttons };
}"""

# After a tap: the links in sight that were not there before. null when the tap took us to another page.
# If the page moved up or down instead, the new links are just the next stretch of the page, not a menu.
NEW_LINKS = "() => {" + ON_SCREEN + r"""
  const before = window.__atelierLinks;
  if (!before) return null;
  if (Math.abs(window.scrollY - window.__atelierScroll) > 4) return [];
  return [...new Set(linksOnScreen().filter((link) => !before.has(link)).map(words).filter((text) => text.length >= 2))];
}"""
MENU_TRIES = 4


def launch(playwright):
    """Playwright's own Chromium if it was installed; otherwise the Edge or Chrome already on this computer."""
    for channel in (None, "msedge", "chrome"):
        try:
            browser = playwright.chromium.launch(channel=channel) if channel else playwright.chromium.launch()
            return browser, channel or "chromium"
        except Exception:
            continue
    raise RuntimeError("No encontré ningún navegador. Instala Edge o Chrome, o ejecuta: playwright install chromium")


def photograph(session, device: str, top: int, height: int, scale: float, kind: str) -> bytes:
    """One rectangle of the page as picture bytes. top and height are in page pixels."""
    options = {"format": kind, "captureBeyondViewport": True,
               "clip": {"x": 0, "y": top, "width": DEVICES[device]["viewport"]["width"], "height": height, "scale": scale}}
    if kind == "jpeg":
        options["quality"] = JPEG_QUALITY
    return base64.b64decode(session.send("Page.captureScreenshot", options)["data"])


def shot_facts(path: Path, device: str, top: int, height: int, scale: float, part: str) -> dict:
    return {"file": path.name, "device": device, "part": part, "from": top, "to": top + height,
            "width": round(DEVICES[device]["viewport"]["width"] * scale), "height": round(height * scale)}


def shoot_stretch(page, session, joiner, path: Path, device: str, top: int, height: int) -> None:
    """One long picture of a stretch of the page, taken screen by screen as a visitor scrolls.

    Many pages only show a block while it is on screen, so a single tall photo would come out with holes.
    Each screen is photographed where a visitor would see it; a blank helper page then stacks them into one.
    """
    screen = DEVICES[device]["viewport"]["height"]
    pieces, position = [], top
    while position < top + height:
        piece = min(screen, top + height - position)
        page.evaluate(SCROLL_TO, position)
        if position > 0:
            page.evaluate(HIDE_PINNED)
        page.wait_for_timeout(650)  # blocks that fade in when they reach the screen
        picture = photograph(session, device, position, piece, OVERVIEW[device], "png")
        pieces.append(base64.b64encode(picture).decode("ascii"))
        position += piece
    width = round(DEVICES[device]["viewport"]["width"] * OVERVIEW[device])
    joiner.set_viewport_size({"width": width, "height": 50})  # low, so the long picture ends where the page does
    joiner.set_content(
        "<body style='margin:0;background:#fff'>" + "".join(
            f"<img style='display:block;width:{width}px' src='data:image/png;base64,{piece}'>" for piece in pieces),
        wait_until="load",
    )
    joiner.screenshot(path=str(path), type="jpeg", quality=JPEG_QUALITY, full_page=True)


def look_for_menu(page, url: str) -> dict:
    """On a phone: is the menu in sight, or is there a button that opens it? Taps the likely buttons to find out.

    "links" are the menu links in sight before touching anything. "button" is the one that opened the menu
    and "opened" the links that appeared. "tried" are the buttons that were tapped and showed nothing.
    """
    found = {"links": [], "button": "", "opened": [], "tried": [], "error": ""}

    def fresh_page() -> dict:
        page.goto(url, wait_until="load", timeout=20_000)
        page.wait_for_timeout(1_500)
        if page.evaluate(ACCEPT_COOKIES):
            page.wait_for_timeout(600)
        state = page.evaluate(MENU_BUTTONS)
        if any(button["named"] and button["covered"] for button in state["buttons"]):
            page.wait_for_timeout(2_500)  # an opening animation may still be on top of the page
            state = page.evaluate(MENU_BUTTONS)
        return state

    try:
        state = fresh_page()
        found["links"] = state["links"]
        for number in range(min(len(state["buttons"]), MENU_TRIES)):
            if number:
                state = fresh_page()  # the last tap may have left something open
                if number >= len(state["buttons"]):
                    break
            button, here = state["buttons"][number], page.url.split("#")[0]
            page.touchscreen.tap(button["x"], button["y"])
            appeared = []
            for _ in range(4):  # menus slide or fade in: look a few times
                page.wait_for_timeout(600)
                appeared = page.evaluate(NEW_LINKS) if page.url.split("#")[0] == here else None
                if appeared is None or len(appeared) >= 2:
                    break
            if appeared and len(appeared) >= 2:
                found["button"], found["opened"] = button["label"], appeared[:12]
                break
            found["tried"].append({"label": button["label"], "named": button["named"]})
    except Exception as error:
        found["error"] = f"{type(error).__name__}: {error}"[:200]
    return found


def capture_page(browser, joiner, url: str, index: int, device: str, folder: Path) -> dict:
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
        page.wait_for_timeout(1_200)  # let the opening animation finish

        session = context.new_cdp_session(page)
        screen = DEVICES[device]["viewport"]["height"]
        if index == 0:
            # The homepage's first screen in full detail, exactly as it greets a visitor: before any scrolling.
            path = folder / f"p{index}-{device}-top.jpg"
            path.write_bytes(photograph(session, device, 0, screen, DETAIL[device], "jpeg"))
            result["shots"].append(shot_facts(path, device, 0, screen, DETAIL[device], "top"))

        page.evaluate(WALK_THE_PAGE)
        facts = page.evaluate(PAGE_FACTS)
        facts["console_errors"] = console_errors[:5]
        facts["failed_requests"] = failed_requests[:8]
        result["facts"] = facts

        for number in range(HOME_STRETCHES if index == 0 else 1):
            top = number * STRETCH[device]
            height = min(STRETCH[device], facts["page_height"] - top)
            if number == 0:
                height = max(height, screen)  # a very short page still fills one screen
            elif height < 200:
                break
            path = folder / f"p{index}-{device}-{number + 1}.jpg"
            shoot_stretch(page, session, joiner, path, device, top, height)
            result["shots"].append(shot_facts(path, device, top, height, OVERVIEW[device], str(number + 1)))

        if index == 0 and device == "mobile":
            # Last, because it taps things. The pictures are already saved.
            facts["menu"] = look_for_menu(page, url)
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
            joiner = browser.new_context(device_scale_factor=1).new_page()  # the blank page that stacks the screens
            report = {"ok": True, "browser": name, "pages": []}
            for index, url in enumerate(arguments[1:]):
                for device in DEVICES:
                    report["pages"].append(capture_page(browser, joiner, url, index, device, folder))
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
