# Decisions

Short records of the choices behind this codebase. Each entry says what was decided and why.

## Sessions live in memory, no database

Sessions are a plain dict keyed by session id inside the API process. The assignment asks for a conversation that collects information and emits a workflow; nothing in it needs state to survive a restart. Redis or Postgres would be infrastructure to explain rather than engineering to show, so it is left out on purpose.

## httpx is a dependency

FastAPI's `TestClient` is built on httpx, so the API tests need it. It is a test dependency only; the running app never makes an HTTP call.

## Question order follows the data flow of the workflow

The build spec lists the priority as trigger, trigger params, action, action params, then logic. The assignment's own reference conversation asks about the amount condition before it asks where to send the notification, so that order would make the reference conversation impossible to reproduce. The catalog therefore declares `slots` (trigger, filter, action, dedupe) in the order data moves through the workflow, and questions follow slot order, then catalog declaration order within a slot.

## Slack workspace is optional

The reference conversation asks "Which Slack workspace and channel?", the user answers only "#finance channel", and the agent treats that as complete. Requiring `workspace` would force a question the reference never asks. A Slack credential is installed per workspace, so the workspace is known from the connection; it stays in the catalog as an optional param and is recorded if the user volunteers it.

## Optional params are accepted, never asked

The reference ends as soon as the mandatory items are known, and shows "Additional Preferences: -". Optional params (Slack message, email subject) are offered to the extractor so a volunteered value is kept, but the engine never spends a question on them.

## Edges are derived, not stored

State holds the chosen nodes and their values. Edges follow from which slots are filled (a condition exists only when the user asked for one), so the generator derives them. Storing them as well would create a second copy that could disagree with the first.

## Structured outputs instead of a `{` prefill

The build spec asked for the opening `{` to be prefilled. Current Claude models return a 400 for assistant prefill, so the provider passes the JSON schema through `output_config.format`. That constrains decoding to the schema, which is a stronger guarantee than a prefill ever gave. The model defaults to `claude-opus-5` and can be changed with `ANTHROPIC_MODEL`; effort is `low` because each call reads one short sentence.

## Server-side refusal fallback on the Claude provider

Requests carry `fallbacks: "default"` so that if a safety classifier declines a request, the API reruns it on its default fallback model inside the same call. For this domain a refusal is unlikely, and when one does get through, the provider raises instead of returning something empty.

## The stub dispatches on the schema title

The LLM interface is one method taking a system prompt, a user message and a schema. The stub needs to know whether it is being asked to extract or to phrase, so every schema carries a `title` ("extraction" or "question"). Real providers strip it before sending.

## Grounding checks the value against its span, not only the span against the message

A span check alone is not enough: a model can quote real text ("finance team") and claim a value it does not contain ("#finance"). So the guard also requires that the span actually says the value. For a closed set, the span must contain one of that option's aliases from the catalog (so "Yes" cannot ground `false`, and "notify" cannot ground Slack). For free text, the value must appear verbatim in the span, prefix included. Numbers are compared digit for digit after removing thousands separators, so "₹10,000" grounds 10000 and nothing else.

## Aliases live in the catalog

Mapping "Yes" to `true`, "above" to `gt` and "₹" to the amount field is data, not code, so it sits next to the enum it describes. The same aliases drive the ambiguity check: if a span names more than one option, the agent asks.
