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

## A small turn loop in `src/agent.py`

The spec's layout has no module for the turn itself. `agent.py` is the one place the pieces meet: it offers requirements, calls the extractor, runs grounding and the ambiguity check, applies what survives, then either asks one question or generates. Every decision in it is deterministic; the LLM is only reached through the extractor and the phraser.

## One message is read more than once when it opens new requirements

"Use Gmail, label Finance" answers the trigger and then the label, but the label requirement does not exist until Gmail is chosen. After applying a pass, the loop offers any newly opened requirements and reads the same message again, at most three passes. The model is still only ever offered what is open at that moment.

## Changes of mind reopen filled requirements, only when the user signals one

Extraction is restricted to open requirements, which on its own would make "actually, send it by email instead" impossible to act on. When the message contains a correction marker ("actually", "instead", "change", "switch", ...), already-filled requirements are offered as well. Changing a node type clears that node's params, so switching from Slack to email drops the channel and opens the recipient.

## Unsupported requests are declined, then the question is asked again

When the user names something the catalog cannot express ("post it to Discord"), the extractor reports it as unsupported, and deterministic code confirms the span is real and matches none of the options. The agent says it cannot do that, lists what it can, and keeps the requirement open. It never maps Discord onto the nearest thing it does support.

## Derived nodes

The reference workflow shows "Filter by Label" and "Extract Invoice & Amount" steps that the user never chose. The generator derives them: a label or folder on a mail trigger becomes a filter node, and a condition on a field becomes an extract step for that field. They are consequences of what the user said, not new values, so they are not asked about.

## No emoji in replies

The reference shows a check-mark emoji on the final message. Replies and the state table use plain text ("All information collected"); the UI draws its own icon. Plain text keeps API output clean for any client that is not a browser.

## The eval's `assumed` check does not trust the guard

`run_eval.py` flags a conversation as `assumed` if any value in the final state is either missing from the conversation's expected values or has no textual evidence anywhere in what the user typed. It shares the alias table with the guard but not its logic, so switching the guard off (`--no-grounding`) shows up as `assumed` failures rather than passing silently. That ablation result is committed next to the real one.

## What the stub eval does and does not measure

With the stub, the "model" is a lookup table, so a 25/25 score measures the deterministic layers: offer restriction, grounding, ambiguity, ordering, corrections and generation, against readings that include deliberate hallucinations. It says nothing about how well a real model reads free text. `--provider anthropic` runs the same conversations against Claude; those numbers are the ones that measure extraction quality.
