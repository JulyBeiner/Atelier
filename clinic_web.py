"""Finds and reads a clinic's own website. Free: it only opens the website, like a visitor would.

Two jobs for the CRM:
- when I do not know a clinic's website, it tries the addresses the clinic's name suggests and keeps one
  only when the page shows the clinic's phone or its street. Checked, not guessed;
- from the website it collects the clinic's social profiles and the first facts for the quick X-ray
  (is there a way to book, a WhatsApp button, a chat, something that measures visits).

It respects robots.txt: a website that asks programs to stay out is not opened.
"""

import re
import socket
import time
import unicodedata
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlsplit

import httpx

import site_reader
from inspiration import robots_allow, tidy_url

TIMEOUT = 10        # seconds for one page
EXTRA_PAGES = 2     # pages that look like "contact", read after the homepage
GUESS_LIMIT = 14    # addresses tried for one clinic
TIME_LIMIT = 40     # seconds spent looking for one clinic's website, at most
ENDINGS = (".com", ".es")

FILLER = {"de", "del", "la", "el", "los", "las", "y", "en", "and", "the"}
TITLES = {"dr", "dra", "doctor", "doctora"}
GENERIC = {"clinica", "clinicas", "clinic", "clinics", "centro", "instituto", "medicina", "estetica",
           "estetico", "medica", "medico", "aesthetic", "beauty"}
CONTACT_PAGE = re.compile(r"contact|donde|ubicaci|localiza|cita|reserva|visit")

# The networks the CRM has a field for. For each: the hosts it lives on, and the first words of a path
# that are not a profile (a post, a share button...).
NETWORKS = {
    "instagram": (("instagram.com",), {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "share", "direct"}),
    "tiktok": (("tiktok.com",), {"share", "tag", "music", "discover", "embed"}),
    "x": (("x.com", "twitter.com"), {"intent", "share", "home", "hashtag", "search", "i", "sharer"}),
    "threads": (("threads.net", "threads.com"), {"intent", "share"}),
    "facebook": (("facebook.com", "fb.com"), {"sharer", "sharer.php", "share", "share.php", "dialog", "tr",
                                                "plugins", "login", "policies", "help", "privacy", "hashtag"}),
    "linkedin": (("linkedin.com",), set()),
}
# On LinkedIn a profile is a company, a person, a school or a product page. Anything else is a share button.
LINKEDIN_KINDS = ("company", "in", "school", "showcase")
# Other places worth knowing about, kept with the reading for the X-ray.
OTHER = {"youtube": ("youtube.com",)}
BOOKING = {
    "Doctoralia": ("doctoralia.",), "Top Doctors": ("topdoctors.",), "Treatwell": ("treatwell.",),
    "Booksy": ("booksy.com",), "Calendly": ("calendly.com",), "Koibox": ("koibox",), "Flowww": ("flowww",),
    "Clinic Cloud": ("clinic-cloud", "cliniccloud"), "Timify": ("timify.com",), "SimplyBook": ("simplybook.",),
    "Acuity": ("acuityscheduling.com",), "Setmore": ("setmore.com",), "Fresha": ("fresha.com",),
    "Bookitit": ("bookitit",), "Reservio": ("reservio.com",), "AgendaPro": ("agendapro.com",),
}
CHATS = {
    "Tidio": ("tidio.co",), "Crisp": ("crisp.chat",), "Intercom": ("widget.intercom.io", "intercomsettings"),
    "tawk.to": ("tawk.to",), "Zendesk": ("zdassets.com", "zopim"), "HubSpot": ("hs-scripts.com",),
    "LiveChat": ("livechatinc.com",), "Smartsupp": ("smartsupp.com", "smartsuppchat.com"),
    "ManyChat": ("manychat.com",), "JivoChat": ("jivosite", "jivochat"),
    "WhatsApp button": ("joinchat", "click-to-chat", "getbutton.io", "wa-chat", "whatsapp-button"),
}
# How a homepage invites a patient to book: "Pide cita", "Reserva tu consulta", "Cita previa"...
# Read on the text without accents, so "valoración" is "valoracion".
BOOKING_WORDS = re.compile(
    r"\b(?:pide|pedir|solicita|solicitar|reserva|reservar|agenda|agendar|concierta|concertar)"
    r"(?:\s+(?:tu|una|su|ya|ahora))?\s+(?:cita|consulta|valoracion|primera|diagnostico)"
    r"|\bcita\s+previa|\bbook\s+(?:now|an?\s+appointment)")
# In a link's path, as a word of its own: "/pedir-cita" yes, "/felicitaciones" no.
BOOKING_PATHS = re.compile(r"(?<![a-z])(?:citas?|reserv|booking|appointment)")
# Patients' opinions on a website: a reviews widget, the schema.org rating, or the words themselves.
REVIEW_WIDGETS = {
    "Trustindex": ("trustindex",), "Elfsight": ("elfsight",), "EmbedSocial": ("embedsocial",),
    "Trustpilot": ("trustpilot",), "Google reviews plugin": ("wp-google-places-review", "grw-", "rplg", "google-reviews"),
}
REVIEW_WORDS = re.compile(r"\bresenas?\b|\bopiniones\b|\btestimonios?\b|\bvaloraciones\b|lo que dicen")


def plain(text: str) -> str:
    """Lowercase and without accents: 'Clínica Rocío' becomes 'clinica rocio'."""
    return "".join(letter for letter in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(letter) != "Mn")


# ---------- Guessing the address ----------

def labels_for(words: list[str]) -> list[str]:
    """The names a clinic called like this could have chosen for its website, most likely first."""
    core = "".join(word for word in words if word not in GENERIC | FILLER | TITLES)
    title = next((word for word in words if word in TITLES), "")
    labels = ["".join(words), "".join(word for word in words if word not in FILLER)]
    if core:
        named_clinic = "clinic" in core  # "auroraclinic" needs no "clinica" in front
        if not named_clinic:
            labels.append("clinica" + core)
        if title:
            labels += [title + core, ("doctora" if title in ("dra", "doctora") else "doctor") + core]
        labels += ["".join(words[:2]), core]
        if not named_clinic:
            labels.append(core + "clinic")
    labels.append("-".join(words))
    return labels


def guesses(name: str) -> list[str]:
    """Addresses to try for a clinic, from its name: 'Clínica Aurora' gives clinicaaurora.com, clinicaaurora.es..."""
    spellings = [plain(name).replace("&", " ")]
    if "&" in name:
        spellings.append(plain(name).replace("&", " y "))
    labels = []
    for spelling in spellings:
        # The whole name first; then each piece of a name like "Aurora Uno, Medicina Estética".
        for piece in [spelling] + re.split(r"[,(]", spelling):
            labels += labels_for(re.findall(r"[a-z0-9]+", piece))
    unique = [label for label in dict.fromkeys(labels) if 5 <= len(label) <= 40]
    return [f"https://{label}{ending}" for label in unique for ending in ENDINGS][:GUESS_LIMIT]


def exists(url: str) -> bool:
    """Whether the name of a website is registered at all. It saves waiting for addresses that go nowhere."""
    host = urlsplit(url).hostname or ""
    if re.fullmatch(r"[\d.]+", host):
        return True
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


# ---------- Reading a website ----------

def read_site(url: str) -> dict:
    """Open the homepage and up to two pages that look like "contact". It never raises.

    status is 'ok', 'blocked' (robots.txt says no) or 'failed'. Each page comes with its facts, as
    site_reader tells them, and its raw HTML in lowercase for looking up phones and tools.
    """
    site = {"status": "failed", "error": "", "final_url": url, "pages": []}
    if not site_reader.is_public(url):
        site["error"] = "No es una dirección pública."
        return site
    if not robots_allow(url):
        site["status"] = "blocked"
        return site
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": site_reader.USER_AGENT}) as client:
        result = site_reader.fetch(client, url)
        if result["status"] != 200 or not result["html"]:
            site["error"] = result["error"] or f"Respondió con el código {result['status']}."
            return site
        home = site_reader.read_page(result, 0) | {"html": result["html"].lower()}
        site.update(status="ok", final_url=home["url"], pages=[home])
        more = [link["url"] for link in home.get("links", [])
                if site_reader.site_name(link["url"]) == site_reader.site_name(home["url"])
                and link["url"] != home["url"]
                and CONTACT_PAGE.search(plain(site_reader.path_of(link["url"]) + " " + link["text"]))]
        for extra in list(dict.fromkeys(more))[:EXTRA_PAGES]:
            result = site_reader.fetch(client, extra)
            if result["status"] == 200 and result["html"]:
                site["pages"].append(site_reader.read_page(result, 1) | {"html": result["html"].lower()})
    return site


def proof(clinic: dict, pages: list[dict]) -> str:
    """How the pages show that the website belongs to this clinic: 'phone', 'address' or '' (they do not)."""
    digits = re.sub(r"\D", "", clinic.get("phone") or "")
    national = digits[-9:] if len(digits) >= 9 else ""
    if national:
        # The same nine digits, written with or without spaces, dots or the +34 in front.
        number = re.compile(r"(?<!\d)(?:(?:\+|00)?34[\s.\-() ]{0,3})?" + r"[\s.\-() ]{0,2}".join(national) + r"(?!\d)")
        if any(number.search(page["html"]) for page in pages):
            return "phone"

    # "Calle de la Aurora, 80, 5º dcha": the street is Aurora and the number is 80.
    address = plain(clinic.get("address") or "")
    street = re.sub(r"^(calle|avenida|paseo|plaza)\s+(de\s+la\s+|de\s+los\s+|del\s+|de\s+)?", "", address.split(",")[0]).strip()
    number = re.search(r",\s*(\d+)", address)
    if street and number:
        last_word = re.escape(street.split()[-1])
        there = re.compile(last_word + r"\W{0,4}(?:n[o.º°]{0,2}\s*)?" + number.group(1) + r"(?!\d)")
        if any(there.search(plain(page.get("all_text", ""))) for page in pages):
            return "address"
    return ""


def profile(url: str) -> tuple[str, str]:
    """If a link is someone's profile on a network, which network and the profile's clean address."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    steps = [step for step in parts.path.split("/") if step]
    for network, (hosts, not_profiles) in NETWORKS.items():
        if not any(host == site or host.endswith("." + site) for site in hosts) or not steps:
            continue
        first = steps[0].lower()
        if first in not_profiles or first.endswith(".php") and first != "profile.php":
            return "", ""
        if network == "facebook":
            if first == "profile.php":
                number = parse_qs(parts.query).get("id", [""])[0]
                return (network, f"https://www.facebook.com/profile.php?id={number}") if number.isdigit() else ("", "")
            keep = steps[:3] if first in ("pages", "people", "p") else steps[:1]
            return network, "https://www.facebook.com/" + "/".join(keep)
        if network == "linkedin":
            if first not in LINKEDIN_KINDS or len(steps) < 2:
                return "", ""
            return network, f"https://www.linkedin.com/{first}/{steps[1]}"
        if network in ("tiktok", "threads"):
            if not first.startswith("@"):
                return "", ""
            return network, f"https://www.{hosts[0]}/{steps[0]}"
        if network == "instagram":
            return network, f"https://www.instagram.com/{steps[0]}/"
        return network, f"https://x.com/{steps[0]}"
    return "", ""


def handle_of(address: str) -> str:
    """The name in a profile's address, letters and digits only: instagram.com/dra.ana_demo/ gives 'draanademo'.

    Empty when the profile is known only by a number, like facebook.com/profile.php?id=123.
    """
    steps = [step for step in urlsplit(address).path.split("/") if step]
    if not steps or steps[0] == "profile.php":
        return ""
    name = steps[1] if steps[0] in ("pages", "people", "p") + LINKEDIN_KINDS and len(steps) > 1 else steps[0]
    return re.sub(r"\d{6,}$", "", re.sub(r"[^a-z0-9]", "", plain(name)))


def belongs(address: str, clinic_name: str, website: str) -> bool:
    """Whether a profile looks like the clinic's own: its name shares something with the clinic's or its website's.

    Website templates often keep the profiles of whoever made the template (a theme shop on Instagram, say).
    Those share nothing with the clinic, and they are left out.
    """
    handle = handle_of(address)
    if not handle:
        return True  # only a number: nothing to compare
    words = re.findall(r"[a-z0-9]+", plain(clinic_name))
    site = site_reader.site_name(website).split(".")[0].replace("-", "")
    wholes = ["".join(words), site]
    keys = [word for word in words if len(word) >= 4 and word not in GENERIC | FILLER | TITLES]
    keys += [site] if len(site) >= 4 else []
    if any(key in handle for key in keys) or (len(handle) >= 4 and any(handle in whole for whole in wholes)):
        return True
    return any(SequenceMatcher(None, handle, whole).ratio() >= 0.6 for whole in wholes)


def profiles(pages: list[dict], clinic_name: str, website: str) -> tuple[dict, list[str]]:
    """The social profiles the pages link to: the clinic's own, and the ones that do not look like theirs.

    When a network shows up with two profiles of the clinic, the one linked most often wins.
    """
    seen = {network: Counter() for network in NETWORKS}
    unsure = []
    for page in pages:
        for link in page.get("links", []):
            network, address = profile(link["url"])
            if network and belongs(address, clinic_name, website):
                seen[network][address] += 1
            elif network and address not in unsure:
                unsure.append(address)
    return {network: counts.most_common(1)[0][0] for network, counts in seen.items() if counts}, unsure


def signals(pages: list[dict]) -> dict:
    """Facts for the quick X-ray (xray.py), from the pages that were read.

    asks_to_book: the homepage invites to book ("Pide cita"), in its text or in a link.
    reviews: how patients' opinions show up on the website (a widget, the schema.org rating, the words).
    """
    home = pages[0]
    html = " ".join(page["html"] for page in pages)
    links = [link["url"] for page in pages for link in page.get("links", [])]
    whatsapp = next((link for link in links if "wa.me/" in link or "api.whatsapp.com" in link), "")

    def union(key: str) -> list[str]:
        return sorted({item for page in pages for item in page.get(key, [])})

    home_links = home.get("links", [])
    asks_to_book = bool(BOOKING_WORDS.search(plain(home.get("all_text", "")))) or any(
        BOOKING_WORDS.search(plain(link["text"])) or BOOKING_PATHS.search(site_reader.path_of(link["url"]).lower())
        for link in home_links)
    reviews = site_reader.found_in(html, REVIEW_WIDGETS)
    if "AggregateRating" in union("schema_types"):
        reviews.append("schema.org rating")
    if any(REVIEW_WORDS.search(plain(page.get("all_text", ""))) for page in pages):
        reviews.append("words")

    return {
        "title": home.get("title", ""),
        "description": home.get("description", ""),
        "https": home["url"].startswith("https://"),
        "mobile_ready": bool(home.get("has_viewport")),
        "seconds": home.get("seconds", 0),
        "technologies": union("technologies"),
        "trackers": union("trackers"),
        "schema_types": union("schema_types"),
        "phone_link": any(page.get("contact", {}).get("tel") for page in pages),
        "mail_link": any(page.get("contact", {}).get("mail") for page in pages),
        "whatsapp": whatsapp,
        "forms": sum(len(page.get("forms", [])) for page in pages),
        "booking": site_reader.found_in(html, BOOKING),
        "chats": site_reader.found_in(html, CHATS),
        "other": {name: next((link for link in links if any(site in link for site in sites)), "")
                  for name, sites in OTHER.items()},
        "asks_to_book": asks_to_book,
        "reviews": reviews,
    }


# ---------- The whole job for one clinic ----------

def look(clinic: dict) -> dict:
    """Read the clinic's website, or look for it when the CRM has none.

    Returns the website (only when it was found here and proved), how it was checked, the clinic's profiles
    and "scan", everything that was read, to keep with the clinic. Profiles linked from the website that do
    not look like the clinic's are kept in the scan as "unsure" and never written to the clinic.
    check: 'phone' or 'address' (proved), 'unconfirmed' (opened, but nothing on it proves it is theirs),
    'blocked' (robots.txt), 'failed' (it did not open) or 'not_found' (no address worked).
    """
    scan = {"at": datetime.now().isoformat(timespec="seconds"), "tried": [], "pages": [], "error": ""}
    answer = {"website": "", "check": "", "profiles": {}, "scan": scan}
    typed = tidy_url(clinic.get("website") or "")

    if typed:
        site = read_site(typed)
        scan["error"] = site["error"]
        if site["status"] != "ok":
            answer["check"] = site["status"]
            return answer
        answer["check"] = proof(clinic, site["pages"]) or "unconfirmed"
    else:
        site, started = None, time.monotonic()
        for url in guesses(clinic.get("name") or ""):
            if time.monotonic() - started > TIME_LIMIT:
                break
            scan["tried"].append(url)
            if not exists(url):
                continue
            candidate = read_site(url)
            how = proof(clinic, candidate["pages"]) if candidate["status"] == "ok" else ""
            if how:
                site = candidate
                answer.update(website=site["final_url"], check=how)
                break
        if site is None:
            answer["check"] = "not_found"
            return answer

    pages = site["pages"]
    answer["profiles"], unsure = profiles(pages, clinic.get("name") or "", site["final_url"])
    scan.update(final_url=site["final_url"], pages=[page["url"] for page in pages], unsure=unsure, signals=signals(pages))
    return answer
