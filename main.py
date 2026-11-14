from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def home():
    return {"message": "Hello from Atelier"}


@app.get("/clients")
def list_clients():
    return [{"name": "Demo Yachts"}, {"name": "Demo Boutique"}]