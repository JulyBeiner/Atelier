"""The quick X-ray of a clinic: where it loses patients, in the order a patient walks. Free: no API.

The three steps of the patient's path:
1. found,  "Me encuentran": how patients find the clinic (Google, reviews, Instagram, ads).
2. chosen, "Me eligen": its website, where they decide.
3. booked, "Me escriben y reservan": how easy it is to write or to book (WhatsApp, online booking, a chat).

Every fact comes from something checked: the clinic's own website, Google PageSpeed (free, with the key in
.env) or what I wrote on the clinic's card. What cannot be seen from outside (how fast they answer, whether
they send reminders) is never guessed: it becomes a question for the diagnosis call.

The biggest hole is the one with the most weight: the one that loses the most patients. It is the topic of
the proposal and the prototype I send the clinic first.

Two halves:
- measure() opens things: the clinic's website and Google's speed test. It is slow (half a minute).
- build() only applies the rules below to what was measured. It is instant, so it runs each time a clinic
  is shown: a fact I add by hand (the Google rating, say) counts at once, and a change to the rules
  reaches every clinic without measuring again.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from dotenv import load_dotenv

import clinic_web
import pagespeed
from inspiration import robots_allow, tidy_url

# The lines the rules draw, in one place so they are easy to move.
SPEED_GOOD, SPEED_POOR = 90, 50   # Google's own colors for the speed score: green from 90, red under 50
# Seconds to show the main content, in Google's test (a phone on a slow connection): good up to 2.5,
# poor from 4. Most websites go over 4 in this test, so on its own it is a fact, not a hole.
MAIN_CONTENT_GOOD, MAIN_CONTENT_POOR = 2.5, 4.0
SEO_LOW = 80                      # Google's basic SEO score, from 0 to 100
RATING_LOW, RATING_GOOD = 4.3, 4.6
REVIEWS_FEW, REVIEWS_GOOD = 20, 60
SLOW_SERVER = 2.0                 # seconds for the homepage to arrive

# The patient's path, in order: the key, the name on screen and what the step looks at.
STEPS = {
    "found": ("Me encuentran", "Google, reseñas, Instagram y anuncios"),
    "chosen": ("Me eligen", "Su web, donde deciden"),
    "booked": ("Me escriben y reservan", "WhatsApp, reserva online y chat"),
}
# How a step is doing, from the least to the most worrying.
LEVELS = ("unknown", "good", "improve", "hole")

# What cannot be seen from outside. Each one is a question for the diagnosis call.
UNSEEN = [
    "¿Cuánto tardan en contestar un WhatsApp o un formulario?",
    "¿Vuelven a escribir a quien pregunta y no reserva?",
    "¿Confirman la cita y mandan un recordatorio?",
    "¿Piden una reseña en Google después del tratamiento?",
]

NETWORK_NAMES = {"tiktok": "TikTok", "facebook": "Facebook", "linkedin": "LinkedIn", "x": "X", "threads": "Threads"}
# Ways of reading a website that mean it opened.
OPENED = ("phone", "address", "unconfirmed")
REAL_VISITORS = {"FAST": (True, "rápida"), "AVERAGE": (False, "regular"), "SLOW": (False, "lenta")}


# ---------- Words ----------

def number(value: float) -> str:
    """4.65 becomes "4,7": one decimal, with a comma, as we write in Spanish."""
    return f"{value:.1f}".replace(".", ",")


def joined(names: list[str]) -> str:
    """["WhatsApp", "chat", "reserva"] becomes "WhatsApp, chat y reserva"."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


# ---------- Measuring (slow: it opens the website and asks Google) ----------

def measure(clinic: dict) -> tuple[dict | None, dict]:
    """Open what the X-ray needs. It never raises.

    Returns what reading the website found (as clinic_web.look gives it, or None when nothing was opened)
    and the X-ray's own scan: when it was taken, the address measured and Google's speed test on a phone.
    """
    load_dotenv(override=True)  # a PageSpeed key added to .env counts without restarting the panel
    scan = {"at": datetime.now().isoformat(timespec="seconds"), "url": "", "mobile": {}}
    website = tidy_url(clinic.get("website") or "")
    if not website and clinic.get("website_check") == "not_found":
        return None, scan  # I already looked for its website and did not find it: nothing to open

    if website and robots_allow(website):
        # Reading the website and Google's test take about as long: both at the same time.
        with ThreadPoolExecutor(max_workers=2) as pool:
            reading = pool.submit(clinic_web.look, clinic)
            timing = pool.submit(pagespeed.measure, website, "mobile")
            found, mobile = reading.result(), timing.result()
        scan.update(url=website, mobile=mobile)
    else:
        found = clinic_web.look(clinic)  # with no website, it looks for one by the clinic's name
        site = found["website"] or website
        if site and found["check"] in OPENED:
            scan.update(url=site, mobile=pagespeed.measure(site, "mobile"))
    return found, scan


# ---------- The rules (instant: they only look at what was measured) ----------

class Xray:
    """Collects the facts of each step and the findings: the things that lose patients."""

    def __init__(self):
        self.facts = {step: [] for step in STEPS}
        self.findings = []
        self.seen = set()  # the steps there was something to look at

    def fact(self, step: str, ok: bool | None, text: str) -> None:
        """ok: True is good, False is a problem, None is neither (or not known)."""
        self.facts[step].append({"ok": ok, "text": text})
        if ok is not None:
            self.seen.add(step)

    def finding(self, step: str, level: str, weight: int, key: str, label: str, say: str, sure: bool = True) -> None:
        """A problem. weight says how many patients it loses: the heaviest one is the biggest hole.

        label is a few words, for the list of clinics. say is one plain sentence, for the clinic's card.
        sure is False when the problem is only a doubt I have to check myself.
        """
        self.findings.append({"step": step, "level": level, "weight": weight, "key": key,
                              "label": label, "say": say, "sure": sure})


def look_found(x: Xray, clinic: dict, signals: dict, mobile: dict) -> None:
    """Step 1, "Me encuentran": Instagram and the other networks, Google reviews, ads and measuring."""
    step = "found"
    if clinic.get("instagram"):
        x.fact(step, True, "Tiene Instagram.")
    elif signals:
        x.fact(step, False, "Su web no enlaza a ningún Instagram.")
        x.finding(step, "improve", 45, "no_instagram", "Sin Instagram a la vista",
                  "Su web no enlaza a ningún Instagram: quien la visita no llega a ver sus redes.")
    else:
        x.fact(step, None, "No tengo su Instagram.")
    others = [name for field, name in NETWORK_NAMES.items() if clinic.get(field)]
    if others:
        x.fact(step, True, "También está en " + joined(others) + ".")

    # The Google rating comes from the card: Google Maps does not let a program read it.
    rating, reviews = clinic.get("google_rating"), clinic.get("google_reviews")
    if rating is None or reviews is None:
        x.fact(step, None, "Falta su nota en Google: mírala en Google Maps y apúntala en la ficha, con el número de reseñas.")
    else:
        problems = len(x.findings)
        if rating < RATING_LOW:
            x.finding(step, "hole", 75, "low_rating", "Nota baja en Google", f"En Google tiene un {number(rating)} de nota.")
        elif rating < RATING_GOOD:
            x.finding(step, "improve", 30, "fair_rating", "Nota mejorable en Google", f"En Google tiene un {number(rating)} de nota.")
        if reviews < REVIEWS_FEW:
            x.finding(step, "hole", 70, "few_reviews", "Pocas reseñas en Google",
                      f"Solo tiene {reviews} reseñas en Google." if reviews else "No tiene ninguna reseña en Google.")
        elif reviews < REVIEWS_GOOD:
            x.finding(step, "improve", 28, "some_reviews", "Reseñas mejorables en Google", f"Tiene {reviews} reseñas en Google.")
        x.fact(step, len(x.findings) == problems, f"En Google: {number(rating)} de nota con {reviews} reseñas.")

    if not signals:
        return  # the rest is read on its website
    trackers = signals.get("trackers", [])
    if trackers:
        x.fact(step, True, "Mide sus visitas (" + joined(trackers) + ").")
    else:
        x.fact(step, False, "En su web no se ve nada que mida las visitas.")
        x.finding(step, "hole", 60, "no_tracking", "No mide sus visitas",
                  "En su web no se ve nada que mida las visitas: no puede saber qué le trae pacientes.")
    if "Google Ads" in trackers:
        x.fact(step, True, "Se ve la etiqueta de Google Ads: anuncia o ha anunciado en Google.")
    if "Meta Pixel" in trackers:
        x.fact(step, True, "Se ve el píxel de Meta: puede medir sus anuncios de Instagram y Facebook.")
    elif "Google Analytics / Tag Manager" in trackers:
        x.fact(step, None, "No se ve el píxel de Meta, pero puede ir dentro de Tag Manager: no se puede decir que no lo tenga.")

    missing = [part for part in ("title", "description") if not signals.get(part)]
    if missing:
        what = {"title": "título", "description": "descripción"}
        say = "Su web no tiene " + " ni ".join(what[part] for part in missing) + " para Google: en los resultados sale lo que Google elige."
        x.fact(step, False, say)
        x.finding(step, "improve", 25, "no_description", "Sin descripción para Google", say)
    else:
        x.fact(step, True, "Tiene título y descripción para Google.")
    seo = (mobile.get("scores") or {}).get("seo") if mobile.get("ok") else None
    if seo is not None:
        x.fact(step, True if seo >= SPEED_GOOD else False if seo < SEO_LOW else None,
               f"Google le da un {seo} de 100 en SEO básico.")
        if seo < SEO_LOW:
            x.finding(step, "improve", 22, "low_seo", "SEO básico flojo", f"Google le da un {seo} de 100 en SEO básico.")


def look_chosen(x: Xray, signals: dict, mobile: dict) -> None:
    """Step 2, "Me eligen": the website. Safe, ready for phones, fast, and with patients' opinions."""
    step = "chosen"
    if not signals.get("https"):
        x.fact(step, False, "Su web no tiene candado (https).")
        x.finding(step, "hole", 90, "no_https", "Web sin candado",
                  "Su web no usa https: el navegador avisa a quien entra de que no es segura.")
    if not signals.get("mobile_ready"):
        x.fact(step, False, "Su web no está preparada para el móvil.")
        x.finding(step, "hole", 90, "not_mobile", "Web no preparada para móvil",
                  "Su web no está preparada para el móvil: en el teléfono se ve diminuta.")
    if signals.get("https") and signals.get("mobile_ready"):
        x.fact(step, True, "Tiene candado (https) y está preparada para el móvil.")

    if mobile.get("ok"):
        score = mobile["scores"]["speed"]
        milliseconds = (mobile.get("numbers") or {}).get("main_content")
        seconds = milliseconds / 1000 if isinstance(milliseconds, (int, float)) else None
        x.fact(step, score >= SPEED_GOOD, f"Google le da un {score} de 100 en velocidad en el móvil.")
        if seconds:
            x.fact(step, True if seconds <= MAIN_CONTENT_GOOD else False if seconds > MAIN_CONTENT_POOR else None,
                   f"En la prueba de Google (un móvil con conexión lenta) tarda {number(seconds)} s en enseñar lo principal.")
        # Real visitors: Google's numbers from people who opened the website in Chrome, when it has enough.
        ok, word = REAL_VISITORS.get(mobile.get("real_visitors"), (None, ""))
        if word:
            x.fact(step, ok, f"Con visitas reales, Google la ve {word}.")
        say = f"En el móvil Google le da un {score} de 100 en velocidad"
        if seconds:
            say += f": en su prueba tarda {number(seconds)} s en enseñar lo principal"
        if word == "lenta":
            say += ". Con visitas reales también la ve lenta"
        # A hole when Google colors it red, or when real visitors find it slow. Otherwise, something to improve.
        if score < SPEED_POOR or word == "lenta":
            x.finding(step, "hole", 80, "slow_mobile", "Muy lenta en el móvil", say + ".")
        elif score < SPEED_GOOD:
            x.finding(step, "improve", 40, "fair_mobile", "Algo lenta en el móvil", say + ".")
    elif mobile:
        x.fact(step, None, "Google no ha podido medir su velocidad. " + mobile.get("error", ""))

    seconds = signals.get("seconds") or 0
    if seconds > SLOW_SERVER:
        x.fact(step, False, f"Su portada tardó {number(seconds)} s en llegar.")
        x.finding(step, "improve", 20, "slow_server", "Tarda en responder", f"Su portada tardó {number(seconds)} s en llegar.")

    if "reviews" in signals:  # websites read before this rule existed do not have it yet
        if signals["reviews"]:
            x.fact(step, True, "En su web se ven opiniones de pacientes.")
        else:
            x.fact(step, False, "En lo que he leído de su web no aparecen opiniones de pacientes.")
            x.finding(step, "improve", 20, "no_reviews_on_site", "Sin opiniones en su web",
                      "En lo que he leído de su web no aparecen opiniones de pacientes.")
    if signals.get("technologies"):
        x.fact(step, None, "Hecha con " + joined(signals["technologies"]) + ".")


def look_booked(x: Xray, signals: dict) -> None:
    """Step 3, "Me escriben y reservan": can a patient write or book without calling?"""
    step = "booked"
    chats = signals.get("chats", [])
    whatsapp = bool(signals.get("whatsapp")) or "WhatsApp button" in chats
    chats = [chat for chat in chats if chat != "WhatsApp button"]
    booking = signals.get("booking", [])
    phone_link, forms = signals.get("phone_link"), signals.get("forms", 0)

    x.fact(step, whatsapp, "Se le puede escribir por WhatsApp desde su web." if whatsapp else "Su web no tiene WhatsApp.")
    x.fact(step, bool(booking), f"Se puede reservar online ({joined(booking)})." if booking else "No se ve reserva online.")
    if chats:
        x.fact(step, True, f"Tiene un chat ({joined(chats)}).")
    x.fact(step, bool(phone_link), "Su teléfono se puede tocar para llamar." if phone_link
           else "Su teléfono no se puede tocar para llamar desde el móvil.")
    if "asks_to_book" in signals:  # websites read before this rule existed do not have it yet
        x.fact(step, signals["asks_to_book"], "En su portada se invita a pedir cita." if signals["asks_to_book"]
               else "En su portada no veo ninguna invitación a pedir cita («Pide cita», «Reserva»…).")
    x.fact(step, None, f"Tiene {forms} formulario{'s' if forms != 1 else ''}." if forms else "No tiene formularios.")

    if not whatsapp and not booking and not chats:
        ways = ["llamar"] + (["rellenar un formulario"] if forms else ["escribir un correo"] if signals.get("mail_link") else [])
        x.finding(step, "hole", 85, "no_quick_way", "Sin WhatsApp ni reserva online",
                  "Para pedir cita solo pueden " + " o ".join(ways) + ": no hay WhatsApp, ni chat, ni reserva online.")
    elif not booking:
        x.finding(step, "improve", 35, "no_booking", "Pueden escribir, no reservar",
                  "Pueden escribirle" + (" por WhatsApp" if whatsapp else "") +
                  ", pero no reservar online: cada cita depende de que alguien conteste rápido.")
    if not phone_link:
        x.finding(step, "improve", 25 if whatsapp else 50, "phone_not_tappable", "Teléfono que no se toca",
                  "En el móvil su teléfono no se puede tocar para llamar.")
    if signals.get("asks_to_book") is False:
        x.finding(step, "improve", 30, "no_book_button", "Sin invitación a pedir cita",
                  "En su portada no hay ninguna invitación a pedir cita.")


def look_without_website(x: Xray, clinic: dict) -> None:
    """When the website could not be read, steps 2 and 3 have nothing to look at. Say why, and what to do."""
    check = clinic.get("website_check")
    if not clinic.get("website") and check == "not_found":
        x.finding("chosen", "unknown", 100, "no_web", "Sin web encontrada",
                  "No he encontrado su web. Mira en Google Maps si tiene una y pégala en la ficha:"
                  " si de verdad no tiene, ese es su agujero más grande.", sure=False)
    elif check == "failed":
        x.finding("chosen", "unknown", 100, "web_down", "Su web no abrió",
                  "Su web no abrió cuando la miré. Ábrela tú: si tampoco abre, ese es su agujero más grande.", sure=False)
    elif check == "blocked":
        x.finding("chosen", "unknown", 100, "web_blocked", "Web sin leer",
                  "Su web pide a los programas que no entren, así que no la he leído. Mírala tú.", sure=False)
    for step in ("chosen", "booked"):
        x.fact(step, None, "Sin leer su web no puedo ver esta parte.")


def step_level(x: Xray, step: str) -> str:
    """The most worrying level among the step's findings. With no findings: good, if there was something to see."""
    levels = [finding["level"] for finding in x.findings if finding["step"] == step]
    if not levels:
        return "good" if step in x.seen else "unknown"
    return max(levels, key=LEVELS.index)


def build(clinic: dict) -> dict | None:
    """The X-ray of a clinic, from what was measured and what is on its card. None if it was never taken."""
    scan = clinic.get("xray_scan") or {}
    if not scan.get("at"):
        return None
    web = clinic.get("web_scan") or {}
    # What was read on its website counts only if the website opened.
    signals = (web.get("signals") or {}) if clinic.get("website_check") in OPENED else {}
    mobile = scan.get("mobile") or {}

    x = Xray()
    look_found(x, clinic, signals, mobile)
    if signals:
        look_chosen(x, signals, mobile)
        look_booked(x, signals)
    else:
        look_without_website(x, clinic)

    order = list(STEPS)
    findings = sorted(x.findings, key=lambda finding: (-finding["weight"], order.index(finding["step"])))
    speed = (mobile.get("scores") or {}).get("speed") if mobile.get("ok") else None
    return {
        "at": scan["at"],
        "web_at": web.get("at", "") if signals else "",
        "speed": speed,
        "hole": findings[0] if findings else None,
        "findings": findings,
        "steps": [{"key": step, "title": title, "about": about, "level": step_level(x, step), "facts": x.facts[step]}
                  for step, (title, about) in STEPS.items()],
        "unseen": UNSEEN,
        "warning": "No he podido comprobar que esa web sea la suya: en ella no salen su teléfono ni su calle."
                   if clinic.get("website_check") == "unconfirmed" else "",
    }
