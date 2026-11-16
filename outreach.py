"""The message that sends a clinic my proposal and its prototype, written around the biggest hole of its
quick X-ray. Free: no API.

Atelier never sends it. It drafts it; I read it, change what I want, copy it and send it myself, with the
clinic's video attached (video.py): the proposal and its prototype in a minute.

It always goes out as Jul Beiner: software, automations and marketing for aesthetic clinics. The tone is
professional and clear: what I analysed, the fact with its number, and what I prepared for them.

Two kinds of message, each for Instagram, WhatsApp and email:
- proposal: what I saw, and the video that carries the proposal and the prototype;
- follow_up: a few days later, when there was no answer.
"""

import re

from xray import number

WHO = "Soy Jul Beiner y me dedico al desarrollo de software, automatizaciones y marketing para clínicas estéticas."
SIGNATURE = "Jul Beiner\njulbeiner · software, automatizaciones y marketing para clínicas estéticas"
# The video goes as an attachment: a link to Atelier would not open on the clinic's phone.
VIDEO_LINE = "Os adjunto un vídeo de un minuto con lo que he visto y un prototipo de cómo quedaría."

# The kinds of message, and the contact each one is in the history once it is sent.
KINDS = {
    "proposal": ("Propuesta y prototipo", "proposal_sent"),
    "follow_up": ("Seguimiento", "message_sent"),
}
CHANNELS = {"instagram": "Instagram", "whatsapp": "WhatsApp", "email": "Correo"}

# What each hole is about, so the proposal is about the one thing they need first. "none" is a clinic
# with no hole from outside: what is left is what happens after someone writes, so automations.
WEB_HOLES = {"slow_mobile", "fair_mobile", "no_https", "not_mobile", "no_reviews_on_site",
             "no_description", "low_seo", "slow_server", "no_book_button"}
MARKETING_HOLES = {"no_tracking", "low_rating", "fair_rating", "few_reviews", "some_reviews", "no_instagram"}
ANGLES = {"web": "su web", "automation": "automatizaciones", "marketing": "marketing"}

# With these I do not know enough to write: first I look at their website myself.
NOT_READY = {
    "no_web": "Primero mira en Google Maps si tiene web y pégala en la ficha. Luego haz la radiografía otra vez.",
    "web_down": "Su web no abrió. Ábrela tú y, si funciona, repite la radiografía.",
    "web_blocked": "No he podido leer su web. Mírala tú antes de escribirle.",
}


def angle_of(key: str) -> str:
    return "web" if key in WEB_HOLES else "marketing" if key in MARKETING_HOLES else "automation"


def seen(clinic: dict, key: str) -> tuple[str, str, str]:
    """What I analysed and found, what the proposal does about it, and the few words that name it.

    "He analizado vuestra web en el móvil: tarda 6,2 segundos...", "hacerla rápida", "la velocidad de vuestra web".
    """
    mobile = (clinic.get("xray_scan") or {}).get("mobile") or {}
    signals = (clinic.get("web_scan") or {}).get("signals") or {}
    scores = mobile.get("scores") or {}
    milliseconds = (mobile.get("numbers") or {}).get("main_content")
    seconds = number(milliseconds / 1000) if isinstance(milliseconds, (int, float)) else ""
    rating, reviews = clinic.get("google_rating"), clinic.get("google_reviews")

    if key == "slow_mobile":
        takes = f"tarda {seconds} segundos en mostrar el contenido principal y " if seconds else ""
        return (f"He analizado vuestra web en el móvil: {takes}Google le da un {scores.get('speed')} sobre 100 en velocidad.",
                "hacerla rápida", "la velocidad de vuestra web")
    if key == "fair_mobile":
        return (f"He analizado vuestra web: en el móvil, Google le da un {scores.get('speed')} sobre 100 en velocidad.",
                "hacerla más rápida", "la velocidad de vuestra web")
    if key == "no_https":
        return ("He analizado vuestra web: no tiene certificado de seguridad (https) y el navegador avisa a quien"
                " entra de que no es segura.", "resolverlo", "la seguridad de vuestra web")
    if key == "not_mobile":
        return ("He analizado vuestra web: no está adaptada al móvil, y desde el teléfono se ve en pequeño.",
                "adaptarla", "vuestra web en el móvil")
    if key == "no_reviews_on_site":
        return ("He analizado vuestra web: no muestra opiniones de pacientes, que son de lo que más pesa antes de"
                " reservar.", "incorporarlas", "las opiniones en vuestra web")
    if key == "no_description":
        return ("He analizado vuestra web: no tiene descripción para Google, así que el texto que aparece en los"
                " resultados lo elige Google.", "resolverlo", "cómo aparece vuestra web en Google")
    if key == "low_seo":
        return (f"He analizado vuestra web: Google le da un {scores.get('seo')} sobre 100 en SEO básico.",
                "mejorarlo", "el SEO de vuestra web")
    if key == "slow_server":
        return (f"He analizado vuestra web: tarda {number(signals.get('seconds') or 0)} segundos en empezar a cargar.",
                "resolverlo", "la velocidad de vuestra web")
    if key == "no_book_button":
        return ("He analizado vuestra web: en la portada no hay una llamada clara a pedir cita.",
                "resolverlo", "pedir cita desde vuestra web")
    if key == "no_quick_way":
        ways = "llamar o rellenar un formulario" if signals.get("forms") else "llamar"
        return (f"He analizado cómo se pide cita en vuestra clínica: desde la web solo se puede {ways},"
                " sin WhatsApp ni reserva online.", "resolverlo", "pedir cita sin tener que llamar")
    if key == "no_booking":
        return ("He analizado cómo se pide cita en vuestra clínica: se os puede escribir por WhatsApp, pero no"
                " reservar online, así que cada cita depende de que alguien conteste a tiempo.",
                "automatizarlo", "la reserva online")
    if key == "phone_not_tappable":
        return ("He analizado vuestra web en el móvil: el teléfono no se puede pulsar para llamar.",
                "resolverlo", "el teléfono de vuestra web")
    if key == "no_tracking":
        return ("He analizado vuestra web: no tiene nada que mida las visitas, así que no hay forma de saber qué"
                " canal os trae pacientes.", "medirlo", "medir de dónde vienen vuestros pacientes")
    if key in ("low_rating", "fair_rating"):
        return (f"He analizado cómo os encuentran en Google: tenéis un {number(rating)} con {reviews} reseñas.",
                "mejorarlo", "vuestras reseñas en Google")
    if key in ("few_reviews", "some_reviews"):
        return (f"He analizado cómo os encuentran en Google: tenéis {reviews} reseñas.",
                "conseguir más", "vuestras reseñas en Google")
    if key == "no_instagram":
        return ("He analizado vuestra web: no enlaza a ningún Instagram, que en una clínica estética es donde más"
                " se ven los resultados.", "resolverlo", "vuestro Instagram")
    return ("He analizado vuestra web y está muy cuidada. Donde suelen perderse más citas es en lo que pasa"
            " después: cuánto se tarda en contestar y si se vuelve a escribir a quien no reserva.",
            "automatizarlo", "lo que pasa cuando alguien os escribe")


def greeting_name(people: list[dict]) -> str:
    """Who I write to: the person who decides, or else the first one.

    A doctor goes with the title and the surname ("Dra. García"); anyone else with the first name.
    """
    if not people:
        return ""
    person = next((person for person in people if person.get("decides")), people[0])
    words = (person.get("name") or "").split()
    if not words:
        return ""
    title = re.sub(r"\W", "", words[0]).lower() in ("dr", "dra", "doctor", "doctora")
    if title and len(words) > 2:
        return words[0] + " " + words[-1]
    return " ".join(words[:2]) if title else words[0]


def write(kind: str, channel: str, what: str, fix: str, topic: str, name: str, clinic_name: str) -> dict:
    """One message: its subject (only for email) and its text."""
    hello = "Hola, buenos días" + (f", {name}" if name else "")
    if kind == "follow_up":
        body = [f"Os escribí hace unos días con un vídeo sobre {topic}: una propuesta y un prototipo. ¿Pudisteis verlo?",
                "Si os encaja, lo vemos en una llamada corta. Y si ahora no es el momento, decídmelo sin problema."]
        subject = f"Re: Propuesta para {clinic_name}"
    else:
        body = [WHO, what, f"Os he preparado una propuesta para {fix}. {VIDEO_LINE}",
                "Si os interesa, lo vemos en una llamada corta esta semana."]
        subject = f"Propuesta para {clinic_name}: {topic}"
    if channel == "email":
        return {"subject": subject, "text": hello + ":\n\n" + "\n\n".join(body) + "\n\nUn saludo,\n" + SIGNATURE}
    return {"subject": "", "text": hello + ". " + "\n\n".join(body)}


def drafts(clinic: dict, people: list[dict], history: list[dict]) -> dict:
    """Every draft of the message to a clinic, ready to show on its card.

    ready is False when the X-ray is missing or not sure enough to write from; why says what to do first.
    angle is what of mine the proposal is about, from its hole. kind is the message that comes next: the
    proposal, or the follow-up once I have sent it. history is newest first.
    """
    xray = clinic.get("xray")
    if not xray:
        return {"ready": False, "why": "Primero haz su radiografía rápida: la propuesta sale de su agujero más grande."}
    hole = xray.get("hole")
    key = hole["key"] if hole else "none"
    if key in NOT_READY:
        return {"ready": False, "why": NOT_READY[key]}

    what, fix, topic = seen(clinic, key)
    name = greeting_name(people)
    sent = any(entry.get("kind") in ("message_sent", "audit_sent", "proposal_sent") for entry in history)
    return {
        "ready": True, "why": "",
        "angle": ANGLES[angle_of(key)],
        "kind": "follow_up" if sent else "proposal",
        "hole": hole["label"] if hole else "Sin agujeros a la vista",
        "to": name,
        "kinds": {key: {"label": label, "contact": contact} for key, (label, contact) in KINDS.items()},
        "channels": CHANNELS,
        "texts": {kind: {channel: write(kind, channel, what, fix, topic, name, clinic["name"]) for channel in CHANNELS}
                  for kind in KINDS},
    }
