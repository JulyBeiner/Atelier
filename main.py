"""Atelier control panel: clients, tasks from the agents, and my approvals."""

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import get_connection, init_db
from web_agent import REPORTS_DIR, readiness, recover_interrupted, start_analysis, usage_summary

STATIC_DIR = Path(__file__).parent / "static"

Department = Literal["brand", "web", "marketing", "automations", "maintenance", "detective"]
Importance = Literal["small", "important"]
Status = Literal["pending", "approved", "rejected", "done"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recover_interrupted()
    yield


app = FastAPI(title="Atelier", lifespan=lifespan)


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


# report_id is set when the task is a website analysis: the panel then links to the full report.
TASK_QUERY = """
SELECT tasks.*, clients.name AS client_name, reports.id AS report_id
FROM tasks JOIN clients ON clients.id = tasks.client_id
LEFT JOIN reports ON reports.task_id = tasks.id
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


@app.get("/api/clients")
def list_clients():
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM clients ORDER BY name").fetchall()
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
