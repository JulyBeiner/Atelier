"""Atelier control panel: clients, tasks from the agents, and my approvals."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import get_connection, init_db
from web_agent import analyze_client, usage_summary

STATIC_DIR = Path(__file__).parent / "static"

Department = Literal["brand", "web", "marketing", "automations", "maintenance", "detective"]
Importance = Literal["small", "important"]
Status = Literal["pending", "approved", "rejected", "done"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


TASK_QUERY = """
SELECT tasks.*, clients.name AS client_name
FROM tasks JOIN clients ON clients.id = tasks.client_id
"""


def fetch_task(connection, task_id: int) -> dict:
    row = connection.execute(TASK_QUERY + " WHERE tasks.id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return dict(row)


@app.get("/", include_in_schema=False)
def panel():
    return FileResponse(STATIC_DIR / "index.html")


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


@app.post("/api/clients/{client_id}/analyze", status_code=201)
def analyze_client_website(client_id: int):
    """Run the Web agent on this client. It uses the Claude API, so it costs money."""
    task_id = analyze_client(client_id)
    with get_connection() as connection:
        return fetch_task(connection, task_id)


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
