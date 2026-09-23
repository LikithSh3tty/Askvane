# Decisions

Short records of the choices behind this codebase. Each entry says what was decided and why.

## Sessions live in memory, no database

Sessions are a plain dict keyed by session id inside the API process. The assignment asks for a conversation that collects information and emits a workflow; nothing in it needs state to survive a restart. Redis or Postgres would be infrastructure to explain rather than engineering to show, so it is left out on purpose.

## httpx is a dependency

FastAPI's `TestClient` is built on httpx, so the API tests need it. It is a test dependency only; the running app never makes an HTTP call.
