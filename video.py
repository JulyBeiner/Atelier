"""The video that carries my proposal and its prototype to a clinic. Under a minute, vertical, made by
Atelier alone. Free: no API.

It goes as an attachment in WhatsApp, Instagram or an email. Atelier lives on my computer, so a link to it
would not open on a clinic's phone; a video needs nothing to open.

Five scenes: the clinic and me → what I saw, with their own website on a phone and the fact with its
number → the prototype → what it would include (never a price) → an invitation to a short call.
The prototype depends on the biggest hole of the quick X-ray:
- automations (no WhatsApp, no online booking...): a WhatsApp conversation that books an appointment by
  itself, with the clinic's name and one of its treatments;
- marketing: the message that asks a patient for her Google review (few or low reviews), the panel that
  says where patients come from (nothing measures the visits), or a month of posts waiting for a yes (no
  Instagram on the website). Every number in those is an example, and the video says so;
- a website that is slow on a phone: the score Google PageSpeed gave it in the X-ray next to the goal
  (90, where Google starts calling a page fast) and what slows it down today. Those numbers are real.
The other holes of a website come next: their prototype is a new homepage made by the Web agent, which
costs API.

How it is made: plan() writes the words from the X-ray (instant). Then this file runs as its own process,
like capture.py, so the browser never blocks the panel: it opens the clinic's homepage on a phone, fills
static/video.html with the words and the picture, photographs it frame by frame and joins the frames into
an mp4 with ffmpeg. The videos stay in private/videos, which git ignores.

Videos are made one at a time, in a line: one clinic's from its card, or all the missing ones from the list
of clinics. The line lives in the panel, so it goes on with the page closed; if the panel stops, the line
is lost (the video being made may still finish).

A video can carry sound: my voice for one clinic, or one audio for all the videos (music, or words that
fit every clinic). The clinic's own audio wins. The picture is recorded once, without sound; putting or
changing the audio only joins the two again, in a second or two, without filming anything.
It needs:   pip install imageio-ffmpeg   (ffmpeg comes inside)
Try it by hand with a job file the panel wrote:   python video.py private/videos/clinic-7-job.json
"""

import base64
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

import capture
import clinic_web
import outreach
import site_reader
from inspiration import robots_allow, tidy_url
from xray import number

BASE_DIR = Path(__file__).parent
VIDEOS_DIR = BASE_DIR / "private" / "videos"  # git ignores private/
TEMPLATE = BASE_DIR / "static" / "video.html"
LOGO = BASE_DIR / "static" / "julbeiner-logo.svg"

SIZE = {"width": 360, "height": 640}  # the page, in page pixels
SCALE = 2                             # the video is twice as big: 720 x 1280
FPS = 30
JPEG_QUALITY = 92
CRF = 20               # ffmpeg quality: lower is better and heavier
MAKE_SECONDS = 600     # the whole job, at most
SECONDS_EACH = 80      # what a video usually takes on my computer: for "quedan unos N minutos"
CLOSED_STAGES = ("client", "not_interested")  # clinics I no longer write a first message to
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # on Windows, no black window pops up

# The sound: the kinds of file I can upload, how heavy, and the fade when the audio is longer than the video.
AUDIO_KINDS = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac")
AUDIO_MAX_BYTES = 40 * 1024 * 1024
FADE_SECONDS = 1.5
NO_FFMPEG = "Falta ffmpeg. Escribe en la terminal: pip install imageio-ffmpeg"

# The angles that already have their prototype. Of the web angle, only the holes in PROTOTYPES have one.
READY_ANGLES = {"automation", "marketing"}
# Why a clinic has no video yet, in the list of clinics, for the holes that need me to look at its website.
NOT_READY_SHORT = {"no_web": "Falta su web", "web_down": "Su web no abrió", "web_blocked": "No pude leer su web"}
# The prototype each hole gets. Every other hole of a ready angle gets "booking".
PROTOTYPES = {"low_rating": "review", "fair_rating": "review", "few_reviews": "review", "some_reviews": "review",
              "no_tracking": "sources", "no_instagram": "posts", "slow_mobile": "speed", "fair_mobile": "speed"}
SPEED_GOAL = 90  # from this score on, Google PageSpeed calls a page fast
# What slows a website down, in a clinic's words, and how to spot it among the audits Google says the page
# fails (in Spanish, lowercase and without accents). In this order; the video shows the first three found.
# The unit is what the audit's number measures: "size" (the KiB it would save), "time" or nothing.
BRAKES = [
    ("Imágenes que pesan de más", r"entrega de imagenes|imagenes", "size"),
    ("Archivos que retrasan el arranque", r"bloquean el renderizado", "time"),
    ("Demasiado trabajo para un móvil", r"hilo principal|tiempo de ejecucion de javascript", "time"),
    ("Código que no se usa", r"que no se use|javascript antiguo|javascript duplicado|minifica", "size"),
    ("La página se mueve al cargar", r"cambios de diseno", ""),
    ("Lo principal aparece tarde", r"\blcp\b", ""),
    ("No guarda nada entre visitas", r"cache", ""),
]
SMALL_KIB = 50  # savings smaller than this are not worth a line in a video
# What the proposal would include, for the prototypes that do not depend on the clinic. Never a price.
POINTS = {
    "review": ["Un mensaje a cada paciente después de su cita, pidiendo la reseña.",
               "Con el enlace directo a vuestra ficha de Google: dos toques.",
               "Un aviso para vosotras cada vez que llega una reseña nueva."],
    "sources": ["Medición de las visitas y de las citas de vuestra web.",
                "Un resumen cada lunes: de dónde vino cada paciente.",
                "Recomendaciones claras: dónde invertir y dónde no."],
    "posts": ["Vuestro Instagram enlazado desde la web, a un toque.",
              "Un mes de publicaciones preparado, con vuestro estilo.",
              "Vosotras aprobáis: nada se publica sin vuestro sí."],
    "speed": ["Imágenes y código más ligeros, sin cambiar vuestro diseño.",
              f"Objetivo: {SPEED_GOAL} o más en el test de Google para móvil.",
              "La medición de antes y la de después, para que veáis el cambio."],
}
INTROS = {
    "booking": "Lo que he visto en vuestra web y una idea para que no se os escape ninguna cita.",
    "review": "Lo que he visto de vuestra clínica en Google y una idea para conseguir más reseñas.",
    "sources": "Lo que he visto en vuestra web y una idea para saber qué os trae pacientes.",
    "posts": "Lo que he visto en vuestra web y una idea para que vuestro Instagram trabaje más.",
    "speed": "Lo que he visto en vuestra web y una idea para que abra rápido en el móvil.",
}

# Treatments a patient could ask for on WhatsApp, in the words she would use, and how to spot them on a
# website (lowercase and without accents). The one the website names most goes in the chat; on a tie, the
# first one here. Words a clinic cannot advertise, like the brand of a medicine, are never used.
TREATMENTS = [
    ("una limpieza facial", r"limpieza facial|higiene facial|hydrafacial"),
    ("ácido hialurónico", r"acido hialuronico"),
    ("aumento de labios", r"(?:aumento|relleno|perfilado) de labios"),
    ("arrugas de expresión", r"arrugas de expresion|toxina botulinica|\bbotox\b|neuromodulador"),
    ("depilación láser", r"depilacion (?:con )?laser|depilacion definitiva"),
    ("mesoterapia", r"mesoterapia"),
    ("un peeling", r"\bpeelings?\b"),
    ("hilos tensores", r"hilos tensores|hilos pdo"),
    ("radiofrecuencia", r"radiofrecuencia"),
    ("rinomodelación", r"rinomodelacion"),
    ("plasma rico en plaquetas", r"plasma rico|\bprp\b"),
    ("microneedling", r"microneedling|dermapen|micropuncion"),
    ("skinboosters", r"skin ?boosters?|profhilo"),
    ("presoterapia", r"presoterapia"),
    ("criolipólisis", r"criolipolisis"),
    ("HIFU", r"\bhifu\b"),
]
NO_TREATMENT = "una primera valoración"
# Words that do not make a clinic's initials.
NOT_INITIALS = {"clinica", "clinicas", "clinic", "centro", "instituto", "medicina", "medical", "estetica",
                "de", "del", "la", "el", "los", "las", "y", "dr", "dra", "doctor", "doctora"}

# Everything on the page, as a visitor reads it, without scripts or styles. Hidden menus count: they
# often hold the list of treatments.
PAGE_TEXT = r"""() => {
  if (!document.body) return document.title || '';
  const copy = document.body.cloneNode(true);
  copy.querySelectorAll('script, style, noscript, template, svg').forEach((node) => node.remove());
  return (document.title + ' ' + copy.textContent).replace(/\s+/g, ' ').slice(0, 300000);
}"""


# ---------- The words (instant) ----------

def has_whatsapp(signals: dict) -> bool:
    return bool(signals.get("whatsapp")) or "WhatsApp button" in signals.get("chats", [])


def what_i_saw(clinic: dict, key: str) -> tuple[str, str]:
    """The fact on the card of scene 2: one short line and the detail under it."""
    signals = (clinic.get("web_scan") or {}).get("signals") or {}
    rating, reviews = clinic.get("google_rating"), clinic.get("google_reviews")
    if key == "no_quick_way":
        ways = ("llamar o rellenar un formulario" if signals.get("forms")
                else "llamar o escribir un correo" if signals.get("mail_link") else "llamar")
        return f"Para pedir cita, solo se puede {ways}.", "Ni WhatsApp, ni chat, ni reserva online."
    if key == "no_booking":
        by = " por WhatsApp" if has_whatsapp(signals) else ""
        return f"Se os puede escribir{by}, pero no reservar online.", "Cada cita depende de que alguien conteste a tiempo."
    if key == "phone_not_tappable":
        return ("En el móvil, vuestro teléfono no se puede pulsar para llamar.",
                "Quien os encuentra desde el móvil tiene que copiarlo a mano.")
    if key == "no_tracking":
        return "Vuestra web no mide sus visitas.", "Así no hay forma de saber qué canal os trae pacientes."
    if key in ("slow_mobile", "fair_mobile"):
        return (f"En el móvil, Google le da un {speed_score(clinic)} sobre 100 en velocidad.",
                f"Es su test PageSpeed: a partir de {SPEED_GOAL}, Google considera que una web es rápida.")
    if key == "no_instagram":
        return ("Vuestra web no enlaza a ningún Instagram.",
                "En una clínica estética, es donde más se ve vuestro trabajo.")
    if key in ("low_rating", "fair_rating"):
        return (f"En Google tenéis un {number(rating)} con {reviews} reseñas.",
                "Es lo primero que mira una paciente antes de reservar.")
    if key in ("few_reviews", "some_reviews"):
        return f"En Google tenéis {reviews} reseñas.", "Con más reseñas recientes, más pacientes se deciden."
    return ("Vuestra web está muy cuidada.",
            "Donde suelen perderse citas es después: en lo que se tarda en contestar.")


def speed_score(clinic: dict) -> int | None:
    """The speed score Google PageSpeed gave the clinic's website on a phone, in the X-ray. From 0 to 100."""
    mobile = (clinic.get("xray_scan") or {}).get("mobile") or {}
    score = (mobile.get("scores") or {}).get("speed")
    return score if isinstance(score, int) else None


def brakes(clinic: dict) -> list[list[str]]:
    """What slows the clinic's website down on a phone: up to three, each with its name in plain words and
    its number ("1,2 MB", "1,8 s" or ""). Only what Google measured in the X-ray; nothing is made up."""
    mobile = (clinic.get("xray_scan") or {}).get("mobile") or {}
    failing = [clinic_web.plain(title) for title in (mobile.get("problems") or {}).get("speed") or []]
    found = []
    for name, pattern, unit in BRAKES:
        titles = [title for title in failing if re.search(pattern, title)]
        failing = [title for title in failing if title not in titles]  # each audit counts for one brake
        if not titles:
            continue
        value = ""
        if unit == "size":
            kib = sum(amount(title, "kib") for title in titles)
            if kib < SMALL_KIB:
                continue
            value = f"{number(kib / 1024)} MB" if kib >= 1024 else f"{round(kib)} KB"
        elif unit == "time":
            seconds = max(amount(title, "s") or amount(title, "ms") / 1000 for title in titles)
            value = f"{number(seconds)} s" if seconds else ""
        found.append([name, value])
    return found[:3] or [["Lo principal aparece tarde", ""]]


def amount(title: str, unit: str) -> float:
    """The number an audit's title gives in that unit, as Google writes it in Spanish: "(Ahorro estimado
    de 1.179 KiB)" → 1179.0. 0 when it gives none."""
    found = re.search(r"(\d[\d.,]*) ?" + unit + r"\b", title)
    if not found:
        return 0.0
    try:
        return float(found.group(1).rstrip(".,").replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def treatment_in(text: str) -> str:
    """The treatment the text names most, in a patient's words. "" when it names none."""
    words = clinic_web.plain(text or "")
    count, _, phrase = max((len(re.findall(pattern, words)), -index, phrase)
                           for index, (phrase, pattern) in enumerate(TREATMENTS))
    return phrase if count else ""


def treatment_on_card(typed: str) -> str:
    """The first treatment I wrote on the clinic's card, if any. It wins over what the website says."""
    first = re.split(r"[,;/\n·]| y ", typed or "")[0].strip(" .")
    if not first:
        return ""
    known = treatment_in(first)
    if known:
        return known
    return first[0].lower() + first[1:] if first[1:2].islower() else first


def initials(name: str) -> str:
    """Clínica Rocío Martín → RM. For the round picture of the chat."""
    words = re.findall(r"[^\W\d_]+", name or "")
    kept = [word for word in words if clinic_web.plain(word) not in NOT_INITIALS] or words
    return "".join(word[0].upper() for word in kept[:2]) or "·"


def plan(clinic: dict) -> dict:
    """What the video of a clinic will say, or why it cannot be made yet. Instant: it opens nothing.

    ready is False when the X-ray is missing, unsure, or its hole has no prototype yet; why says what to do,
    and short says it in two or three words, for the list of clinics.
    kind is the prototype: booking, review, sources, posts or speed.
    """
    xray = clinic.get("xray")
    if not xray:
        return {"ready": False, "short": "Sin radiografía",
                "why": "Primero haz su radiografía rápida: el vídeo sale de su agujero más grande."}
    hole = xray.get("hole")
    key = hole["key"] if hole else "none"
    if key in outreach.NOT_READY:
        return {"ready": False, "short": NOT_READY_SHORT.get(key, "Mira su web"), "why": outreach.NOT_READY[key]}
    angle = outreach.angle_of(key)
    if angle not in READY_ANGLES and (key not in PROTOTYPES or speed_score(clinic) is None):
        return {"ready": False, "angle": angle, "short": "Agujero de web: más adelante",
                "why": f"Su agujero es de {outreach.ANGLES[angle]} («{hole['label']}»), y ese vídeo llega más adelante."
                       " De momento están los de automatizaciones, los de marketing y los de una web lenta en el móvil."}
    url = tidy_url((clinic.get("xray_scan") or {}).get("url") or clinic.get("website") or "")
    if not url:
        return {"ready": False, "short": "Falta su web", "why": "Falta su web en la ficha."}

    signals = (clinic.get("web_scan") or {}).get("signals") or {}
    kind = PROTOTYPES.get(key, "booking")
    headline, detail = what_i_saw(clinic, key)
    first = ("Vuestro WhatsApp, contestando y reservando solo, a cualquier hora." if has_whatsapp(signals)
             else "Un WhatsApp que contesta y reserva solo, a cualquier hora.")
    booking = [first,
               "Conectado a vuestra agenda: sin citas dobles ni huecos perdidos.",
               "Un recordatorio el día antes, para que no se pierdan citas."]
    return {
        "ready": True, "why": "", "angle": angle, "key": key, "kind": kind,
        "clinic_id": clinic["id"], "clinic": clinic["name"], "url": url, "xray_at": xray["at"],
        # Only the booking chat names a treatment.
        "treatment": treatment_on_card(clinic.get("treatments") or "") if kind == "booking" else "",
        "headline": headline, "detail": detail,
        "points": POINTS.get(kind, booking),
        # Only the speed prototype: Google's score today and what slows the website down.
        "speed": {"score": speed_score(clinic), "brakes": brakes(clinic)} if kind == "speed" else None,
    }


def prototype(job: dict, treatment: str) -> dict:
    """The words of scene 3, the prototype. In "sources" and "posts" every number is an example; in "speed"
    every number is the one Google measured."""
    clinic = job["clinic"]
    if job["kind"] == "speed":
        score = job["speed"]["score"]
        return {
            "kind": "speed", "kicker": "Así quedaría", "sub": "La misma web, abriéndose rápido en el móvil.",
            "clock": "10:24", "title": "Velocidad en el móvil", "note": "Test de Google PageSpeed",
            "today": {"label": "Hoy", "score": score, "text": str(score)},
            "goal": {"label": "Objetivo", "score": SPEED_GOAL, "text": f"{SPEED_GOAL}+"},
            "brakes_title": "Lo que la frena hoy", "brakes": job["speed"]["brakes"],
            "total": f"De {score} a {SPEED_GOAL} o más",
            "caption": "Vuestra web y vuestra imagen no cambian. Cambia lo que tarda en abrirse.",
        }
    chat = {"kicker": "Así quedaría", "status": "en línea", "input": "Escribe un mensaje"}
    if job["kind"] == "review":
        return chat | {
            "kind": "review", "sub": "Una paciente sale de su cita. Esa misma tarde recibe esto.", "clock": "18:40",
            "thanks": f"¡Gracias por venir hoy a {clinic}!", "thanks_time": "18:40",
            "ask": "¿Nos cuentas cómo ha ido? Tu opinión ayuda a otras pacientes a decidirse.", "ask_time": "18:40",
            "button": "Dejar mi reseña", "published": "Reseña publicada en Google",
            "bye": "¡Mil gracias! Nos ayuda muchísimo.", "bye_time": "18:43",
            "caption": "Cada paciente recibe este mensaje al salir, sin que nadie del equipo tenga que acordarse.",
        }
    if job["kind"] == "sources":
        return {
            "kind": "sources", "kicker": "Así quedaría", "sub": "Cada lunes, en vuestro correo: de dónde vino cada cita.",
            "clock": "9:02", "title": "De dónde vienen vuestras pacientes", "note": "Este mes · datos de ejemplo",
            "rows": [["Google", 16], ["Instagram", 11], ["Recomendación", 7], ["Web directa", 4]],
            "total": "38 citas este mes",
            "caption": "Sabréis qué canal os trae pacientes y dónde merece la pena invertir.",
        }
    if job["kind"] == "posts":
        return {
            "kind": "posts", "kicker": "Así quedaría", "sub": "Un mes de publicaciones, esperando vuestro sí.",
            "clock": "10:15", "count": "9 publicaciones preparadas",
            "tiles": ["Conoce al equipo", "Preguntas frecuentes", "El favorito del mes", "Cuidados en casa",
                      "Un día en la clínica", "Opiniones de pacientes", "Mitos y verdades", "La primera visita",
                      "Pide tu cita"],
            "ready": "Listas para aprobar",
            "caption": "Vosotras solo aprobáis. Y vuestra web lleva directa a vuestro Instagram.",
        }
    asks_for = treatment or NO_TREATMENT
    return chat | {
        "kind": "booking", "sub": "Una paciente os escribe a las 23:14, con la clínica cerrada.", "clock": "23:14",
        "ask": f"Hola, ¿tenéis hueco esta semana para {asks_for}?", "ask_time": "23:14",
        "hello": f"¡Hola! Soy el asistente de {clinic}. Para {asks_for} tengo estos huecos:", "hello_time": "23:14",
        "slots": ["Jue 10:30", "Jue 17:00", "Vie 12:00"], "pick": 1,
        "picked": "El jueves a las 17:00, por favor", "picked_time": "23:15",
        "done": "Hecho: te esperamos el jueves a las 17:00. El día antes te mandamos un recordatorio.",
        "done_time": "23:15",
        "caption": "La cita queda en vuestra agenda sin que nadie del equipo toque el móvil.",
    }


def script(job: dict, treatment: str) -> dict:
    """Every word of the video, for static/video.html. treatment is "" when I do not know one."""
    clinic = job["clinic"]
    return {
        "clinic": clinic, "initials": initials(clinic),
        "domain": (urlsplit(job["url"]).hostname or "").removeprefix("www."),
        "intro": INTROS[job["kind"]],
        "byline": "Un vídeo de Jul Beiner",
        "seen": {"kicker": "Lo que he visto", "label": "Lo primero que cambiaría",
                 "sub": "En Google, antes de reservar" if job["kind"] == "review"
                        else "En vuestra web, desde el móvil",
                 "headline": job["headline"], "detail": job["detail"]},
        "proto": prototype(job, treatment),
        "includes": {"kicker": "La propuesta", "title": "Qué incluiría", "points": job["points"],
                     "foot": "Todo se adapta a cómo trabajáis hoy."},
        "call": {"title": "¿Lo vemos en una llamada corta?",
                 "text": "Contestad a este mensaje y os cuento cómo funcionaría en vuestra clínica.",
                 "role": "Jul Beiner · Software, automatizaciones y marketing para clínicas estéticas",
                 "thanks": "Gracias por vuestro tiempo"},
    }


# ---------- In the panel: make the videos, one after another, and follow them ----------

JOBS: dict[int, dict] = {}  # clinic id → state, progress, what it is doing and the error. Lost on a restart.
LINE: deque[dict] = deque()  # the plans of the videos waiting their turn, first one first
BATCH = {"total": 0, "done": 0, "failed": 0}  # the videos asked for since the line was last empty
LOCK = threading.Lock()
RUNNING = {"line": False}  # True while a thread is working the line; never two at once


def files(clinic_id: int) -> tuple[Path, Path]:
    """The video of a clinic (with its sound, if it has one) and the facts of how it was made."""
    return VIDEOS_DIR / f"clinic-{clinic_id}.mp4", VIDEOS_DIR / f"clinic-{clinic_id}.json"


def silent_copy(clinic_id: int) -> Path:
    """The picture without sound, as it was recorded. The audio is put on a copy of it."""
    return VIDEOS_DIR / f"clinic-{clinic_id}-silent.mp4"


def made(clinic_id: int) -> dict | None:
    """When and how the clinic's video was made, or None when it has none."""
    video, facts = files(clinic_id)
    try:
        return json.loads(facts.read_text(encoding="utf-8")) if video.is_file() else None
    except (OSError, ValueError):
        return None


def summary(clinic: dict, ready: dict | None = None, facts: dict | None = None) -> dict:
    """The video of a clinic in a few words, for the lists. Instant: it only reads a small file.

    state: none (no video yet), waiting (in the line), working, done or failed. outdated: the X-ray was
    taken again after the video was made. wanted: the button for all the missing videos would make it.
    """
    ready = ready or plan(clinic)
    facts = facts if facts is not None else made(clinic["id"])
    job = dict(JOBS.get(clinic["id"], {}))
    state = job.get("state") or ("done" if facts else "none")
    if state == "done" and not facts:
        state = "none"
    outdated = bool(facts and ready.get("xray_at") and facts.get("xray_at") != ready["xray_at"])
    may_write = not clinic.get("do_not_contact") and clinic.get("stage") not in CLOSED_STAGES
    return {
        "ready": ready["ready"], "short": ready.get("short", ""), "state": state,
        "progress": job.get("progress", 0.0), "outdated": outdated,
        "made_at": facts.get("made_at") if facts else None,
        "wanted": bool(ready["ready"] and may_write and (state in ("none", "failed") or (state == "done" and outdated))),
    }


def status(clinic: dict) -> dict:
    """Everything the card needs to show the video section: the summary and more.

    made: the facts of the last video, which stays even when a new one fails. ahead: while it waits, how
    many videos go before it.
    """
    ready = plan(clinic)
    facts = made(clinic["id"])
    short = summary(clinic, ready, facts)
    job = dict(JOBS.get(clinic["id"], {}))
    with LOCK:
        waiting = [one["clinic_id"] for one in LINE]
        working = any(one.get("state") == "working" for one in JOBS.values())
    ahead = waiting.index(clinic["id"]) + working if clinic["id"] in waiting else 0
    return short | {
        "why": ready["why"], "say": job.get("say", ""), "ahead": ahead,
        "error": job.get("error", "") if short["state"] == "failed" else "",
        "made": facts,
        # own: the audio uploaded for this clinic. all: the one for every video. Without the file paths.
        "audio": {"own": shown(audio_info(clinic["id"])), "all": shown(audio_info(None))},
    }


def start(clinic: dict) -> dict:
    """Make the video of a clinic, in the background. If another one is being made, it waits its turn."""
    job = plan(clinic)
    if not job["ready"]:
        raise HTTPException(status_code=409, detail=job["why"])
    queue([job])
    return status(clinic)


def queue(jobs: list[dict]) -> int:
    """Put videos in the line, each one a ready plan(). A clinic already waiting or being made is not put
    twice. Returns how many went in. One thread works the line, one video at a time."""
    with LOCK:
        taken = {one["clinic_id"] for one in LINE}
        taken |= {clinic_id for clinic_id, one in JOBS.items() if one.get("state") == "working"}
        if not taken:  # a new batch: count from zero
            BATCH.update(total=0, done=0, failed=0)
        added = 0
        for job in jobs:
            if job["clinic_id"] in taken:
                continue
            taken.add(job["clinic_id"])
            LINE.append(job)
            JOBS[job["clinic_id"]] = {"state": "waiting", "progress": 0.0, "say": "", "error": "", "clinic": job["clinic"]}
            added += 1
        BATCH["total"] += added
        if added and not RUNNING["line"]:
            RUNNING["line"] = True
            threading.Thread(target=work_the_line, daemon=True).start()
    return added


def work_the_line() -> None:
    """Make the videos of the line, the first one first, until it is empty."""
    while True:
        with LOCK:
            if not LINE:
                RUNNING["line"] = False
                return
            job = LINE.popleft()
            clinic_id = job["clinic_id"]
            JOBS[clinic_id] = {"state": "working", "progress": 0.0, "say": "Empezando…", "error": "",
                               "clinic": job["clinic"]}
        try:
            VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
            job_file = VIDEOS_DIR / f"clinic-{clinic_id}-job.json"
            job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            follow(clinic_id, job_file)
        except Exception as error:  # one video that fails never stops the others
            with LOCK:
                if clinic_id in JOBS:
                    JOBS[clinic_id].update(state="failed", error=f"{type(error).__name__}: {error}"[:300])
        with LOCK:
            BATCH["done" if JOBS.get(clinic_id, {}).get("state") == "done" else "failed"] += 1


def stop() -> int:
    """Empty the line: the video being made now finishes, the waiting ones are not made. Returns how many."""
    with LOCK:
        dropped = [job["clinic_id"] for job in LINE]
        LINE.clear()
        for clinic_id in dropped:
            JOBS.pop(clinic_id, None)
        BATCH["total"] -= len(dropped)
    return len(dropped)


def line() -> dict:
    """How the line goes, for the list of clinics: the video being made, the ones waiting and the batch."""
    with LOCK:
        now = next(({"clinic_id": clinic_id, "clinic": one.get("clinic", ""), "progress": one.get("progress", 0.0),
                     "say": one.get("say", "")}
                    for clinic_id, one in JOBS.items() if one.get("state") == "working"), None)
        waiting = [job["clinic_id"] for job in LINE]
        batch = dict(BATCH)
    left = len(waiting) * SECONDS_EACH + ((1 - now["progress"]) * SECONDS_EACH if now else 0)
    return {"running": bool(now or waiting), "now": now, "waiting": waiting,
            "minutes_left": round(left / 60) if left else 0, **batch}


def follow(clinic_id: int, job_file: Path) -> None:
    """Run this file as its own process and keep JOBS up to date with the lines it prints."""
    def update(**changes) -> None:
        with LOCK:
            if clinic_id in JOBS:  # not when the clinic was erased meanwhile
                JOBS[clinic_id].update(changes)

    too_long = threading.Event()
    try:
        process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "video.py"), str(job_file)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
        timer = threading.Timer(MAKE_SECONDS, lambda: (too_long.set(), process.kill()))
        timer.start()
        last, other = {}, deque(maxlen=5)
        for printed in process.stdout:
            try:
                said = json.loads(printed)
            except ValueError:
                other.append(printed.strip())
                continue
            if "progress" in said:
                update(progress=said["progress"], say=said["say"])
            else:
                last = said
        process.wait()
        timer.cancel()
        if last.get("ok"):
            update(state="done", progress=1.0, say="")
        else:
            error = ("Tardó demasiado y lo he parado." if too_long.is_set()
                     else last.get("error") or " ".join(other)[-300:] or "El proceso terminó sin decir nada.")
            update(state="failed", error=error)
    except Exception as error:  # the panel must never fall because of a video
        update(state="failed", error=f"{type(error).__name__}: {error}"[:300])
    finally:
        job_file.unlink(missing_ok=True)


def forget(clinic_id: int) -> None:
    """Delete everything of a clinic's video, its own audio too. For when the clinic is erased."""
    with LOCK:
        kept = [job for job in LINE if job["clinic_id"] != clinic_id]
        BATCH["total"] -= len(LINE) - len(kept)
        LINE.clear()
        LINE.extend(kept)
        JOBS.pop(clinic_id, None)
    video, facts = files(clinic_id)
    silent = silent_copy(clinic_id)
    for path in (video, facts, silent, part_of(video), part_of(silent), VIDEOS_DIR / f"clinic-{clinic_id}-job.json"):
        path.unlink(missing_ok=True)
    drop_audio(clinic_id)


# ---------- The sound ----------

def part_of(path: Path) -> Path:
    """Where a file is written before it is finished: a stopped job never leaves a file that looks done."""
    return path.with_name(path.stem + "-part.mp4")


def find_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return ""


def audio_slot(clinic_id: int | None) -> str:
    """The start of the audio's file names: one clinic's, or the one for all the videos (None)."""
    return f"clinic-{clinic_id}-audio" if clinic_id is not None else "all-audio"


def audio_info(clinic_id: int | None) -> dict | None:
    """One clinic's audio (or the one for all, with None): its name when I uploaded it, how long it lasts
    and its file. None when there is none."""
    slot = audio_slot(clinic_id)
    try:
        about = json.loads((VIDEOS_DIR / f"{slot}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = VIDEOS_DIR / (slot + about.get("kind", ""))
    return about | {"path": str(path), "for_all": clinic_id is None} if path.is_file() else None


def shown(audio: dict | None) -> dict | None:
    """An audio as the card shows it."""
    return {key: audio[key] for key in ("name", "seconds", "added_at", "for_all")} if audio else None


def audio_for(clinic_id: int) -> dict | None:
    """The audio a clinic's video carries: its own, or else the one for all."""
    return audio_info(clinic_id) or audio_info(None)


def seconds_of(path: Path, ffmpeg: str) -> float | None:
    """How long an audio lasts, or None when ffmpeg finds no sound in it."""
    done = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    length = re.search(r"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", done.stderr)
    if not length or "Audio:" not in done.stderr:
        return None
    hours, minutes, seconds = length.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def drop_audio(clinic_id: int | None) -> None:
    slot = audio_slot(clinic_id)
    for path in [VIDEOS_DIR / f"{slot}.json", *(VIDEOS_DIR / (slot + kind) for kind in AUDIO_KINDS)]:
        path.unlink(missing_ok=True)


def save_audio(clinic_id: int, for_all: bool, filename: str, content: bytes) -> None:
    """Keep an audio I uploaded, for this clinic or (for_all) for every video, and put it on the videos."""
    kind = Path(filename or "").suffix.lower()
    if kind not in AUDIO_KINDS:
        raise HTTPException(status_code=422, detail="Ese archivo no es un audio que conozca."
                                                    " Sube un mp3, m4a, wav, aac, ogg, opus o flac.")
    if len(content) > AUDIO_MAX_BYTES:
        raise HTTPException(status_code=422, detail="Pesa demasiado: el máximo son 40 MB.")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(status_code=409, detail=NO_FFMPEG)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    owner = None if for_all else clinic_id
    slot = audio_slot(owner)
    new = VIDEOS_DIR / f"{slot}-new{kind}"
    new.write_bytes(content)
    seconds = seconds_of(new, ffmpeg)
    if seconds is None:
        new.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="No he podido leer ese audio. Prueba a exportarlo como mp3.")
    drop_audio(owner)  # the one before, whatever its kind
    new.replace(VIDEOS_DIR / (slot + kind))
    about = {"name": Path(filename).name[:120], "kind": kind, "seconds": round(seconds, 1),
             "added_at": datetime.now().isoformat(timespec="seconds")}
    (VIDEOS_DIR / f"{slot}.json").write_text(json.dumps(about, ensure_ascii=False), encoding="utf-8")
    remix(clinic_id, for_all)


def remove_audio(clinic_id: int, for_all: bool) -> None:
    """Take the audio off this clinic's video, or off every video (for_all)."""
    drop_audio(None if for_all else clinic_id)
    remix(clinic_id, for_all)


def remix(clinic_id: int, for_all: bool) -> None:
    """Put the sound again on the videos an audio change touches. This clinic's at once; with for_all,
    the others in the background (the ones with their own audio do not change)."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(status_code=409, detail=NO_FFMPEG)
    if for_all:
        others = [int(path.name.split("-")[1]) for path in VIDEOS_DIR.glob("clinic-*-silent.mp4")]
        others = [other for other in others if other != clinic_id and not audio_info(other)]
        threading.Thread(target=mix_many, args=(others, ffmpeg), daemon=True).start()
    try:
        mix_now(clinic_id, ffmpeg)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=f"He guardado el audio, pero no he podido ponerlo en el vídeo: {error}")


def mix_many(clinic_ids: list[int], ffmpeg: str) -> None:
    for clinic_id in clinic_ids:
        try:
            mix_now(clinic_id, ffmpeg)
        except Exception:
            pass  # that clinic keeps its video as it was; making it again puts the sound


def mix_now(clinic_id: int, ffmpeg: str) -> None:
    """mix(), unless the clinic's video is being made: the job puts the sound when it finishes."""
    if JOBS.get(clinic_id, {}).get("state") != "working":
        mix(clinic_id, ffmpeg)


def mix(clinic_id: int, ffmpeg: str) -> None:
    """Make the clinic's video from its silent copy and its audio. The picture is copied as it is.

    An audio longer than the video is cut with a fade at the end; a shorter one leaves the end silent.
    """
    video, facts_file = files(clinic_id)
    silent = silent_copy(clinic_id)
    try:
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not silent.is_file():
        return
    audio, part = audio_for(clinic_id), part_of(video)
    if audio:
        length = facts["seconds"]
        command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(silent), "-i", audio["path"],
                   "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", str(length)]
        if audio["seconds"] > length - 0.2:
            command += ["-af", f"afade=t=out:st={max(0.0, length - FADE_SECONDS):.2f}:d={FADE_SECONDS}"]
        done = subprocess.run(command + ["-movflags", "+faststart", str(part)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        if done.returncode != 0:
            raise RuntimeError("ffmpeg: " + (done.stderr.strip()[-300:] or "sin detalles"))
    else:
        shutil.copyfile(silent, part)
    replace(part, video)
    facts.update(audio=shown(audio), mixed_at=datetime.now().isoformat(timespec="seconds"), bytes=video.stat().st_size)
    facts_file.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- In its own process: the browser and ffmpeg ----------

def tell(progress: float, words: str) -> None:
    """A line for the panel. Only ASCII in it, so any Windows console can carry it."""
    print(json.dumps({"progress": round(progress, 3), "say": words}), flush=True)


def finish(ok: bool, error: str = "", **facts) -> int:
    print(json.dumps({"ok": ok, "error": error} | facts), flush=True)
    return 0 if ok else 1


def data_uri(content: bytes, kind: str) -> str:
    return f"data:{kind};base64," + base64.b64encode(content).decode("ascii")


def look(browser, url: str) -> tuple[bytes, str]:
    """The clinic's homepage on a phone: its first screen, as a visitor sees it, and all its text."""
    if not site_reader.is_public(url):
        raise RuntimeError("Su web no es una dirección pública.")
    if not robots_allow(url):
        raise RuntimeError("Su web pide a los programas que no entren, así que no la abro.")
    device = capture.DEVICES["mobile"]
    context = browser.new_context(ignore_https_errors=True, locale="es-ES", **device)
    try:
        page = context.new_page()
        try:
            page.goto(url, wait_until="load", timeout=30_000)
        except Exception as error:
            raise RuntimeError("Su web no abrió en el móvil. Prueba otra vez en un rato.") from error
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass  # some pages never go quiet (chats, video): what has loaded is enough
        page.wait_for_timeout(2_000)  # the opening animation
        if page.evaluate(capture.ACCEPT_COOKIES):
            page.wait_for_timeout(900)  # so the cookie notice is not what the picture shows
        page.evaluate(capture.SCROLL_TO, 0)
        session = context.new_cdp_session(page)
        shot = capture.photograph(session, "mobile", 0, device["viewport"]["height"], capture.DETAIL["mobile"], "jpeg")
        return shot, page.evaluate(PAGE_TEXT)
    finally:
        context.close()


def page_html(data: dict) -> str:
    """static/video.html with one clinic's words and pictures inside."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace(
        "<!--VIDEO-DATA-->", f"<script>window.VIDEO = {payload};</script>", 1)


def moving(spans: list[list[float]], moment: float) -> bool:
    step = 1 / FPS
    return any(start - step <= moment <= end + step for start, end in spans)


def record(browser, html: str, out: Path, ffmpeg: str) -> dict:
    """Photograph the page frame by frame and send the frames to ffmpeg, which writes the mp4.

    While nothing moves, the last photograph is sent again: it is the same picture, and much faster.
    """
    context = browser.new_context(viewport=dict(SIZE), device_scale_factor=SCALE)
    try:
        page = context.new_page()
        page.set_content(html, wait_until="load", timeout=60_000)
        seconds = page.evaluate("() => window.ready")  # the typefaces and the pictures are in
        spans = page.evaluate("() => window.spans()")
        session = context.new_cdp_session(page)
        clip = {"x": 0, "y": 0, **SIZE, "scale": SCALE}
        total = round(seconds * FPS)
        encoder = subprocess.Popen(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF), "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW,
        )
        picture, drawn = b"", 0
        try:
            for frame in range(total):
                moment = frame / FPS
                if not picture or moving(spans, moment):
                    page.evaluate("(seconds) => window.seek(seconds)", moment)
                    answer = session.send("Page.captureScreenshot", {"format": "jpeg", "quality": JPEG_QUALITY, "clip": clip})
                    picture = base64.b64decode(answer["data"])
                    drawn += 1
                encoder.stdin.write(picture)
                if frame % FPS == 0:
                    tell(0.12 + 0.83 * frame / total, "Grabando…")
            encoder.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg stopped: what it says comes next
        problem = encoder.stderr.read().decode("utf-8", "replace").strip()
        if encoder.wait() != 0:
            raise RuntimeError("ffmpeg no pudo hacer el vídeo: " + (problem[-300:] or "sin detalles"))
    finally:
        context.close()
    return {"seconds": round(total / FPS, 1), "frames": total, "drawn": drawn}


def replace(new: Path, old: Path) -> None:
    """Put the new video where the old one was. On Windows a file someone is reading cannot be replaced:
    the panel may still be sending the old video to the card, so it tries again for a few seconds."""
    for attempt in range(20):
        try:
            new.replace(old)
            return
        except PermissionError:
            if attempt == 19:
                raise RuntimeError("El vídeo anterior sigue abierto. Cierra la ficha en otras pestañas y vuelve a probar.")
            time.sleep(0.5)


def main(job_path: str) -> int:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return finish(False, NO_FFMPEG)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return finish(False, "Falta Playwright. Escribe en la terminal: pip install playwright")

    started = time.monotonic()
    with sync_playwright() as playwright:
        try:
            browser, _ = capture.launch(playwright)
        except RuntimeError as error:
            return finish(False, str(error))
        try:
            tell(0.02, "Abriendo su web en un móvil…")
            shot, text = look(browser, job["url"])
            booking = job["kind"] == "booking"
            treatment = (job["treatment"] or treatment_in(text)) if booking else ""
            source = "card" if job["treatment"] else "web" if treatment else ""
            data = script(job, treatment) | {"shot": data_uri(shot, "image/jpeg"),
                                             "logo": data_uri(LOGO.read_bytes(), "image/svg+xml")}
            tell(0.1, "Montando las escenas…")
            clinic_id = job["clinic_id"]
            silent = silent_copy(clinic_id)
            done = record(browser, page_html(data), part_of(silent), ffmpeg)
            replace(part_of(silent), silent)
            facts = {
                "made_at": datetime.now().isoformat(timespec="seconds"), "xray_at": job["xray_at"],
                "angle": job["angle"], "key": job["key"], "prototype": job["kind"],
                "treatment": (treatment or NO_TREATMENT) if booking else "",
                "treatment_from": source, "took": round(time.monotonic() - started), **done,
            }
            files(clinic_id)[1].write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
            tell(0.97, "Poniendo el audio…" if audio_for(clinic_id) else "Terminando…")
            mix(clinic_id, ffmpeg)
            return finish(True, seconds=done["seconds"])
        except Exception as error:
            words = (str(error).strip().splitlines() or [type(error).__name__])[0]
            return finish(False, words[:300])
        finally:
            browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python video.py JOB_FILE   (the panel writes the job file when I press «Hacer el vídeo»)")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
