"""FastAPI app: chat routes and the static UI."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.session import Session, SessionStore
from src.agent import handle_turn
from src.catalog.loader import get_catalog
from src.generator import state_table
from src.llm.base import LLM, LLMError, make_llm
from src.llm.stub import StubMiss

STATIC = Path(__file__).resolve().parent.parent / "static"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    state_table: list[dict]
    workflow: dict | None = None
    complete: bool


class SessionView(BaseModel):
    session_id: str
    state_table: list[dict]
    workflow: dict | None = None
    complete: bool
    transcript: list[dict]


def create_app(llm: LLM | None = None) -> FastAPI:
    get_catalog()   # a malformed catalog stops the app here, not on the first message
    app = FastAPI(title="Askvane clarification agent")
    app.state.llm = llm or make_llm()
    app.state.sessions = SessionStore()

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The traceback goes to the server log; the client sees only the exception type,
        # never its message, which could quote a secret (a malformed key in a header error).
        print("".join(traceback.format_exception(exc)), file=sys.stderr, flush=True)
        return JSONResponse(status_code=500, content={"detail": f"internal error: {type(exc).__name__}"})

    def find(session_id: str) -> Session:
        session = app.state.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
        return session

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        session = find(req.session_id) if req.session_id else app.state.sessions.create()
        # Run the turn on a copy so a failed LLM call leaves the session untouched.
        state = session.state.model_copy(deep=True)
        try:
            result = handle_turn(state, req.message.strip(), app.state.llm)
        except StubMiss as exc:
            raise HTTPException(status_code=422, detail=(
                "The offline stub has no scripted answer for that message. "
                "Set ANTHROPIC_API_KEY to talk to Claude, or try the example request.")) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=f"language model error: {exc}") from exc
        session.state = state
        session.workflow = result.workflow   # a later correction can reopen the workflow
        app.state.sessions.save(session)
        return ChatResponse(session_id=state.session_id, reply=result.reply,
                            state_table=result.state_table, workflow=result.workflow,
                            complete=result.complete)

    @app.get("/session/{session_id}", response_model=SessionView)
    def get_session(session_id: str) -> SessionView:
        session = find(session_id)
        return SessionView(session_id=session_id, state_table=state_table(session.state),
                           workflow=session.workflow, complete=session.workflow is not None,
                           transcript=[t.model_dump() for t in session.state.transcript])

    @app.delete("/session/{session_id}", status_code=204)
    def reset_session(session_id: str) -> None:
        if not app.state.sessions.delete(session_id):
            raise HTTPException(status_code=404, detail=f"no session {session_id!r}")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "provider": app.state.llm.name}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
