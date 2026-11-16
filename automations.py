"""The Automations agent: for one clinic, a WhatsApp that answers and books by itself.

My videos promise three things to a clinic with no WhatsApp or no online booking:
1) a WhatsApp that answers and books by itself, at any hour;
2) connected to the clinic's diary, with no double bookings and no lost slots;
3) a reminder the day before.

Here Atelier leaves that service ready for one clinic: its card (treatments and opening hours), a
rehearsal with a pretend patient and a pretend diary, and the words that will go to n8n.

Nothing in this file sends a real message, calls the Claude API or costs money. The rehearsal runs on
rules, on this computer: the diary part (which slots are free, never two bookings at once) is exact,
and the language part is simple on purpose. In n8n a model does the language and the same diary rules
stay as they are. A service talks to real patients only after I say yes.
"""

import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import get_connection

router = APIRouter()

STATIC_DIR = Path(__file__).parent / "static"

# A service belongs to a clinic of the CRM, or to an invented clinic with only a name (for trying things).
# config is the service's card and chat is where the rehearsal conversation stands; both are JSON.
# tested_at and reminder_tested_at say the rehearsal booked an appointment and showed its reminder.
# approved_at is my yes. Changing the card clears the three: what I approved must be what I tried.
SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id INTEGER UNIQUE REFERENCES clinics(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    chat TEXT NOT NULL DEFAULT '{}',
    tested_at TEXT,
    reminder_tested_at TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The pretend diary of a rehearsal. source is 'chat' (the pretend patient booked it) or 'hand'
-- (I filled the slot myself, to see that the assistant no longer offers it).
CREATE TABLE IF NOT EXISTS automation_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES automation_services(id) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    treatment TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'chat',
    reminded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The rehearsal conversation. who is 'patient', 'assistant' or 'reminder'. slots holds the slots the
-- assistant offered in that message (JSON). for_team marks what the assistant left for a person.
CREATE TABLE IF NOT EXISTS automation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES automation_services(id) ON DELETE CASCADE,
    who TEXT NOT NULL,
    text TEXT NOT NULL,
    slots TEXT NOT NULL DEFAULT '[]',
    for_team INTEGER NOT NULL DEFAULT 0,
    said_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS automation_bookings_by_service ON automation_bookings(service_id);
CREATE INDEX IF NOT EXISTS automation_messages_by_service ON automation_messages(service_id);
"""

DayKey = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DAY_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# The visit a patient gets when she does not know which treatment she wants.
FIRST_VISIT = "una primera valoración"
# Slots start on the hour and on the half hour.
STEP = timedelta(minutes=30)
# How far ahead the assistant looks for a slot.
LOOK_AHEAD_DAYS = 21
# From this hour on, a slot counts as "in the afternoon".
AFTERNOON = time(15, 0)

DEFAULT_HOURS = {"mon": "10:00-14:00, 16:00-20:00", "tue": "10:00-14:00, 16:00-20:00",
                 "wed": "10:00-14:00, 16:00-20:00", "thu": "10:00-14:00, 16:00-20:00",
                 "fri": "10:00-14:00, 16:00-20:00", "sat": "", "sun": ""}

# Words that do not tell one treatment from another. "primera" is here because a patient also says
# "la primera" to pick a slot.
SMALL_WORDS = {"un", "una", "unos", "unas", "de", "del", "el", "la", "los", "las", "con", "para", "por", "en", "y",
               "primera", "primer", "sesion", "tratamiento", "cita"}


class Treatment(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    minutes: int = Field(default=30, ge=10, le=240)


class Card(BaseModel):
    """A service's card: what the assistant needs to know about the clinic to give appointments."""
    treatments: list[Treatment] = Field(min_length=1, max_length=30)
    hours: dict[DayKey, str]  # as I type them: "10:00-14:00, 16:00-20:00", or nothing for a closed day
    notice_hours: int = Field(default=2, ge=0, le=72)  # the soonest a slot can be, counted from now
    reminder_time: str = Field(default="10:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class NewService(BaseModel):
    clinic_id: int | None = None
    name: str = Field(default="", max_length=120)


class Said(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    clock: str = Field(default="", pattern=r"^(([01]\d|2[0-3]):[0-5]\d)?$")  # pretend she writes at this time today


class HandBooking(BaseModel):
    starts_at: datetime
    minutes: int = Field(default=30, ge=10, le=240)
    treatment: str = Field(default="", max_length=60)


def init_tables() -> None:
    with get_connection() as connection:
        connection.executescript(SCHEMA)


# ---------- Small helpers with words and dates ----------

def plain(text: str) -> str:
    """Lowercase and without accents, so "Miércoles" and "miercoles" are the same word."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(letter for letter in decomposed if unicodedata.category(letter) != "Mn")


def parse_spans(typed: str) -> list[list[str]]:
    """Turn "10:00-14:00, 16:00-20:00" into [["10:00", "14:00"], ["16:00", "20:00"]]. Nothing means closed."""
    text = plain(typed).replace("cerrado", "").strip()
    if not text:
        return []
    spans = []
    for part in re.split(r"[,;y]", text):
        found = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*(?:-|–|a)\s*(\d{1,2})[:.](\d{2})\s*", part)
        if not found:
            raise ValueError("Escribe el horario así: 10:00-14:00, 16:00-20:00. Déjalo vacío si ese día cierra.")
        open_h, open_m, close_h, close_m = (int(number) for number in found.groups())
        if open_h > 23 or close_h > 23 or open_m > 59 or close_m > 59:
            raise ValueError("Hay una hora que no existe en el horario.")
        opens, closes = time(open_h, open_m), time(close_h, close_m)
        if opens >= closes or (spans and opens.strftime("%H:%M") < spans[-1][1]):
            raise ValueError("En el horario, cada tramo tiene que empezar después de que acabe el anterior.")
        spans.append([opens.strftime("%H:%M"), closes.strftime("%H:%M")])
    return spans


def spans_text(spans: list[list[str]]) -> str:
    return ", ".join(f"{opens}-{closes}" for opens, closes in spans)


def at_time(day: date, clock: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(clock))


def short(moment: datetime, now: datetime) -> str:
    """A slot as a button: "Jue 17:00" this week, "Jue 26 nov 17:00" when it is further away."""
    name = DAY_SHORT[moment.weekday()]
    if (moment.date() - now.date()).days < 7:
        return f"{name} {moment:%H:%M}"
    return f"{name} {moment.day} {MONTHS[moment.month - 1]} {moment:%H:%M}"


def spoken(moment: datetime, now: datetime) -> str:
    """A slot as someone says it: "el jueves a las 17:00", "mañana a las 10:30", "el jueves 26 a las 17:00"."""
    days = (moment.date() - now.date()).days
    hour = f"a las {moment:%H:%M}" if moment.hour != 1 else f"a la {moment:%H:%M}"
    if days == 0:
        return f"hoy {hour}"
    if days == 1:
        return f"mañana {hour}"
    if days < 7:
        return f"el {DAY_NAMES[moment.weekday()]} {hour}"
    return f"el {DAY_NAMES[moment.weekday()]} {moment.day} de {MONTHS[moment.month - 1]} {hour}"


# ---------- The diary: which slots are free ----------

def busy_spans(connection, service_id: int, skip: int | None = None) -> list[tuple[datetime, datetime]]:
    """Every booking in the pretend diary as (start, end). skip leaves one out: the one being moved."""
    rows = connection.execute(
        "SELECT id, starts_at, minutes FROM automation_bookings WHERE service_id = ?", (service_id,)).fetchall()
    spans = []
    for row in rows:
        if row["id"] != skip:
            start = datetime.fromisoformat(row["starts_at"])
            spans.append((start, start + timedelta(minutes=row["minutes"])))
    return spans


def is_free(start: datetime, minutes: int, busy: list[tuple[datetime, datetime]]) -> bool:
    end = start + timedelta(minutes=minutes)
    return all(end <= taken_from or start >= taken_to for taken_from, taken_to in busy)


def inside_hours(config: dict, start: datetime, minutes: int) -> bool:
    """True when the whole visit fits in one stretch of that day's opening hours."""
    end = start + timedelta(minutes=minutes)
    for opens, closes in config["hours"][DAYS[start.weekday()]]:
        if at_time(start.date(), opens) <= start and end <= at_time(start.date(), closes):
            return True
    return False


def free_slots(config: dict, busy: list, minutes: int, first: datetime, last: datetime) -> list[datetime]:
    """Every free start between two moments, in order. A slot is free when the whole visit fits inside
    the opening hours and touches no other booking: that is what keeps the diary without doubles."""
    slots = []
    day = first.date()
    while day <= last.date():
        for opens, closes in config["hours"][DAYS[day.weekday()]]:
            start, closing = at_time(day, opens), at_time(day, closes)
            while start + timedelta(minutes=minutes) <= closing:
                if first <= start <= last and is_free(start, minutes, busy):
                    slots.append(start)
                start += STEP
        day += timedelta(days=1)
    return slots


def three_of(slots: list[datetime]) -> list[datetime]:
    """Three slots that are a real choice: two on the first day, far apart, and one on another day."""
    if len(slots) <= 3:
        return slots
    first = slots[0]
    picks = [first]
    later = next((slot for slot in slots
                  if slot.date() == first.date() and slot - first >= timedelta(hours=4)), None)
    if later:
        picks.append(later)
    other_days = [slot for slot in slots if slot.date() > first.date()]
    if other_days:
        hours_taken = {pick.time() for pick in picks}
        picks.append(next((slot for slot in other_days if slot.time() not in hours_taken), other_days[0]))
    for slot in slots:
        if len(picks) == 3:
            break
        if slot not in picks:
            picks.append(slot)
    return sorted(picks)


# ---------- Understanding the patient, with simple rules ----------

def treatment_in(text: str, config: dict) -> tuple[dict | None, list[dict]]:
    """The treatment a message names, from the clinic's own list.

    Returns (treatment, []) when one is clear, (None, [several]) when the words fit more than one, and
    (None, []) when it names none. A treatment is named when at least half of its words are there.
    """
    words = re.findall(r"[a-z0-9]+", plain(text))
    scored = []
    for treatment in config["treatments"]:
        keys = [word for word in re.findall(r"[a-z0-9]+", plain(treatment["name"])) if word not in SMALL_WORDS]
        hits = [key for key in keys
                if any(word.startswith(key[:max(4, len(key) - 2)]) for word in words)]
        if hits and len(hits) * 2 >= len(keys) and max(len(hit) for hit in hits) >= 4:
            scored.append((len(hits) / len(keys), len(hits), treatment))
    if not scored:
        return None, []
    top = max(scored, key=lambda entry: entry[:2])[:2]
    best = [treatment for share, hits, treatment in scored if (share, hits) == top]
    if len(best) == 1:
        return best[0], []
    return None, best


def first_visit(config: dict) -> dict | None:
    return next((treatment for treatment in config["treatments"] if "valoracion" in plain(treatment["name"])), None)


def clock_in(text: str) -> time | None:
    """The time of day a message names: "17:00", "a las 17", "a las 5 de la tarde". None when it names none."""
    found = re.search(r"\b(\d{1,2})[:.h](\d{2})\b", text)
    if found:
        hour, minute = int(found.group(1)), int(found.group(2))
    else:
        found = re.search(r"\ba las? (\d{1,2})(?: y (media|cuarto))?\b", text) or re.search(r"\b(\d{1,2}) ?h\b", text)
        if not found:
            return None
        hour = int(found.group(1))
        extra = found.group(2) if found.lastindex and found.lastindex >= 2 else None
        minute = 30 if extra == "media" else 15 if extra == "cuarto" else 0
    # Nobody books a clinic at five in the morning: "a las 5" is the afternoon.
    if hour < 8 or (hour < 12 and re.search(r"de la tarde|por la tarde", text)):
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def weekdays_in(text: str) -> set[int]:
    """The days of the week a message names, as numbers: Monday is 0."""
    days = set()
    for number, name in enumerate(DAY_NAMES):
        if re.search(rf"\b{plain(name)}\b", text) or re.search(rf"\b{plain(DAY_SHORT[number])}\b", text):
            days.add(number)
    return days


def wish_in(text: str, now: datetime) -> dict:
    """When the patient would like to come: days of the week, a stretch of dates and a part of the day.

    "mañana" means two things in Spanish. "por la mañana" is the morning; what is left is tomorrow.
    """
    wish = {"weekdays": weekdays_in(text), "first": None, "last": None, "part": None}
    morning = r"\b(?:por|de|a) las? mananas?\b|\bprimera hora\b|\btemprano\b"
    if re.search(morning, text):
        wish["part"] = "morning"
        text = re.sub(morning, " ", text)
    elif re.search(r"\b(?:por|de|a) las? tardes?\b|\bultima hora\b|\bdespues de comer\b|\bal salir del? trabaj", text):
        wish["part"] = "afternoon"
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    if re.search(r"\bpasado manana\b", text):
        wish["first"] = wish["last"] = today + timedelta(days=2)
    elif re.search(r"\bmanana\b", text):
        wish["first"] = wish["last"] = today + timedelta(days=1)
    elif re.search(r"\bhoy\b", text):
        wish["first"] = wish["last"] = today
    elif re.search(r"\bsemana que viene\b|\bproxima semana\b|\bsemana proxima\b|\bsemana siguiente\b", text):
        wish["first"], wish["last"] = monday + timedelta(days=7), monday + timedelta(days=13)
    elif re.search(r"\besta semana\b", text):
        wish["first"], wish["last"] = today, monday + timedelta(days=6)
    return wish


def has_wish(wish: dict) -> bool:
    return bool(wish["weekdays"] or wish["first"] or wish["part"])


def fits(slot: datetime, wish: dict, without_days: set[int] = frozenset()) -> bool:
    if slot.weekday() in without_days:
        return False
    if wish["weekdays"] and slot.weekday() not in wish["weekdays"]:
        return False
    if wish["first"] and not wish["first"] <= slot.date() <= wish["last"]:
        return False
    if wish["part"] == "morning" and slot.time() >= AFTERNOON:
        return False
    if wish["part"] == "afternoon" and slot.time() < AFTERNOON:
        return False
    return True


def choice_in(text: str, offered: list[datetime], now: datetime) -> list[datetime]:
    """Which of the offered slots the patient picks. One slot is a clear answer; several, a doubt.

    She can pick by its time ("a las 17:00"), by its day ("el jueves", "mañana", "el día 26"), or by its
    place in the list ("la segunda").
    """
    clock = clock_in(text)
    wish = wish_in(text, now)
    number = re.search(rf"\b(?:dia|{'|'.join(plain(name) for name in DAY_NAMES)}) (\d{{1,2}})\b(?![:.h\d])", text)
    if clock or number or has_wish(wish):
        return [slot for slot in offered
                if (not clock or slot.time() == clock)
                and (not number or slot.day == int(number.group(1)))
                and fits(slot, wish)]
    order = None
    if re.fullmatch(r"\W*(?:(?:el|la|opcion|numero)\s+)?([1-3])\W*", text):
        order = int(re.search(r"[1-3]", text).group()) - 1
    elif re.search(r"\bprimer[oa]?\b", text):
        order = 0
    elif re.search(r"\bsegund[oa]\b", text):
        order = 1
    elif re.search(r"\btercer[oa]?\b", text):
        order = 2
    elif re.search(r"\bultim[oa]\b", text):
        order = len(offered) - 1
    if order is not None and 0 <= order < len(offered):
        return [offered[order]]
    return []


# ---------- The assistant's turn ----------

class Turn:
    """One turn of the assistant: what it says back, and how the conversation stands afterwards."""

    def __init__(self, connection, service: dict, now: datetime):
        self.connection = connection
        self.service = service
        self.config = service["config"]
        self.state = service["chat"]
        self.now = now
        self.clinic = service["clinic_name"]
        self.replies: list[dict] = []
        self.booked = False

    def say(self, text: str, slots: list[datetime] | None = None, for_team: bool = False) -> None:
        self.replies.append({"text": text, "slots": slots or [], "for_team": for_team})

    def offered(self) -> list[datetime]:
        return [datetime.fromisoformat(slot) for slot in self.state.get("offered", [])]

    def treatment(self) -> dict | None:
        name = self.state.get("treatment")
        return next((entry for entry in self.config["treatments"] if entry["name"] == name), None)

    def my_booking(self):
        """The pretend patient's next appointment, if she has one."""
        return self.connection.execute(
            "SELECT * FROM automation_bookings WHERE service_id = ? AND source = 'chat' AND starts_at >= ?"
            " ORDER BY starts_at LIMIT 1", (self.service["id"], self.now.isoformat(timespec="minutes"))).fetchone()

    def offer(self, wish: dict, after: datetime | None = None, without_days: set[int] = frozenset(),
              opening: str = "") -> None:
        """Look in the diary and offer three slots for the treatment being talked about."""
        treatment = self.treatment()
        busy = busy_spans(self.connection, self.service["id"], skip=self.state.get("moving"))
        first = self.now + timedelta(hours=self.config["notice_hours"])
        if after:
            first = max(first, after)
        last = self.now + timedelta(days=LOOK_AHEAD_DAYS)
        free = free_slots(self.config, busy, treatment["minutes"], first, last)
        wanted = [slot for slot in free if fits(slot, wish, without_days)]
        if wanted:
            start = opening or f"Para {treatment['name']} tengo estos huecos:"
        elif free:
            # Nothing where she asked: say so and offer the nearest ones, instead of leaving her with nothing.
            wanted = [slot for slot in free if slot.weekday() not in without_days] or free
            start = "Ahí no me queda ningún hueco. Los más cercanos que tengo son estos:"
        else:
            self.state["offered"] = []
            self.say(f"Ahora mismo no veo huecos libres en las próximas semanas. Le dejo tu mensaje al equipo de "
                     f"{self.clinic} y te escriben en cuanto abran.", for_team=True)
            return
        slots = three_of(wanted)
        self.state["offered"] = [slot.isoformat(timespec="minutes") for slot in slots]
        self.say(start, slots)

    def book(self, slot: datetime) -> None:
        """Write the appointment in the diary. It looks again first: a slot offered a while ago may be gone."""
        treatment = self.treatment()
        moving = self.state.get("moving")
        busy = busy_spans(self.connection, self.service["id"], skip=moving)
        if not (is_free(slot, treatment["minutes"], busy) and inside_hours(self.config, slot, treatment["minutes"])):
            self.offer(wish_in("", self.now), opening="Ese hueco se acaba de ocupar. Tengo estos otros:")
            return
        if moving:
            self.connection.execute("DELETE FROM automation_bookings WHERE id = ?", (moving,))
        self.connection.execute(
            "INSERT INTO automation_bookings (service_id, starts_at, minutes, treatment, source)"
            " VALUES (?, ?, ?, ?, 'chat')",
            (self.service["id"], slot.isoformat(timespec="minutes"), treatment["minutes"], treatment["name"]))
        days = (slot.date() - self.now.date()).days
        done = "Hecho: he cambiado tu cita. Te esperamos" if moving else "Hecho: te esperamos"
        text = f"{done} {spoken(slot, self.now)}."
        if days > 1:
            text += " El día antes te mandamos un recordatorio."
        self.state.update(offered=[], moving=None)
        self.booked = True
        self.say(text)

    def ask_which(self, slots: list[datetime]) -> None:
        both = " o ".join(spoken(slot, self.now) for slot in slots)
        self.state["offered"] = [slot.isoformat(timespec="minutes") for slot in slots]
        self.say(f"¿Cuál prefieres: {both}?", slots)

    def at_clock(self, clock: time, wish: dict, offered: list[datetime]) -> None:
        """She asks for a time that was not on the table ("el jueves a las 17:00"): if the diary has it
        free, it is hers. With no day in her words, it means one of the days already on the table."""
        treatment = self.treatment()
        busy = busy_spans(self.connection, self.service["id"], skip=self.state.get("moving"))
        first = self.now + timedelta(hours=self.config["notice_hours"])
        free = [slot for slot in free_slots(self.config, busy, treatment["minutes"], first,
                                            self.now + timedelta(days=LOOK_AHEAD_DAYS)) if slot.time() == clock]
        if has_wish(wish):
            found = [slot for slot in free if fits(slot, wish)][:1]  # the nearest day that fits her words
        else:
            days = {slot.date() for slot in offered}
            found = [slot for slot in free if slot.date() in days]
        if len(found) == 1:
            self.book(found[0])
        elif found:
            self.ask_which(found[:3])
        else:
            self.offer(wish, opening="A esa hora no me queda hueco. Los más cercanos que tengo son estos:")

    def ask_treatment(self, among: list[dict] | None = None) -> None:
        names = [entry["name"] for entry in (among or self.config["treatments"])]
        listed = ", ".join(names[:-1]) + (" o " if len(names) > 1 else "") + names[-1]
        hello = "" if self.state.get("greeted") else f"¡Hola! Soy el asistente de {self.clinic}. "
        self.state.update(asked_treatment=True, greeted=True)
        if among:
            self.say(f"{hello}¿Cuál de estos sería: {listed}?")
        elif len(names) <= 8:
            self.say(f"{hello}¿Para qué tratamiento quieres la cita? Puedo reservarte: {listed}.")
        else:
            self.say(f"{hello}¿Para qué tratamiento quieres la cita?")

    def to_team(self, opening: str) -> None:
        self.state["greeted"] = True
        self.say(f"{opening} Le dejo tu mensaje al equipo de {self.clinic} y te escriben en cuanto abran. "
                 "Si quieres una cita, dime para qué tratamiento y te busco hueco.", for_team=True)

    def answer(self, said: str) -> None:
        text = plain(said)
        offered = self.offered()
        wish = wish_in(text, self.now)

        # 1. She wants to cancel.
        if re.search(r"\bcancel|\banul|\bno (?:voy a poder|puedo) (?:ir|acudir|asistir)\b|\bno podre ir\b", text):
            booking = self.my_booking()
            self.state.update(offered=[], moving=None)
            if booking is None:
                self.say("No veo ninguna cita a tu nombre. Si quieres una, dime para qué tratamiento y te busco hueco.")
                return
            when = spoken(datetime.fromisoformat(booking["starts_at"]), self.now)
            self.connection.execute("DELETE FROM automation_bookings WHERE id = ?", (booking["id"],))
            self.state["treatment"] = booking["treatment"]
            self.say(f"Hecho: he cancelado tu cita de {when}. El hueco queda libre para otra persona. "
                     "Si quieres, te busco otro día.")
            return

        # 2. She wants to move the appointment she has.
        if re.search(r"\bcambi|\bmover\b|\bmuev|\breprogram|\baplaz|\botro dia para mi cita\b", text):
            booking = self.my_booking()
            if booking is not None:
                self.state.update(moving=booking["id"], treatment=booking["treatment"])
                self.offer(wish, opening="Claro, te la cambio. Tengo estos huecos:")
                return

        # 3. She names a treatment: remember it, or ask which one when the words fit several.
        named, several = treatment_in(said, self.config)
        if named:
            self.state["treatment"] = named["name"]
        elif several and not self.treatment():
            self.ask_treatment(several)
            return
        elif self.state.get("asked_treatment") and not self.treatment():
            visit = first_visit(self.config)
            if visit and re.search(r"\bno (?:lo )?se\b|\bno (?:lo )?tengo claro\b|\bvaloracion\b|\bcualquier", text):
                self.state["treatment"] = visit["name"]

        # 4. What the assistant never answers by itself: prices and anything medical go to a person.
        if re.search(r"\bprecio|\bcuanto (?:cuesta|vale|sale|es|cobr)|\bcuesta\b|\btarifa|\bpresupuesto|\bfinanci|\boferta", text):
            self.to_team("Los precios te los confirma el equipo, porque dependen de cada caso.")
            return
        if re.search(r"\bduele|\bdolor|\befectos?\b|\briesgo|\bcontraindic|\bembaraz|\blactancia|\balergi|\bmedica"
                     r"|\brecupera|\bhincha|\bmoraton|\breaccion|\binfeccion|\bes seguro\b|\bme ha salido\b", text):
            self.to_team("No puedo darte consejo médico: eso te lo explica el equipo, que es quien te va a tratar.")
            return

        # 5. She answers to the slots on the table.
        if offered:
            no = re.search(r"\bno puedo\b|\bno me (?:viene|va|cuadra)\b|\bningun|\bimposible\b|\botros?\b|\botra\b"
                           r"|\bmas adelante\b|\bmas tarde\b", text)
            picked = [] if no else choice_in(text, offered, self.now)
            if len(picked) == 1:
                self.book(picked[0])
                return
            if 1 < len(picked) < len(offered) or (len(picked) > 1 and clock_in(text)):
                self.ask_which(picked)
                return
            if clock_in(text) and not no:
                self.at_clock(clock_in(text), wish, offered)
                return
            if no or has_wish(wish):
                without, later = set(), None
                if no:
                    # A no turns the wish around: "no puedo el jueves" takes Thursdays away, "no puedo
                    # mañana" looks from the day after, "por la mañana no" leaves the afternoons.
                    without, wish["weekdays"] = wish["weekdays"], set()
                    if wish["part"]:
                        wish["part"] = "afternoon" if wish["part"] == "morning" else "morning"
                    if wish["first"]:
                        later = datetime.combine(wish["last"] + timedelta(days=1), time.min)
                        wish["first"] = wish["last"] = None
                if not (has_wish(wish) or without or later):
                    later = max(offered) + STEP
                self.offer(wish, after=later, without_days=without,
                           opening="Sin problema. ¿Te viene mejor alguno de estos?")
                return

        # 6. She asks for an appointment, or she has just said the treatment.
        wants = re.search(r"\bcita\b|\bhueco|\breserv|\bturno\b|\bdisponib|\bpedir hora\b|\bcoger hora\b|\bagend"
                          r"|\bbusca|\bquiero\b|\bquisiera\b|\bme gustaria\b|\bnecesito\b|\bpuedo ir\b", text)
        if self.treatment() and (wants or named or has_wish(wish) or self.state.get("asked_treatment")):
            hello = "" if self.state.get("greeted") else f"¡Hola! Soy el asistente de {self.clinic}. "
            self.state.update(asked_treatment=False, greeted=True)
            self.offer(wish, opening=f"{hello}Para {self.treatment()['name']} tengo estos huecos:")
            return
        if wants or has_wish(wish):
            self.ask_treatment()
            return

        # 7. Small talk, and everything else: when in doubt, a person answers.
        if re.search(r"\bgracias\b|\bperfecto\b|\bgenial\b|\bvale\b|\bok\b|\bde acuerdo\b|\bhasta (?:luego|pronto|manana)\b|\badios\b", text):
            self.say("¡A ti! Aquí estoy para lo que necesites.")
            return
        if re.fullmatch(r"\W*(?:hola|buenas|buenos dias|buenas tardes|buenas noches|hey|holi)\W*", text):
            self.ask_treatment()
            return
        self.to_team("Eso prefiero que te lo conteste una persona.")


# ---------- Reading and saving a service ----------

SERVICE_QUERY = """
SELECT automation_services.*, clinics.name AS crm_name, clinics.treatments AS crm_treatments,
       clinics.stage AS crm_stage
FROM automation_services LEFT JOIN clinics ON clinics.id = automation_services.clinic_id
"""


def default_config(typed_treatments: str = "") -> dict:
    """A card to start from: a first visit, plus the treatments already written on the clinic's CRM card."""
    names = [FIRST_VISIT]
    for name in re.split(r"[,;\n/]+", typed_treatments):
        name = name.strip(" .").lower()
        if 2 <= len(name) <= 60 and name not in names:
            names.append(name)
    return {"treatments": [{"name": name, "minutes": 30} for name in names[:30]],
            "hours": {day: parse_spans(typed) for day, typed in DEFAULT_HOURS.items()},
            "notice_hours": 2, "reminder_time": "10:00"}


def checklist(service: dict) -> list[dict]:
    """What a service needs before I can say yes, in order. Each step is done or not, nothing in between."""
    config = service["config"]
    return [
        {"key": "card", "label": "La ficha: tratamientos y horario",
         "done": bool(config["treatments"]) and any(config["hours"][day] for day in DAYS)},
        {"key": "tested", "label": "Un ensayo que termina con una cita en la agenda", "done": bool(service["tested_at"])},
        {"key": "reminder", "label": "El recordatorio del día antes, visto", "done": bool(service["reminder_tested_at"])},
        {"key": "approved", "label": "Tu sí", "done": bool(service["approved_at"])},
    ]


def state_of(service: dict) -> str:
    steps = {step["key"]: step["done"] for step in checklist(service)}
    if steps["approved"]:
        return "approved"
    if steps["card"] and steps["tested"] and steps["reminder"]:
        return "ready"
    return "draft"


def as_service(row) -> dict:
    service = dict(row)
    service["config"] = json.loads(service["config"])
    service["chat"] = json.loads(service["chat"])
    service["clinic_name"] = service.pop("crm_name") or service["name"]
    service["invented"] = service["clinic_id"] is None
    service["checklist"] = checklist(service)
    service["state"] = state_of(service)
    del service["crm_treatments"], service["name"]
    return service


def fetch_service(connection, service_id: int) -> dict:
    row = connection.execute(SERVICE_QUERY + " WHERE automation_services.id = ?", (service_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No encuentro ese servicio.")
    return as_service(row)


def save(connection, service: dict, **changes) -> None:
    """Save the conversation's state and any other column that changed."""
    values = {"chat": json.dumps(service["chat"], ensure_ascii=False), **changes}
    assignments = ", ".join(f"{column} = ?" for column in values)
    connection.execute(f"UPDATE automation_services SET {assignments}, updated_at = datetime('now') WHERE id = ?",
                       [*values.values(), service["id"]])


def instructions(service: dict) -> str:
    """What the assistant in n8n will be told about this clinic. The model does the language; the diary
    stays with two tools that follow the same rules as the rehearsal."""
    config = service["config"]
    treatments = "\n".join(f"- {entry['name']}: {entry['minutes']} minutos" for entry in config["treatments"])
    hours = "\n".join(f"- {DAY_NAMES[number]}: {spans_text(config['hours'][day]) or 'cerrado'}"
                      for number, day in enumerate(DAYS))
    return f"""Eres el asistente de {service['clinic_name']}, una clínica de medicina estética. Atiendes su WhatsApp a cualquier hora.

Qué haces: dar cita, cambiarla o cancelarla. Nada más.

Cómo hablas: en español, de tú, con mensajes cortos y amables. Te presentas una vez como «el asistente de {service['clinic_name']}». Nunca dices que eres una persona.

Reglas que no se rompen:
- Los huecos salen siempre de la herramienta buscar_huecos. Nunca inventes un día ni una hora.
- Ofrece tres huecos como mucho. Cuando la paciente elija uno, usa la herramienta reservar. Solo confirmas la cita si reservar dice que ha ido bien; si dice que el hueco ya no está, ofrece otros.
- No des precios, ni consejo médico, ni opiniones sobre un tratamiento. Di que se lo confirma el equipo y usa la herramienta avisar_al_equipo.
- Si no entiendes algo o la paciente está molesta, usa avisar_al_equipo y dile que le escribirán en cuanto abran.
- No pidas datos de salud. Para reservar basta el nombre y el tratamiento.
- Una cita no puede ser antes de {config['notice_hours']} horas desde ahora.

Tratamientos y lo que dura cada cita:
{treatments}

Horario de la clínica:
{hours}

Cuando la cita quede reservada, dile el día y la hora y que el día antes recibirá un recordatorio."""


def reminder_text(service: dict, starts_at: datetime) -> str:
    return (f"Hola, te recordamos tu cita de mañana, {DAY_NAMES[starts_at.weekday()]} a las {starts_at:%H:%M}, "
            f"en {service['clinic_name']}. Si no puedes venir, contesta a este mensaje y te buscamos otro hueco.")


def whole(connection, service: dict) -> dict:
    """A service with everything its page shows: the card as I type it, the rehearsal and the diary."""
    messages = connection.execute(
        "SELECT * FROM automation_messages WHERE service_id = ? ORDER BY id", (service["id"],)).fetchall()
    bookings = connection.execute(
        "SELECT * FROM automation_bookings WHERE service_id = ? ORDER BY starts_at", (service["id"],)).fetchall()
    now = datetime.now()
    service = dict(service)
    service["hours_text"] = {day: spans_text(service["config"]["hours"][day]) for day in DAYS}
    service["messages"] = []
    for row in messages:
        said_at = datetime.fromisoformat(row["said_at"])
        slots = [datetime.fromisoformat(slot) for slot in json.loads(row["slots"])]
        service["messages"].append({
            "id": row["id"], "who": row["who"], "text": row["text"], "for_team": bool(row["for_team"]),
            "time": f"{said_at:%H:%M}", "day": said_at.date().isoformat(),
            "slots": [{"label": short(slot, said_at),
                       "say": f"{spoken(slot, said_at)[0].upper()}{spoken(slot, said_at)[1:]}, por favor"}
                      for slot in slots],
        })
    service["bookings"] = [
        {"id": row["id"], "starts_at": row["starts_at"], "minutes": row["minutes"], "treatment": row["treatment"],
         "source": row["source"], "reminded": bool(row["reminded"]),
         "when": spoken(datetime.fromisoformat(row["starts_at"]), now)}
        for row in bookings]
    service["instructions"] = instructions(service)
    service["reminder_example"] = reminder_text(service, at_time(now.date() + timedelta(days=2), "17:00"))
    del service["chat"]
    return service


def add_message(connection, service_id: int, who: str, text: str, said_at: datetime,
                slots: list[datetime] | None = None, for_team: bool = False) -> None:
    connection.execute(
        "INSERT INTO automation_messages (service_id, who, text, slots, for_team, said_at) VALUES (?, ?, ?, ?, ?, ?)",
        (service_id, who, text, json.dumps([slot.isoformat(timespec="minutes") for slot in slots or []]),
         int(for_team), said_at.isoformat(timespec="seconds")))


# ---------- The page and its API ----------

@router.get("/automations", include_in_schema=False)
def automations_page():
    """The department's page. It loads its data from /api/automations."""
    return FileResponse(STATIC_DIR / "automations.html")


@router.get("/api/automations/services")
def list_services():
    with get_connection() as connection:
        rows = connection.execute(SERVICE_QUERY + " ORDER BY automation_services.id DESC").fetchall()
        services = []
        for row in rows:
            service = as_service(row)
            services.append({key: service[key] for key in
                             ("id", "clinic_id", "clinic_name", "invented", "state", "checklist", "updated_at")})
        # Clinics of the CRM that have no service yet, to pick one. Clients first, then the ones furthest along.
        clinics = connection.execute(
            "SELECT id, name, stage FROM clinics WHERE do_not_contact = 0"
            " AND id NOT IN (SELECT clinic_id FROM automation_services WHERE clinic_id IS NOT NULL)"
            " ORDER BY CASE stage WHEN 'client' THEN 0 WHEN 'proposal_sent' THEN 1 WHEN 'call_done' THEN 2"
            " WHEN 'call_booked' THEN 3 WHEN 'replied' THEN 4 ELSE 5 END, name").fetchall()
    return {"services": services, "clinics": [dict(row) for row in clinics]}


@router.post("/api/automations/services", status_code=201)
def create_service(new: NewService):
    """Start a service for a clinic of the CRM, or for an invented clinic with only a name."""
    with get_connection() as connection:
        typed = ""
        if new.clinic_id is not None:
            clinic = connection.execute("SELECT treatments FROM clinics WHERE id = ?", (new.clinic_id,)).fetchone()
            if clinic is None:
                raise HTTPException(status_code=404, detail="No encuentro esa clínica en el CRM.")
            if connection.execute("SELECT 1 FROM automation_services WHERE clinic_id = ?", (new.clinic_id,)).fetchone():
                raise HTTPException(status_code=409, detail="Esa clínica ya tiene su servicio.")
            typed = clinic["treatments"]
        elif not new.name.strip():
            raise HTTPException(status_code=422, detail="Elige una clínica o escribe un nombre inventado.")
        cursor = connection.execute(
            "INSERT INTO automation_services (clinic_id, name, config) VALUES (?, ?, ?)",
            (new.clinic_id, "" if new.clinic_id is not None else new.name.strip(),
             json.dumps(default_config(typed), ensure_ascii=False)))
        return whole(connection, fetch_service(connection, cursor.lastrowid))


@router.get("/api/automations/services/{service_id}")
def get_service(service_id: int):
    with get_connection() as connection:
        return whole(connection, fetch_service(connection, service_id))


@router.put("/api/automations/services/{service_id}/card")
def save_card(service_id: int, card: Card):
    """Save the card. What I tried and approved was the old card, so the rehearsal starts again."""
    try:
        hours = {day: parse_spans(card.hours.get(day, "")) for day in DAYS}
    except ValueError as problem:
        raise HTTPException(status_code=422, detail=str(problem))
    if not any(hours.values()):
        raise HTTPException(status_code=422, detail="La clínica tiene que abrir al menos un día.")
    treatments, seen = [], set()
    for treatment in card.treatments:
        name = treatment.name.strip()
        if plain(name) in seen:
            raise HTTPException(status_code=422, detail=f"«{name}» está dos veces en los tratamientos.")
        seen.add(plain(name))
        treatments.append({"name": name, "minutes": treatment.minutes})
    config = {"treatments": treatments, "hours": hours,
              "notice_hours": card.notice_hours, "reminder_time": card.reminder_time}
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        if config != service["config"]:
            service["chat"] = {}
            connection.execute("DELETE FROM automation_messages WHERE service_id = ?", (service_id,))
            connection.execute("DELETE FROM automation_bookings WHERE service_id = ?", (service_id,))
            save(connection, service, config=json.dumps(config, ensure_ascii=False),
                 tested_at=None, reminder_tested_at=None, approved_at=None)
        return whole(connection, fetch_service(connection, service_id))


@router.post("/api/automations/services/{service_id}/say")
def say(service_id: int, said: Said):
    """The pretend patient writes, and the assistant answers. Nothing leaves this computer."""
    now = datetime.now().replace(second=0, microsecond=0)
    if said.clock:
        now = datetime.combine(now.date(), time.fromisoformat(said.clock))
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        add_message(connection, service_id, "patient", said.text.strip(), now)
        turn = Turn(connection, service, now)
        turn.answer(said.text)
        for reply in turn.replies:
            add_message(connection, service_id, "assistant", reply["text"], now, reply["slots"], reply["for_team"])
        changes = {"tested_at": now.isoformat(timespec="seconds")} if turn.booked and not service["tested_at"] else {}
        save(connection, service, **changes)
        return whole(connection, fetch_service(connection, service_id))


@router.post("/api/automations/services/{service_id}/reminders")
def show_reminders(service_id: int):
    """Pretend it is the day before each appointment: show the reminder the patient would get."""
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        now = datetime.now()
        rows = connection.execute(
            "SELECT * FROM automation_bookings WHERE service_id = ? AND source = 'chat' AND reminded = 0"
            " AND starts_at > ? ORDER BY starts_at", (service_id, now.isoformat(timespec="minutes"))).fetchall()
        if not rows:
            raise HTTPException(status_code=409, detail="Primero reserva una cita en el ensayo: sin cita no hay recordatorio.")
        sent_any = False
        for row in rows:
            starts_at = datetime.fromisoformat(row["starts_at"])
            sent = at_time(starts_at.date() - timedelta(days=1), service["config"]["reminder_time"])
            if sent <= now:
                continue  # booked too late for its reminder: the day before has already gone by
            add_message(connection, service_id, "reminder", reminder_text(service, starts_at), sent)
            connection.execute("UPDATE automation_bookings SET reminded = 1 WHERE id = ?", (row["id"],))
            sent_any = True
        if not sent_any:
            raise HTTPException(status_code=409, detail="La cita del ensayo es para hoy o mañana, y su recordatorio ya "
                                "no saldría. Reserva una para dentro de dos días o más.")
        changes = {} if service["reminder_tested_at"] else {"reminder_tested_at": now.isoformat(timespec="seconds")}
        save(connection, service, **changes)
        return whole(connection, fetch_service(connection, service_id))


@router.post("/api/automations/services/{service_id}/restart")
def restart(service_id: int):
    """Start the rehearsal again: an empty conversation and an empty pretend diary. The card stays."""
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        service["chat"] = {}
        connection.execute("DELETE FROM automation_messages WHERE service_id = ?", (service_id,))
        connection.execute("DELETE FROM automation_bookings WHERE service_id = ?", (service_id,))
        save(connection, service)
        return whole(connection, fetch_service(connection, service_id))


@router.post("/api/automations/services/{service_id}/bookings", status_code=201)
def fill_slot(service_id: int, booking: HandBooking):
    """Fill a slot of the pretend diary by hand, to see that the assistant stops offering it."""
    start = booking.starts_at.replace(second=0, microsecond=0, tzinfo=None)
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        if not is_free(start, booking.minutes, busy_spans(connection, service_id)):
            raise HTTPException(status_code=409, detail="Ese hueco ya está ocupado en la agenda de ensayo.")
        connection.execute(
            "INSERT INTO automation_bookings (service_id, starts_at, minutes, treatment, source)"
            " VALUES (?, ?, ?, ?, 'hand')",
            (service_id, start.isoformat(timespec="minutes"), booking.minutes, booking.treatment.strip()))
        return whole(connection, service)


@router.delete("/api/automations/bookings/{booking_id}")
def free_slot(booking_id: int):
    with get_connection() as connection:
        row = connection.execute("SELECT service_id FROM automation_bookings WHERE id = ?", (booking_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No encuentro esa cita.")
        connection.execute("DELETE FROM automation_bookings WHERE id = ?", (booking_id,))
        return whole(connection, fetch_service(connection, row["service_id"]))


@router.post("/api/automations/services/{service_id}/approve")
def approve(service_id: int):
    """My yes. It says the service is as I want it; it does not switch anything on."""
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        if service["state"] == "draft":
            missing = next(step["label"] for step in service["checklist"] if not step["done"])
            raise HTTPException(status_code=409, detail=f"Todavía falta: {missing[0].lower()}{missing[1:]}.")
        if service["state"] == "ready":
            save(connection, service, approved_at=datetime.now().isoformat(timespec="seconds"))
        return whole(connection, fetch_service(connection, service_id))


@router.post("/api/automations/services/{service_id}/unapprove")
def unapprove(service_id: int):
    with get_connection() as connection:
        service = fetch_service(connection, service_id)
        save(connection, service, approved_at=None)
        return whole(connection, fetch_service(connection, service_id))


@router.delete("/api/automations/services/{service_id}")
def delete_service(service_id: int):
    with get_connection() as connection:
        fetch_service(connection, service_id)
        connection.execute("DELETE FROM automation_services WHERE id = ?", (service_id,))
    return {"deleted": service_id}
