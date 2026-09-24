# Decisions

Short records of the choices behind this codebase. Each entry says what was decided and why. The first section covers the architecture; the second covers calls the build spec left open or got wrong.

# Core design

## Completeness is decided by code, not by the model

Whether every mandatory item is known is a property of a data structure: is each required param of each chosen node set, and is there a trigger and an action. That is checkable, so `engine.is_complete` checks it. Asking a model "do you have enough now?" would turn a reliable answer into a probabilistic one, and it would make the accuracy numbers depend on the model's mood rather than on the design.

## Extraction is restricted to currently open requirements

The model is shown only the requirements that are open this turn (plus optional params of nodes already chosen), and anything it returns for another slot is dropped before grounding. A model cannot fill a slot it was not offered. On the reference opening "notify my finance team", the Slack channel requirement does not exist yet, so a hallucinated `#finance` has nowhere to go.

## Grounding verifies spans instead of trusting the model

Every extracted value must come with the exact words it was read from. Code checks that those words are in the user's message this turn, that the value is an allowed option, and that the words actually say that value. An instruction in a prompt is a request; a span check is a guarantee. This is the brief's "never assume missing information" made structural rather than instructed.

## Requirements are computed every turn, not held as a checklist

The set of questions is not fixed. Choosing Gmail creates a label requirement that did not exist a moment ago; choosing Slack creates a channel requirement, choosing email creates a recipient instead; saying "only above ₹10,000" creates field, operator and value. `engine.requirements` derives the open set from the current state on every call, so a change of mind (Slack to email) removes the old requirements and adds the new ones with no bookkeeping.

## Ambiguity asks instead of picking

The brief asks the agent to detect "whether multiple interpretations exist". If the user's words name more than one option ("an email" fits Gmail and Outlook) or one phrase answers two different text slots, the agent quotes the words back and asks. Picking the likelier reading would be an assumption with extra steps. The one exception is when the user was answering a specific question: "Finance" in reply to "Which label?" is the label.

## One question per turn

The brief and its reference both ask one question at a time. It also keeps each answer attributable: when a reply is short ("Finance"), the question just asked tells both the model and the ambiguity check what it answers. `next_question` returns one requirement, never a list; a requirement asked twice without an answer is flagged so the third ask is worded differently.

## Sessions live in memory, no database

Sessions are a plain dict keyed by session id inside the API process. The assignment asks for a conversation that collects information and emits a workflow; nothing in it needs state to survive a restart. Redis or Postgres would be infrastructure to explain rather than engineering to show, so it is left out on purpose.

## The stub provider exists so nothing needs the network

Every LLM call goes through one interface, and the stub implements it from a lookup table. The whole test suite and the eval run offline with no key. The stub also plays a badly behaved model on purpose: its table includes hallucinations (Slack read out of "notify", `#finance` out of "finance team") so the guard is tested against the failure it exists to stop. An input it has no entry for raises, rather than returning something plausible.

# Decisions the spec did not cover

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

## The stub words questions around what the user named

The real phraser is told to use the user's own words, so the stub does the same deterministically: it takes the first subject the user named from a short list in `stub_responses.yaml` (invoices, orders, messages, …) and fills it into the question. When the user named none, as with a daily summary on a schedule, it asks a generic version rather than talking about invoices nobody mentioned.

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

When the user names something the catalog cannot express ("post it to Mattermost"), the extractor reports it as unsupported, and deterministic code confirms the span is real and matches none of the options. The agent says it cannot do that, lists what it can, and keeps the requirement open. It never maps Mattermost onto the nearest thing it does support.

## Derived nodes

The reference workflow shows "Filter by Label" and "Extract Invoice & Amount" steps that the user never chose. The generator derives them: a label or folder on a mail trigger becomes a filter node, and a condition on a field becomes an extract step for that field. They are consequences of what the user said, not new values, so they are not asked about.

## No emoji in replies

The reference shows a check-mark emoji on the final message. Replies and the state table use plain text ("All information collected"); the UI draws its own icon. Plain text keeps API output clean for any client that is not a browser.

## The eval's `assumed` check does not trust the guard

`run_eval.py` flags a conversation as `assumed` if any value in the final state is either missing from the conversation's expected values or has no textual evidence anywhere in what the user typed. It shares the alias table with the guard but not its logic, so switching the guard off (`--no-grounding`) shows up as `assumed` failures rather than passing silently. That ablation result is committed next to the real one.

## What the stub eval does and does not measure

With the stub, the "model" is a lookup table, so a 25/25 score measures the deterministic layers: offer restriction, grounding, ambiguity, ordering, corrections and generation, against readings that include deliberate hallucinations. It says nothing about how well a real model reads free text. `--provider anthropic` runs the same conversations against Claude; those numbers are the ones that measure extraction quality.

## Keys reach the container at run time only

The image contains the app, the catalog and the UI, nothing else: `.dockerignore` keeps out `.env`, tests, eval and docs. `docker-compose.yml` passes `ANTHROPIC_API_KEY` from the shell or `.env` when the container starts, so no key is ever written into a layer. With no key the app falls back to the offline stub, so `docker compose up` works on a fresh machine.

## The catalog goes beyond the spec's eleven node types

The spec named four triggers and three actions. The catalog now also covers Google Sheets, Excel, Google Forms, Typeform, Telegram, Google Drive, Google Calendar, Stripe, Shopify, GitHub and RSS as triggers, and Microsoft Teams, Discord, Telegram, WhatsApp, SMS, Google Sheets, Excel, Notion, Trello, Jira, Airtable and Asana as actions. No code changed to support them: each is a catalog entry, and its required details become questions the same way Gmail's label does. Where one word names two apps ("a spreadsheet" is Google Sheets or Excel, "a form" is Google Forms or Typeform), the aliases overlap on purpose so the ambiguity check asks. The unsupported-request conversations now use apps that are still outside the catalog (Mattermost, Jotform, Signal). State-table row labels now come from the catalog too (see below), so a Jira action no longer sits under "Notification Channel".

## State-table row labels live in the catalog

The generator used to name every row itself, so a Jira project appeared under "Channel / Recipient". What a row means depends on the node in it, and the catalog is where nodes are described, so each node declares the label of the row that names it and each required param the label of the row carrying its value. The reference path declares the brief's exact wording (Trigger Source, Monitor Location, Condition, Notification Channel, Channel / Recipient, Duplicate Handling), a test pins those eight names in the brief's order, and the order itself stays in the generator because it is the reference table's. A node without a label falls back to the slot's display name, or the brief's name for a values row, so the catalog can be labelled gradually.

## A values row joins the labels of the params it shows

The trigger and action values each share one row, and some nodes need two details (a Trello board and list, a Jira project and issue type). Adding rows would break the reference's fixed shape, so the row's label joins the distinct labels of the required params in declaration order: "Project / Issue Type". Because only currently required params count, a schedule reads "Frequency" until a daily frequency makes the run time required.

## The UI matches table rows by position

Row labels now change with the chosen apps, so the page can no longer look values up by label. The server always sends the same eight rows in the same order; the page renders them by position and keeps the brief's labels only as placeholders before the first reply.
