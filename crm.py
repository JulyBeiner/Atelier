"""The CRM: the clinics I want as clients, the people in them, every contact and what comes next.

The clinics and the people are real: they live only in the local database, never in the repository.
Atelier never sends a message by itself. I write to people; here I note what happened.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import clinic_web
import outreach
import video
import xray
from database import get_connection

router = APIRouter(prefix="/api/crm")

# Lists of clinics waiting to come in (files named clinics-*.json). Git ignores this folder.
IMPORT_DIR = Path(__file__).parent / "private"

Stage = Literal["to_contact", "contacted", "replied", "call_booked", "call_done",
                "proposal_sent", "client", "later", "not_interested"]
# The path a clinic walks, in order. "later" and "not_interested" are the two ways off the path.
PATH = ["to_contact", "contacted", "replied", "call_booked", "call_done", "proposal_sent", "client"]
# Stages where a conversation is going on: these always need a next step.
OPEN_STAGES = ("contacted", "replied", "call_booked", "call_done", "proposal_sent")

Kind = Literal["message_sent", "audit_sent", "reply", "call_booked", "call", "proposal_sent", "note"]
Channel = Literal["", "instagram", "whatsapp", "email", "phone", "linkedin", "in_person"]
ClinicKind = Literal["", "independent", "chain"]

# The stage each kind of contact takes a clinic to. A note moves nothing.
MOVES_TO = {
    "message_sent": "contacted",
    "audit_sent": "contacted",
    "reply": "replied",
    "call_booked": "call_booked",
    "call": "call_done",
    "proposal_sent": "proposal_sent",
}
# The kinds of contact that count as me writing to a clinic.
OUTREACH = ("message_sent", "audit_sent", "proposal_sent")

NUMBER_FIELDS = ("google_rating", "google_reviews", "fit")
# The social networks a clinic has a field for, and the start of a profile's address on each.
NETWORKS = {
    "instagram": "https://www.instagram.com/{}/",
    "tiktok": "https://www.tiktok.com/@{}",
    "x": "https://x.com/{}",
    "threads": "https://www.threads.net/@{}",
    "facebook": "https://www.facebook.com/{}",
    "linkedin": "https://www.linkedin.com/company/{}",
}

# A clinic with the day of the last real contact (notes and changes of stage are not contacts).
CLINIC_QUERY = """
SELECT clinics.*,
       (SELECT MAX(happened_on) FROM clinic_history
         WHERE clinic_history.clinic_id = clinics.id AND kind NOT IN ('note', 'stage')) AS last_contact_on
FROM clinics
"""


class ClinicUpdate(BaseModel):
    """What I can write on a clinic's card. Only the fields that are sent change."""
    name: str | None = Field(default=None, min_length=1, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=300)
    instagram: str | None = Field(default=None, max_length=300)
    tiktok: str | None = Field(default=None, max_length=300)
    x: str | None = Field(default=None, max_length=300)
    threads: str | None = Field(default=None, max_length=300)
    facebook: str | None = Field(default=None, max_length=300)
    linkedin: str | None = Field(default=None, max_length=300)
    kind: ClinicKind | None = None
    batch: str | None = Field(default=None, max_length=40)
    origin: str | None = Field(default=None, max_length=200)
    treatments: str | None = Field(default=None, max_length=1000)
    google_rating: float | None = Field(default=None, ge=0, le=5)
    google_reviews: int | None = Field(default=None, ge=0)
    fit: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=8000)
    do_not_contact: bool | None = None


class ClinicCreate(ClinicUpdate):
    name: str = Field(min_length=1, max_length=160)


class ClinicImport(ClinicCreate):
    """One clinic in a file of the private folder."""
    stage: Stage = "to_contact"
    source_url: str | None = Field(default=None, max_length=300)


class PersonUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    instagram: str | None = Field(default=None, max_length=300)
    prefers: Channel | None = None
    decides: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class PersonCreate(PersonUpdate):
    name: str = Field(min_length=1, max_length=120)


class Contact(BaseModel):
    """One line of the history. happened_on is today when it is left out."""
    kind: Kind
    channel: Channel = ""
    happened_on: date | None = None
    summary: str = Field(default="", max_length=6000)
    person_id: int | None = None
    # What I do next. Left out: the next step stays as it was. An empty text clears it.
    next_step: str | None = Field(default=None, max_length=300)
    next_step_on: date | None = None


class StageChange(BaseModel):
    stage: Stage
    reason: str = Field(default="", max_length=500)


class NextStep(BaseModel):
    next_step: str = Field(default="", max_length=300)
    next_step_on: date | None = None


def full_link(value: str) -> str:
    """A website as a link that opens: example.com becomes https://example.com."""
    if value and not re.match(r"https?://", value, re.IGNORECASE):
        value = "https://" + value
    return value


def profile_link(network: str, value: str) -> str:
    """A social profile as a full link, whether I typed @name, name or the link itself."""
    if not value or re.match(r"https?://", value, re.IGNORECASE):
        return value
    return NETWORKS[network].format(value.lstrip("@").strip("/"))


def clean(fields: dict) -> dict:
    """Tidy what was typed before saving it: no spaces around, links that open, yes/no as 1/0."""
    values = {}
    for key, value in fields.items():
        if key in NUMBER_FIELDS:
            values[key] = value
        elif key in ("do_not_contact", "decides"):
            values[key] = int(bool(value))
        else:
            values[key] = (value or "").strip()
    if "website" in values:
        values["website"] = full_link(values["website"])
    for network in NETWORKS:
        if network in values:
            values[network] = profile_link(network, values[network])
    if "name" in values and not values["name"]:
        raise HTTPException(status_code=422, detail="Falta el nombre.")
    return values


def insert(connection, table: str, values: dict) -> int:
    # The column names come from the models above, never from what someone typed.
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cursor = connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(values.values()))
    return cursor.lastrowid


def update(connection, table: str, row_id: int, values: dict) -> None:
    if not values:
        return
    assignments = ", ".join(f"{column} = ?" for column in values)
    connection.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", [*values.values(), row_id])


def as_clinic(row) -> dict:
    clinic = dict(row)
    clinic["do_not_contact"] = bool(clinic["do_not_contact"])
    clinic["web_scan"] = json.loads(clinic["web_scan"])
    clinic["xray_scan"] = json.loads(clinic["xray_scan"])
    # Worked out again each time from what was measured and what is on the card: it is instant.
    clinic["xray"] = xray.build(clinic)
    return clinic


def with_video(clinic: dict) -> dict:
    """The clinic with its video in a few words, for the lists: whether it has one, or why not yet."""
    return clinic | {"video": video.summary(clinic)}


def as_person(row) -> dict:
    person = dict(row)
    person["decides"] = bool(person["decides"])
    return person


def fetch_clinic(connection, clinic_id: int) -> dict:
    row = connection.execute(CLINIC_QUERY + " WHERE clinics.id = ?", (clinic_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    return as_clinic(row)


def fetch_person(connection, person_id: int) -> dict:
    row = connection.execute("SELECT * FROM clinic_people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return as_person(row)


def touch(connection, clinic_id: int) -> None:
    connection.execute("UPDATE clinics SET updated_at = datetime('now') WHERE id = ?", (clinic_id,))


def stage_after(current: str, kind: str) -> str:
    """The stage a clinic is in after a contact. It only moves forward along the path.

    A clinic left for later comes back to the path. One that said no, or is already a client, stays put.
    """
    target = MOVES_TO.get(kind)
    if target is None or current in ("client", "not_interested"):
        return current
    if current == "later" or PATH.index(target) > PATH.index(current):
        return target
    return current


def set_next_step(connection, clinic_id: int, text: str, day: date | None) -> None:
    """Write what I do next and when. A step without a day would never show up in "Hoy": it is refused."""
    text = text.strip()
    if text and day is None:
        raise HTTPException(status_code=422, detail="El próximo paso necesita un día.")
    if day is not None and not text:
        text = "Seguimiento"
    connection.execute(
        "UPDATE clinics SET next_step = ?, next_step_on = ?, updated_at = datetime('now') WHERE id = ?",
        (text, day.isoformat() if day else None, clinic_id),
    )


def clear_next_step(connection, clinic_id: int) -> None:
    connection.execute("UPDATE clinics SET next_step = '', next_step_on = NULL WHERE id = ?", (clinic_id,))


# ---- Clinics ----

@router.get("/clinics")
def list_clinics():
    """Every clinic, each with its video in a few words. There are few enough for the page to search and
    filter them by itself."""
    with get_connection() as connection:
        rows = connection.execute(CLINIC_QUERY + " ORDER BY clinics.name COLLATE NOCASE").fetchall()
    return [with_video(as_clinic(row)) for row in rows]


@router.post("/clinics", status_code=201)
def create_clinic(new_clinic: ClinicCreate):
    with get_connection() as connection:
        clinic_id = insert(connection, "clinics", clean(new_clinic.model_dump(exclude_unset=True)))
        return fetch_clinic(connection, clinic_id)


@router.get("/clinics/{clinic_id}")
def get_clinic(clinic_id: int):
    """One clinic with its people, its history (newest first), the drafts of the message to write to it
    and the state of its video."""
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
        people = connection.execute(
            "SELECT * FROM clinic_people WHERE clinic_id = ? ORDER BY decides DESC, name COLLATE NOCASE",
            (clinic_id,),
        ).fetchall()
        history = connection.execute(
            "SELECT clinic_history.*, clinic_people.name AS person_name FROM clinic_history"
            " LEFT JOIN clinic_people ON clinic_people.id = clinic_history.person_id"
            " WHERE clinic_history.clinic_id = ? ORDER BY happened_on DESC, clinic_history.id DESC",
            (clinic_id,),
        ).fetchall()
    people = [as_person(row) for row in people]
    history = [dict(row) for row in history]
    # The first message, written around its biggest hole. Atelier never sends it: I copy it.
    return clinic | {"people": people, "history": history, "drafts": outreach.drafts(clinic, people, history),
                     "video": video.status(clinic)}


@router.patch("/clinics/{clinic_id}")
def update_clinic(clinic_id: int, changes: ClinicUpdate):
    values = clean(changes.model_dump(exclude_unset=True))
    with get_connection() as connection:
        before = fetch_clinic(connection, clinic_id)
        if "website" in values and values["website"] != before["website"]:
            # A different website has not been read yet: what was known about the old one no longer counts.
            values |= {"website_check": "", "web_scan": "{}", "xray_scan": "{}"}
        update(connection, "clinics", clinic_id, values)
        if values.get("do_not_contact"):
            # No follow-ups for a clinic that asked me not to write.
            clear_next_step(connection, clinic_id)
        touch(connection, clinic_id)
        return fetch_clinic(connection, clinic_id)


@router.delete("/clinics/{clinic_id}")
def delete_clinic(clinic_id: int):
    """Erase a clinic with its people, its history and its video. For when someone asks me to delete their data."""
    with get_connection() as connection:
        fetch_clinic(connection, clinic_id)
        connection.execute("DELETE FROM clinics WHERE id = ?", (clinic_id,))
    video.forget(clinic_id)
    return {"deleted": True}


@router.post("/clinics/{clinic_id}/stage")
def change_stage(clinic_id: int, change: StageChange):
    """Move a clinic by hand. The change is noted in the history, so my numbers stay true."""
    reason = change.reason.strip()
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
        if change.stage == clinic["stage"]:
            return clinic
        connection.execute(
            "INSERT INTO clinic_history (clinic_id, happened_on, kind, summary, stage_to) VALUES (?, ?, 'stage', ?, ?)",
            (clinic_id, date.today().isoformat(), reason, change.stage),
        )
        connection.execute(
            "UPDATE clinics SET stage = ?, lost_reason = ?, updated_at = datetime('now') WHERE id = ?",
            (change.stage, reason if change.stage == "not_interested" else "", clinic_id),
        )
        if change.stage == "not_interested":
            # Nothing left to do with a clinic that said no.
            clear_next_step(connection, clinic_id)
        return fetch_clinic(connection, clinic_id)


@router.post("/clinics/{clinic_id}/next-step")
def change_next_step(clinic_id: int, step: NextStep):
    """Set, move or clear what I do next with a clinic."""
    with get_connection() as connection:
        fetch_clinic(connection, clinic_id)
        set_next_step(connection, clinic_id, step.next_step, step.next_step_on)
        return fetch_clinic(connection, clinic_id)


def save_reading(connection, clinic_id: int, found: dict | None, more: dict) -> list[str]:
    """Save what reading a clinic's website found, and the other columns in "more". Returns the fields filled.

    What I typed myself is never replaced: only fields that are empty right now are filled. Right now, because
    reading takes a while and I may have typed something on the card in the meantime.
    """
    values, filled = dict(more), []
    if found is not None:
        clinic = fetch_clinic(connection, clinic_id)
        values |= {"website_check": found["check"], "web_scan": json.dumps(found["scan"], ensure_ascii=False)}
        additions = {"website": found["website"]} | found["profiles"]
        filled = [field for field, value in additions.items() if value and not clinic[field]]
        values |= {field: additions[field] for field in filled}
    update(connection, "clinics", clinic_id, values)
    touch(connection, clinic_id)
    return filled


@router.post("/clinics/{clinic_id}/read-web")
def read_web(clinic_id: int):
    """Open the clinic's website, check that it is theirs and collect its social profiles. Free: no API.

    With no website in the CRM, it tries the addresses the clinic's name suggests and keeps one only when
    the page shows the clinic's phone or its street. Only empty fields are filled. "filled" lists what was
    added this time.
    """
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    found = clinic_web.look(clinic)  # the slow part, with the database closed
    with get_connection() as connection:
        filled = save_reading(connection, clinic_id, found, {})
        return fetch_clinic(connection, clinic_id) | {"filled": filled}


@router.post("/clinics/{clinic_id}/xray")
def take_xray(clinic_id: int):
    """The quick X-ray of a clinic: where it loses patients. Free: no API.

    It reads the clinic's website again (as "Leer su web" does, filling only empty fields) and asks Google
    PageSpeed how fast the website is on a phone. Both at once: half a minute or so. A clinic whose website
    I looked for and did not find is not opened at all. The X-ray itself comes with the clinic, in "xray".
    """
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    found, scan = xray.measure(clinic)  # the slow part, with the database closed
    with get_connection() as connection:
        filled = save_reading(connection, clinic_id, found, {"xray_scan": json.dumps(scan, ensure_ascii=False)})
        return fetch_clinic(connection, clinic_id) | {"filled": filled}


# ---- The video with the proposal ----

@router.get("/clinics/{clinic_id}/video")
def video_state(clinic_id: int):
    """Whether its video can be made, whether it is being made (and how far it is) and the last one made."""
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    return video.status(clinic)


@router.post("/clinics/{clinic_id}/video", status_code=202)
def make_video(clinic_id: int):
    """Make the clinic's video: a minute with what I saw, a prototype and an invitation to a call. Free: no API.

    It takes a minute or two and runs in the background: the card follows it by asking again. If another
    video is being made, this one waits its turn.
    """
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    return video.start(clinic)


@router.get("/videos")
def video_line():
    """How the line of videos goes: the one being made and how far it is, the ones waiting, and the batch."""
    return video.line()


@router.post("/videos", status_code=202)
def make_missing_videos():
    """Make, one after another, every video that is missing. Free: no API.

    It takes the clinics I may still write a first message to whose video can be made and that have none,
    an old one (the X-ray is newer) or one that failed. The ones that fit me best go first, as in "Hoy".
    About a minute and a half each, in the background: it goes on with the page closed, not if the panel stops.
    """
    with get_connection() as connection:
        rows = connection.execute(
            CLINIC_QUERY + " ORDER BY fit DESC, kind = 'chain', clinics.id").fetchall()
    jobs = []
    for clinic in (as_clinic(row) for row in rows):
        job = video.plan(clinic)
        if video.summary(clinic, job)["wanted"]:
            jobs.append(job)
    video.queue(jobs)
    return video.line()


@router.delete("/videos")
def stop_videos():
    """Stop the line: the video being made now finishes, the waiting ones are not made."""
    video.stop()
    return video.line()


@router.get("/clinics/{clinic_id}/video.mp4")
def video_file(clinic_id: int, download: bool = False):
    """The video itself: to watch it on the card, or to save it (download=true) and attach it to the message."""
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    path, _ = video.files(clinic_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Esta clínica todavía no tiene vídeo.")
    name = re.sub(r"[^a-z0-9]+", "-", clinic_web.plain(clinic["name"])).strip("-")[:60] or "clinica"
    return FileResponse(path, media_type="video/mp4", filename=f"propuesta-{name}.mp4" if download else None,
                        headers={"Cache-Control": "no-store"})


@router.post("/clinics/{clinic_id}/video/audio")
def add_video_audio(clinic_id: int, file: UploadFile = File(...), for_all: bool = Form(False)):
    """Put an audio on the clinic's video (my voice, a music), or on every video with for_all. Free.

    The video is not filmed again: the sound is joined to the picture in a second or two.
    """
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    content = file.file.read(video.AUDIO_MAX_BYTES + 1)
    video.save_audio(clinic_id, for_all, file.filename or "", content)
    return video.status(clinic)


@router.delete("/clinics/{clinic_id}/video/audio")
def remove_video_audio(clinic_id: int, for_all: bool = False):
    """Take the audio off the clinic's video, or off every video with for_all."""
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
    video.remove_audio(clinic_id, for_all)
    return video.status(clinic)


# ---- History ----

@router.post("/clinics/{clinic_id}/history", status_code=201)
def add_contact(clinic_id: int, contact: Contact):
    """Note a contact. The stage moves forward by itself when the contact means it."""
    with get_connection() as connection:
        clinic = fetch_clinic(connection, clinic_id)
        if contact.person_id is not None and fetch_person(connection, contact.person_id)["clinic_id"] != clinic_id:
            raise HTTPException(status_code=404, detail="Person not found")
        new_stage = stage_after(clinic["stage"], contact.kind)
        moved = new_stage != clinic["stage"]
        insert(connection, "clinic_history", {
            "clinic_id": clinic_id,
            "person_id": contact.person_id,
            "happened_on": (contact.happened_on or date.today()).isoformat(),
            "kind": contact.kind,
            "channel": contact.channel,
            "summary": contact.summary.strip(),
            "stage_to": new_stage if moved else "",
        })
        update(connection, "clinics", clinic_id, {"stage": new_stage})
        if contact.next_step is not None:
            set_next_step(connection, clinic_id, contact.next_step, contact.next_step_on)
        touch(connection, clinic_id)
        return fetch_clinic(connection, clinic_id) | {"moved": moved}


@router.delete("/history/{entry_id}")
def delete_contact(entry_id: int):
    """Remove a line of the history written by mistake. The stage stays where it is."""
    with get_connection() as connection:
        deleted = connection.execute("DELETE FROM clinic_history WHERE id = ?", (entry_id,)).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="History entry not found")
    return {"deleted": True}


# ---- People ----

@router.post("/clinics/{clinic_id}/people", status_code=201)
def add_person(clinic_id: int, new_person: PersonCreate):
    values = clean(new_person.model_dump(exclude_unset=True)) | {"clinic_id": clinic_id}
    with get_connection() as connection:
        fetch_clinic(connection, clinic_id)
        return fetch_person(connection, insert(connection, "clinic_people", values))


@router.patch("/people/{person_id}")
def update_person(person_id: int, changes: PersonUpdate):
    values = clean(changes.model_dump(exclude_unset=True))
    with get_connection() as connection:
        fetch_person(connection, person_id)
        update(connection, "clinic_people", person_id, values)
        return fetch_person(connection, person_id)


@router.delete("/people/{person_id}")
def delete_person(person_id: int):
    """Erase a person. The history keeps what happened, without the name."""
    with get_connection() as connection:
        fetch_person(connection, person_id)
        connection.execute("DELETE FROM clinic_people WHERE id = ?", (person_id,))
    return {"deleted": True}


# ---- Today ----

@router.get("/today")
def today():
    """What the CRM asks of me today.

    late: follow-ups whose day has passed. due: today's. adrift: conversations going on with no next step.
    fresh: the next clinics to write to for the first time. coming: the next seven days.
    A clinic marked "do not contact" never shows up here.
    """
    day = date.today()
    this_day, in_a_week = day.isoformat(), (day + timedelta(days=7)).isoformat()
    by_day = " ORDER BY next_step_on, clinics.name COLLATE NOCASE"
    open_marks = ", ".join("?" for _ in OPEN_STAGES)

    with get_connection() as connection:
        def clinics(where: str, values: tuple = ()) -> list[dict]:
            rows = connection.execute(CLINIC_QUERY + " WHERE do_not_contact = 0 AND " + where, values).fetchall()
            return [with_video(as_clinic(row)) for row in rows]

        def count(query: str, values: tuple = ()) -> int:
            return connection.execute(query, values).fetchone()[0]

        return {
            "today": this_day,
            "late": clinics("next_step_on < ?" + by_day, (this_day,)),
            "due": clinics("next_step_on = ?" + by_day, (this_day,)),
            "adrift": clinics(f"next_step_on IS NULL AND stage IN ({open_marks})"
                              " ORDER BY clinics.updated_at", OPEN_STAGES),
            # Best fit first, then independent clinics, then the ones I already have a website or Instagram for.
            "fresh": clinics("stage = 'to_contact' AND next_step_on IS NULL"
                             " ORDER BY fit DESC, kind = 'chain', (website = '' AND instagram = ''), clinics.id LIMIT 5"),
            "coming": clinics("next_step_on > ? AND next_step_on <= ?" + by_day, (this_day, in_a_week)),
            "counts": {
                "total": count("SELECT COUNT(*) FROM clinics"),
                "to_contact": count("SELECT COUNT(*) FROM clinics WHERE stage = 'to_contact' AND do_not_contact = 0"),
                "open": count(f"SELECT COUNT(*) FROM clinics WHERE stage IN ({open_marks})", OPEN_STAGES),
                "clients": count("SELECT COUNT(*) FROM clinics WHERE stage = 'client'"),
                "written_today": count(
                    "SELECT COUNT(DISTINCT clinic_id) FROM clinic_history WHERE happened_on = ? AND kind IN ("
                    + ", ".join("?" * len(OUTREACH)) + ")",
                    (this_day, *OUTREACH)),
            },
        }


# ---- My numbers ----

# What I count each week: a contact I noted, or a clinic I moved to that stage by hand on the board.
# The words on screen are in crm.html, under the same keys.
COUNTED = {
    "written": "(kind IN ('message_sent', 'audit_sent') OR stage_to = 'contacted')",
    "replies": "(kind = 'reply' OR stage_to = 'replied')",
    "calls_booked": "(kind = 'call_booked' OR stage_to = 'call_booked')",
    "calls": "(kind = 'call' OR stage_to = 'call_done')",
    "proposals": "(kind = 'proposal_sent' OR stage_to = 'proposal_sent')",
    "clients": "stage_to = 'client'",
}
WEEKS = 6


@router.get("/numbers")
def numbers():
    """My numbers, from the history of contacts.

    funnel: how many clinics have ever got to each stage of the path, even if they later left it (a clinic
    that replied and then said no still counts as contacted and as replied). now: where the clinics are
    today. weeks: what happened in each of the last weeks, Monday to Sunday, the newest first; each figure
    counts clinics, not messages (two messages to one clinic in a week count once).
    """
    day = date.today()
    monday = day - timedelta(days=day.weekday())
    with get_connection() as connection:
        clinics = connection.execute("SELECT id, stage FROM clinics").fetchall()
        history = connection.execute("SELECT clinic_id, kind, stage_to FROM clinic_history").fetchall()
        weeks = []
        for back in range(WEEKS):
            start = monday - timedelta(days=7 * back)
            end = start + timedelta(days=6)
            week = {"from": start.isoformat(), "to": end.isoformat()}
            for name, where in COUNTED.items():
                week[name] = connection.execute(
                    f"SELECT COUNT(DISTINCT clinic_id) FROM clinic_history WHERE {where} AND happened_on BETWEEN ? AND ?",
                    (week["from"], week["to"])).fetchone()[0]
            weeks.append(week)

    # The furthest each clinic has ever been along the path.
    furthest = {row["id"]: PATH.index(row["stage"]) if row["stage"] in PATH else 0 for row in clinics}
    for entry in history:
        reached = entry["stage_to"] or MOVES_TO.get(entry["kind"], "")
        if reached in PATH and entry["clinic_id"] in furthest:
            furthest[entry["clinic_id"]] = max(furthest[entry["clinic_id"]], PATH.index(reached))
    funnel = [{"stage": stage, "clinics": sum(1 for far in furthest.values() if far >= index)}
              for index, stage in enumerate(PATH)]
    now = {stage: 0 for stage in Stage.__args__}
    for row in clinics:
        now[row["stage"]] += 1
    return {"today": day.isoformat(), "funnel": funnel, "now": now, "weeks": weeks}


# ---- Import ----

def waiting_clinics() -> tuple[list[Path], list[ClinicImport]]:
    """The files of the private folder and the clinics written in them."""
    files = sorted(IMPORT_DIR.glob("clinics-*.json"))
    clinics = []
    for path in files:
        try:
            clinics += [ClinicImport(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError) as error:
            raise HTTPException(status_code=400, detail=f"No puedo leer {path.name}: {str(error)[:300]}")
    return files, clinics


def already_in(connection, clinic: ClinicImport) -> bool:
    """A clinic is the same one when it comes from the same page, or has the same name at the same address."""
    if clinic.source_url:
        query, values = "SELECT 1 FROM clinics WHERE source_url = ?", (clinic.source_url.strip(),)
    else:
        query = "SELECT 1 FROM clinics WHERE name = ? COLLATE NOCASE AND address = ? COLLATE NOCASE"
        values = (clinic.name.strip(), (clinic.address or "").strip())
    return connection.execute(query, values).fetchone() is not None


@router.get("/import")
def import_waiting():
    """How many clinics wait in the private folder to come in."""
    files, clinics = waiting_clinics()
    with get_connection() as connection:
        new = sum(not already_in(connection, clinic) for clinic in clinics)
    return {"files": [path.name for path in files], "new": new, "known": len(clinics) - new}


@router.post("/import")
def import_clinics():
    """Bring in the clinics that wait in the private folder. One that is already in is left alone.

    Then the files are deleted: the database is the only home of this data.
    """
    files, clinics = waiting_clinics()
    added = 0
    with get_connection() as connection:
        for clinic in clinics:
            if already_in(connection, clinic):
                continue
            values = clean(clinic.model_dump(exclude_unset=True))
            values["source_url"] = values.get("source_url") or None
            insert(connection, "clinics", values)
            added += 1
    for path in files:
        path.unlink(missing_ok=True)
    return {"added": added, "skipped": len(clinics) - added}
