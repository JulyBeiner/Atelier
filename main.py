"""Atelier control panel: clients, tasks from the agents, my approvals, and the CRM."""

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import automations
import crm
import inspiration
from database import get_connection, init_db
from web_agent import REPORTS_DIR, readiness, recover_interrupted, start_analysis, usage_summary

STATIC_DIR = Path(__file__).parent / "static"

Department = Literal["brand", "web", "marketing", "automations", "maintenance", "detective"]
Importance = Literal["small", "important"]
Status = Literal["pending", "approved", "rejected", "done"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    automations.init_tables()
    recover_interrupted()
    inspiration.recover_interrupted()
    yield


app = FastAPI(title="Atelier", lifespan=lifespan)
# The files the pages need, like the logo, are served from /static.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# Everything under /api/crm lives in crm.py.
app.include_router(crm.router)
# The Automations department: its page and everything under /api/automations live in automations.py.
app.include_router(automations.router)


@app.middleware("http")
async def always_fresh(request, call_next):
    """Tell the browser to ask again for every file. A page never mixes old and new code after a change."""
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=2000)


class TaskCreate(BaseModel):
    client_id: int
    department: Department
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    importance: Importance


class Decision(BaseModel):
    feedback: str = Field(default="", max_length=2000)


class InspirationBrief(BaseModel):
    brief: str = Field(default="", max_length=2000)


class MyLink(BaseModel):
    url: str = Field(min_length=4, max_length=500)
    note: str = Field(default="", max_length=1000)


class Verdict(BaseModel):
    verdict: Literal["", "liked", "discarded"]
    likes: list[Literal["design", "page", "motion", "effects"]] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=1000)


class Choice(BaseModel):
    chosen: bool  # True: I finished choosing. False: none of them convinces me.
    feedback: str = Field(default="", max_length=2000)


# report_id is set when the task is a website analysis: the panel then links to the full report.
# inspiration_id is set when the task is a search for inspiration: the panel then links to the candidates.
TASK_QUERY = """
SELECT tasks.*, clients.name AS client_name, reports.id AS report_id, inspiration_searches.id AS inspiration_id
FROM tasks JOIN clients ON clients.id = tasks.client_id
LEFT JOIN reports ON reports.task_id = tasks.id
LEFT JOIN inspiration_searches ON inspiration_searches.task_id = tasks.id
"""

# An analysis without the long report inside: enough for lists and for comparing scores.
REPORT_QUERY = """
SELECT reports.id, reports.client_id, clients.name AS client_name, reports.task_id, reports.website,
       reports.status, reports.progress, reports.error, reports.overall_score, reports.scores,
       reports.cost_usd, reports.created_at, reports.finished_at
FROM reports JOIN clients ON clients.id = reports.client_id
"""

# The only file names the Web agent gives its screenshots.
SHOT_NAME = re.compile(r"p\d{1,2}-(desktop|mobile)-(top|\d)\.jpg")


def fetch_task(connection, task_id: int) -> dict:
    row = connection.execute(TASK_QUERY + " WHERE tasks.id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return dict(row)


def report_summary(row) -> dict:
    summary = dict(row)
    summary["scores"] = json.loads(summary["scores"])
    return summary


@app.get("/", include_in_schema=False)
def panel():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/reports/{report_id}", include_in_schema=False)
def report_page(report_id: int):
    """The page for reading one analysis comfortably. It loads its data from /api/reports/{id}."""
    return FileResponse(STATIC_DIR / "report.html")


@app.get("/inspiration/{client_id}", include_in_schema=False)
def inspiration_page(client_id: int):
    """The page for choosing what inspires one client's website. It loads its data from /api/clients/{id}/inspiration."""
    return FileResponse(STATIC_DIR / "inspiration.html")


@app.get("/crm", include_in_schema=False)
def crm_page():
    """The CRM: what to do today and the list of clinics. It loads its data from /api/crm."""
    return FileResponse(STATIC_DIR / "crm.html")


@app.get("/crm/clinics/{clinic_id}", include_in_schema=False)
def clinic_page(clinic_id: int):
    """The card of one clinic. It loads its data from /api/crm/clinics/{id}."""
    return FileResponse(STATIC_DIR / "clinic.html")


@app.get("/api/clients")
def list_clients():
    with get_connection() as connection:
        # clinic_id: the clinic of the CRM a client came from, when it was won there.
        rows = connection.execute(
            "SELECT clients.*, (SELECT MIN(id) FROM clinics WHERE clinics.client_id = clients.id) AS clinic_id"
            " FROM clients ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/clients/{client_id}")
def get_client(client_id: int):
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return dict(row)


@app.post("/api/clients", status_code=201)
def create_client(new_client: ClientCreate):
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO clients (name, website, notes) VALUES (?, ?, ?)",
            (new_client.name.strip(), new_client.website.strip(), new_client.notes.strip()),
        )
        row = connection.execute("SELECT * FROM clients WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.post("/api/clients/{client_id}/analyze", status_code=202)
def analyze_client_website(client_id: int):
    """Start the Web agent on this client. It uses the Claude API, so it costs money.

    The analysis takes a few minutes and runs in the background:
    the answer comes back at once with the id of the report to watch.
    """
    return {"report_id": start_analysis(client_id)}


@app.get("/api/reports")
def list_reports(client_id: int | None = None):
    """Every analysis, newest first."""
    where, values = (" WHERE reports.client_id = ?", [client_id]) if client_id is not None else ("", [])
    with get_connection() as connection:
        rows = connection.execute(REPORT_QUERY + where + " ORDER BY reports.id DESC", values).fetchall()
    return [report_summary(row) for row in rows]


@app.get("/api/reports/{report_id}")
def get_report(report_id: int):
    """One analysis: its full report, the task waiting for my decision, and the analysis before it."""
    with get_connection() as connection:
        row = connection.execute(REPORT_QUERY + " WHERE reports.id = ?", (report_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Report not found")
        data = connection.execute("SELECT data FROM reports WHERE id = ?", (report_id,)).fetchone()["data"]
        # The analysis before this one, to compare scores. One that I rejected does not count.
        previous = connection.execute(
            REPORT_QUERY + " LEFT JOIN tasks ON tasks.id = reports.task_id"
            " WHERE reports.client_id = ? AND reports.status = 'done' AND reports.id < ?"
            " AND COALESCE(tasks.status, '') != 'rejected' ORDER BY reports.id DESC LIMIT 1",
            (row["client_id"], report_id),
        ).fetchone()
        task = fetch_task(connection, row["task_id"]) if row["task_id"] else None
    return report_summary(row) | {
        "data": json.loads(data),
        "task": task,
        "previous": report_summary(previous) if previous else None,
    }


@app.get("/api/reports/{report_id}/shots/{name}")
def report_shot(report_id: int, name: str):
    """One screenshot of an analysis."""
    path = REPORTS_DIR / str(report_id) / name
    if not SHOT_NAME.fullmatch(name) or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/clients/{client_id}/inspiration")
def get_inspiration(client_id: int):
    """The candidates of one client, the last search, and whether a search waits for my decision."""
    return inspiration.page_data(client_id)


@app.post("/api/clients/{client_id}/inspiration/search", status_code=202)
def search_inspiration(client_id: int, request: InspirationBrief | None = None):
    """Start the Web agent looking for websites that can inspire. It uses the Claude API, so it costs money.

    The search takes a few minutes and runs in the background: the page follows it by asking again.
    """
    return {"search_id": inspiration.start_search(client_id, request.brief if request else "")}


@app.post("/api/clients/{client_id}/inspiration/links", status_code=201)
def add_inspiration_link(client_id: int, link: MyLink):
    """Add a website I already know. The agent only opens it and takes pictures: free."""
    return inspiration.add_my_link(client_id, link.url, link.note)


@app.post("/api/clients/{client_id}/inspiration/decision")
def decide_inspiration(client_id: int, choice: Choice):
    """I finished choosing, or none convinces me. It closes this client's searches waiting in the panel."""
    return inspiration.decide(client_id, choice.chosen, choice.feedback)


@app.post("/api/candidates/{candidate_id}/verdict")
def candidate_verdict(candidate_id: int, verdict: Verdict):
    """Mark one candidate: it inspires me (and what I like about it), I discard it, or not decided."""
    return inspiration.set_verdict(candidate_id, verdict.verdict, verdict.likes, verdict.note)


@app.post("/api/candidates/{candidate_id}/look", status_code=202)
def candidate_look_again(candidate_id: int):
    """Take the pictures of one candidate again. Free."""
    return inspiration.visit_again(candidate_id)


@app.get("/api/candidates/{candidate_id}/shots/{name}")
def candidate_shot(candidate_id: int, name: str):
    """One picture of a candidate."""
    path = inspiration.shots_folder(candidate_id) / name
    if not SHOT_NAME.fullmatch(name) or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/agent/check")
def agent_check():
    """What the Web agent has ready and what it is missing (keys, browser for screenshots)."""
    return readiness()


@app.get("/api/usage")
def api_usage():
    """How much the agents have spent on the Claude API, and the budget."""
    return usage_summary()


@app.get("/api/tasks")
def list_tasks(status: Status | None = None, client_id: int | None = None):
    conditions, values = [], []
    if status is not None:
        conditions.append("tasks.status = ?")
        values.append(status)
    if client_id is not None:
        conditions.append("tasks.client_id = ?")
        values.append(client_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with get_connection() as connection:
        rows = connection.execute(TASK_QUERY + where + " ORDER BY tasks.id DESC", values).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/tasks", status_code=201)
def create_task(new_task: TaskCreate):
    """Agents report their work here.

    Small tasks are already done: they are only a notification.
    Important tasks wait until I approve or reject them.
    """
    status = "done" if new_task.importance == "small" else "pending"
    with get_connection() as connection:
        client = connection.execute("SELECT 1 FROM clients WHERE id = ?", (new_task.client_id,)).fetchone()
        if client is None:
            raise HTTPException(status_code=404, detail="Client not found")
        cursor = connection.execute(
            "INSERT INTO tasks (client_id, department, title, description, importance, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (new_task.client_id, new_task.department, new_task.title.strip(),
             new_task.description.strip(), new_task.importance, status),
        )
        return fetch_task(connection, cursor.lastrowid)


def decide(task_id: int, new_status: str, feedback: str) -> dict:
    with get_connection() as connection:
        task = fetch_task(connection, task_id)
        if task["status"] != "pending":
            raise HTTPException(status_code=409, detail="Task is not waiting for approval")
        if new_status == "approved" and task["inspiration_id"] and not inspiration.liked_count(connection, task["client_id"]):
            # Approving a search means "these are the ones": it needs at least one.
            raise HTTPException(status_code=409, detail="Marca al menos una web que te inspire antes de aprobar.")
        connection.execute(
            "UPDATE tasks SET status = ?, feedback = ?, decided_at = datetime('now') WHERE id = ?",
            (new_status, feedback.strip(), task_id),
        )
        return fetch_task(connection, task_id)


@app.post("/api/tasks/{task_id}/approve")
def approve_task(task_id: int, decision: Decision | None = None):
    return decide(task_id, "approved", decision.feedback if decision else "")


@app.post("/api/tasks/{task_id}/reject")
def reject_task(task_id: int, decision: Decision | None = None):
    return decide(task_id, "rejected", decision.feedback if decision else "")
