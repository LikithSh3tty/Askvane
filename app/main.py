"""FastAPI app: chat routes and the static UI."""
from fastapi import FastAPI

app = FastAPI(title="Askvane clarification agent")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
