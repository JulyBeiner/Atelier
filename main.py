from fastapi import FastAPI, HTTPException

app = FastAPI()

clients = [
    {"id": 1, "name": "Demo Yachts"},
    {"id": 2, "name": "Demo Boutique"},
]


@app.get("/")
def home():
    return {"message": "Hello from Atelier"}


@app.get("/clients")
def list_clients():
    return clients


@app.get("/clients/{client_id}")
def get_client(client_id: int):
    for client in clients:
        if client["id"] == client_id:
            return client
    raise HTTPException(status_code=404, detail="Client not found")