"""The video that carries my proposal and its prototype to a clinic. Under a minute, vertical, made by
Atelier alone. Free: no API.

It goes as an attachment in WhatsApp, Instagram or an email. Atelier lives on my computer, so a link to it
would not open on a clinic's phone; a video needs nothing to open.

Five scenes: the clinic and me → what I saw, with their own website on a phone and the fact with its
number → the prototype → what it would include (never a price) → an invitation to a short call.
The prototype depends on the biggest hole of the quick X-ray. For now there is one, for automations
(no WhatsApp, no online booking...): a WhatsApp conversation that books an appointment by itself, with the
clinic's name and one of its treatments. Marketing and web come next.

How it is made: plan() writes the words from the X-ray (instant). Then this file runs as its own process,
like capture.py, so the browser never blocks the panel: it opens the clinic's homepage on a phone, fills
static/video.html with the words and the picture, photographs it frame by frame and joins the frames into
an mp4 with ffmpeg. The videos stay in private/videos, which git ignores.
It needs:   pip install imageio-ffmpeg   (ffmpeg comes inside)
Try it by hand with a job file the panel wrote:   python video.py private/videos/clinic-7-job.json
"""

import base64
import json
import re
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
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # on Windows, no black window pops up

# The angles that already have their prototype.
READY_ANGLES = {"automation"}

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
    return ("Vuestra web está muy cuidada.",
            "Donde suelen perderse citas es después: en lo que se tarda en contestar.")


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

    ready is False when the X-ray is missing, unsure, or its hole has no prototype yet; why says what to do.
    """
    xray = clinic.get("xray")
    if not xray:
        return {"ready": False, "why": "Primero haz su radiografía rápida: el vídeo sale de su agujero más grande."}
    hole = xray.get("hole")
    key = hole["key"] if hole else "none"
    if key in outreach.NOT_READY:
        return {"ready": False, "why": outreach.NOT_READY[key]}
    angle = outreach.angle_of(key)
    if angle not in READY_ANGLES:
        return {"ready": False, "angle": angle,
                "why": f"Su agujero es de {outreach.ANGLES[angle]}, y ese vídeo llega en el siguiente paso de Atelier."
                       " De momento solo está el de automatizaciones."}
    url = tidy_url((clinic.get("xray_scan") or {}).get("url") or clinic.get("website") or "")
    if not url:
        return {"ready": False, "why": "Falta su web en la ficha."}

    signals = (clinic.get("web_scan") or {}).get("signals") or {}
    headline, detail = what_i_saw(clinic, key)
    first = ("Vuestro WhatsApp, contestando y reservando solo, a cualquier hora." if has_whatsapp(signals)
             else "Un WhatsApp que contesta y reserva solo, a cualquier hora.")
    return {
        "ready": True, "why": "", "angle": angle, "key": key,
        "clinic_id": clinic["id"], "clinic": clinic["name"], "url": url, "xray_at": xray["at"],
        "treatment": treatment_on_card(clinic.get("treatments") or ""),
        "headline": headline, "detail": detail,
        "points": [first,
                   "Conectado a vuestra agenda: sin citas dobles ni huecos perdidos.",
                   "Un recordatorio el día antes, para que no se pierdan citas."],
    }


def script(job: dict, treatment: str) -> dict:
    """Every word of the video, for static/video.html. treatment is "" when I do not know one."""
    clinic = job["clinic"]
    asks_for = treatment or NO_TREATMENT
    return {
        "clinic": clinic, "initials": initials(clinic),
        "domain": (urlsplit(job["url"]).hostname or "").removeprefix("www."),
        "intro": "Lo que he visto en vuestra web y una idea para que no se os escape ninguna cita.",
        "byline": "Un vídeo de Jul Beiner",
        "seen": {"kicker": "Lo que he visto", "sub": "En vuestra web, desde el móvil", "label": "Lo primero que cambiaría",
                 "headline": job["headline"], "detail": job["detail"]},
        "proto": {
            "kicker": "Así quedaría", "sub": "Una paciente os escribe a las 23:14, con la clínica cerrada.",
            "status": "en línea", "input": "Escribe un mensaje", "clock": "23:14",
            "ask": f"Hola, ¿tenéis hueco esta semana para {asks_for}?", "ask_time": "23:14",
            "hello": f"¡Hola! Soy el asistente de {clinic}. Para {asks_for} tengo estos huecos:", "hello_time": "23:14",
            "slots": ["Jue 10:30", "Jue 17:00", "Vie 12:00"], "pick": 1,
            "picked": "El jueves a las 17:00, por favor", "picked_time": "23:15",
            "done": "Hecho: te esperamos el jueves a las 17:00. El día antes te mandamos un recordatorio.",
            "done_time": "23:15",
            "caption": "La cita queda en vuestra agenda sin que nadie del equipo toque el móvil.",
        },
        "includes": {"kicker": "La propuesta", "title": "Qué incluiría", "points": job["points"],
                     "foot": "Todo se adapta a cómo trabajáis hoy."},
        "call": {"title": "¿Lo vemos en una llamada corta?",
                 "text": "Contestad a este mensaje y os cuento cómo funcionaría en vuestra clínica.",
                 "role": "Jul Beiner · Software, automatizaciones y marketing para clínicas estéticas",
                 "thanks": "Gracias por vuestro tiempo"},
    }


# ---------- In the panel: start a video and follow it ----------

JOBS: dict[int, dict] = {}  # clinic id → state, progress, what it is doing and the error. Lost on a restart.
LOCK = threading.Lock()


def files(clinic_id: int) -> tuple[Path, Path]:
    """The video of a clinic and the facts of how it was made."""
    return VIDEOS_DIR / f"clinic-{clinic_id}.mp4", VIDEOS_DIR / f"clinic-{clinic_id}.json"


def made(clinic_id: int) -> dict | None:
    """When and how the clinic's video was made, or None when it has none."""
    video, facts = files(clinic_id)
    try:
        return json.loads(facts.read_text(encoding="utf-8")) if video.is_file() else None
    except (OSError, ValueError):
        return None


def status(clinic: dict) -> dict:
    """Everything the card needs to show the video section.

    state: none (no video yet), working, done or failed. made: the facts of the last video, which stays
    even when a new one fails. outdated: the X-ray was taken again after the video was made.
    """
    ready = plan(clinic)
    job = dict(JOBS.get(clinic["id"], {}))
    facts = made(clinic["id"])
    state = job.get("state") or ("done" if facts else "none")
    if state == "done" and not facts:
        state = "none"
    return {
        "ready": ready["ready"], "why": ready["why"], "state": state,
        "progress": job.get("progress", 0.0), "say": job.get("say", ""),
        "error": job.get("error", "") if state == "failed" else "",
        "made": facts,
        "outdated": bool(facts and ready.get("xray_at") and facts.get("xray_at") != ready["xray_at"]),
    }


def start(clinic: dict) -> dict:
    """Start making the video of a clinic, in the background. One video at a time."""
    job = plan(clinic)
    if not job["ready"]:
        raise HTTPException(status_code=409, detail=job["why"])
    with LOCK:
        if any(one.get("state") == "working" for one in JOBS.values()):
            raise HTTPException(status_code=409, detail="Ya se está haciendo un vídeo: espera a que termine.")
        JOBS[clinic["id"]] = {"state": "working", "progress": 0.0, "say": "Empezando…", "error": ""}
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    video, facts = files(clinic["id"])
    job_file = VIDEOS_DIR / f"clinic-{clinic['id']}-job.json"
    job_file.write_text(json.dumps(job | {"video": str(video), "facts": str(facts)}, ensure_ascii=False), encoding="utf-8")
    threading.Thread(target=follow, args=(clinic["id"], job_file), daemon=True).start()
    return status(clinic)


def follow(clinic_id: int, job_file: Path) -> None:
    """Run this file as its own process and keep JOBS up to date with the lines it prints."""
    def update(**changes) -> None:
        with LOCK:
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
        for line in process.stdout:
            try:
                said = json.loads(line)
            except ValueError:
                other.append(line.strip())
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
    """Delete everything of a clinic's video. For when the clinic is erased."""
    video, facts = files(clinic_id)
    for path in (video, facts, video.with_name(video.stem + "-part.mp4"), VIDEOS_DIR / f"clinic-{clinic_id}-job.json"):
        path.unlink(missing_ok=True)
    with LOCK:
        JOBS.pop(clinic_id, None)


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
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return finish(False, "Falta ffmpeg. Escribe en la terminal: pip install imageio-ffmpeg")
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
            treatment = job["treatment"] or treatment_in(text)
            source = "card" if job["treatment"] else "web" if treatment else ""
            data = script(job, treatment) | {"shot": data_uri(shot, "image/jpeg"),
                                             "logo": data_uri(LOGO.read_bytes(), "image/svg+xml")}
            tell(0.1, "Montando las escenas…")
            video = Path(job["video"])
            part = video.with_name(video.stem + "-part.mp4")  # a stopped video never looks finished
            done = record(browser, page_html(data), part, ffmpeg)
            replace(part, video)
            facts = {
                "made_at": datetime.now().isoformat(timespec="seconds"), "xray_at": job["xray_at"],
                "angle": job["angle"], "key": job["key"], "treatment": treatment or NO_TREATMENT,
                "treatment_from": source, "bytes": video.stat().st_size,
                "took": round(time.monotonic() - started), **done,
            }
            Path(job["facts"]).write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
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
