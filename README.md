# Askvane

A clarification agent that turns a plain-language automation request into a structured workflow. It asks one question at a time until every mandatory detail is known, and it never fills a gap with a guess.

```
user:  Whenever a new invoice arrives, notify my finance team.
agent: Which platform receives the invoice?
user:  Gmail
agent: Which Gmail label or folder should I monitor?
user:  Finance
agent: Should every invoice trigger the workflow, or only invoices above a certain amount?
user:  Only above ₹10,000
agent: Where should the notification be sent?
user:  Slack
agent: Which Slack workspace and channel?
user:  #finance channel
agent: Should duplicate invoices be ignored?
user:  Yes
agent: I have all the required information. Generating your workflow.
```

```
Gmail Trigger -> Filter by Label -> Duplicate Handling -> Extract Fields -> Condition --yes--> Slack Notification (#finance)
                 (Finance)                                 (amount)          (amount > 10000)
                                                                                      --no---> End
```

Nothing is executed and no external service is called. The output is a workflow representation: a JSON graph of nodes and edges, plus the collected-information table from the brief.

## How it works

The model reads values out of text and words questions. Everything else is deterministic code.

```
                 user message
                      |
                      v
   +------------------------------------+
   | engine.requirements(state)         |  what is open right now, derived from
   |   (no LLM)                         |  the nodes chosen so far
   +------------------------------------+
                      | open requirements only
                      v
   +------------------------------------+
   | extractor            (LLM)         |  "which of these does the message
   |                                    |   answer? quote the exact words"
   +------------------------------------+
                      | value + span per requirement
                      v
   +------------------------------------+
   | grounding            (no LLM)      |  span is in the message? value is an
   |                                    |  allowed option? span says the value?
   +------------------------------------+   -> rejected values are logged, never applied
                      | grounded values
                      v
   +------------------------------------+
   | ambiguity            (no LLM)      |  words fit two options, or one phrase
   |                                    |  fits two slots -> ask, never pick
   +------------------------------------+
                      | clear values
                      v
                 WorkflowState  <---- new requirements may open; the same
                      |                message is read again (max 3 passes)
                      v
   +------------------------------------+
   | engine.is_complete / next_question |  one requirement, in data-flow order,
   |   (no LLM)                         |  flagged for rephrase after 2 misses
   +------------------------------------+
            |                         |
      incomplete                  complete
            v                         v
   +------------------+     +--------------------+
   | phraser   (LLM)  |     | generator (no LLM) |  workflow JSON with yes/no
   | words the one    |     | + state table      |  branches, and the table
   | chosen question  |     +--------------------+
   +------------------+
```

| Layer | File | Owns | LLM |
|---|---|---|---|
| Node catalog | `src/catalog/nodes.yaml`, `loader.py` | Node types, params, what is required and when | no |
| State | `src/state.py` | Values collected, where each came from, transcript | no |
| Engine | `src/engine.py` | What is missing, which single question is next | no |
| Extractor | `src/extractor.py` | Reading values out of one message | yes |
| Grounding guard | `src/grounding.py` | Rejecting values the user never said | no |
| Ambiguity check | `src/ambiguity.py` | Detecting more than one reading | no |
| Phraser | `src/phraser.py` | Wording the chosen question | yes |
| Generator | `src/generator.py` | State to workflow JSON and the table | no |
| Turn loop | `src/agent.py` | Wiring the above for one message | no |

The requirement set is recomputed from state every turn. Choosing Gmail creates a label requirement; choosing Slack creates a channel requirement where email would create a recipient; "only above ₹10,000" creates field, operator and value. The reasoning behind each choice is in [DECISIONS.md](DECISIONS.md).

## Supported apps

Every app is an entry in `src/catalog/nodes.yaml`: its name, the words a user might use for it, and the details it needs. Nothing else in the code names an app, so adding one is a catalog entry, a logo in the UI, and a question for the offline stub.

| Starts the workflow | Needs |
|---|---|
| Gmail, Outlook | label or folder |
| Webhook | URL path |
| Schedule | how often, then a time and day when that applies |
| Google Sheets, Excel | spreadsheet or workbook to watch for new rows |
| Google Forms, Typeform | form |
| Telegram | chat or bot |
| Google Drive | folder |
| Google Calendar | calendar |
| Stripe | event (payment succeeded or failed, invoice paid, subscription created or cancelled) |
| Shopify | event (new order, paid order, new customer, product update) |
| GitHub | repository and event (push, pull request, issue, release) |
| RSS | feed URL |

| Sends or records the result | Needs |
|---|---|
| Slack, Discord | channel |
| Microsoft Teams | team and channel |
| Email | recipient address |
| Telegram | chat |
| WhatsApp, SMS (Twilio) | phone number |
| HTTP Request | URL and method |
| Google Sheets, Excel | spreadsheet or workbook to add a row to |
| Notion | database |
| Trello | board and list |
| Jira | project and issue type |
| Airtable | base and table |
| Asana | project |

Words that fit more than one app ("a spreadsheet", "a form", "an email") are asked about rather than guessed. An app that is not listed is declined by name and the question is asked again.

## Running it

Python 3.11.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; use .venv/bin/activate elsewhere
pip install -r requirements.txt
uvicorn app.main:app --reload
```

On Windows, keep the checkout at a short path or enable long paths: the `anthropic` package has deeply nested files, and pip skips them without an error when the full path passes 260 characters.

Open http://localhost:8000. With `ANTHROPIC_API_KEY` set the agent uses Claude (`claude-opus-5` by default, override with `ANTHROPIC_MODEL`). Without a key it uses the offline stub, which only knows the scripted messages in `src/llm/stub_responses.yaml`; the example request on the start screen is one of them.

With Docker:

```bash
cp .env.example .env              # add a key, or leave it blank for the stub
docker compose up --build
```

### API

| Method | Path | Body / result |
|---|---|---|
| `POST` | `/chat` | `{session_id?, message}` returns `{session_id, reply, state_table, workflow, complete}`. Omit `session_id` to start a session. |
| `GET` | `/session/{id}` | Current state table, workflow and transcript. `404` for an unknown id. |
| `DELETE` | `/session/{id}` | Discards the session. |
| `GET` | `/health` | Status and which model provider is active. |

## Tests and evaluation

```bash
pytest                                              # offline, no key needed
python eval/run_eval.py --provider stub             # writes eval/results.json
python eval/run_eval.py --provider stub --no-grounding --out eval/results_no_grounding.json
python eval/run_eval.py --readme                    # refreshes the table below
```

`eval/conversations.yaml` holds 34 scripted conversations covering plain requests, apps beyond the reference (Google Sheets, Teams, Stripe, SMS, Typeform, Excel, GitHub, Jira, Shopify, Telegram, Notion), one message answering several questions, answers given before they were asked, genuinely ambiguous wording (including two ambiguities in one message), changes of mind, everything in the first message, requests the catalog cannot express, and a user who stops early. Each run is classified as `exact`, `complete_different`, `incomplete`, or `assumed` (a value the user never gave reached the state, the one failure the brief forbids by name).

<!-- eval:start -->
| Run | Provider | Conversations | exact | complete_different | incomplete | assumed | Guard rejections |
|---|---|---|---|---|---|---|---|
| Grounding on | stub | 34 | 34 | 0 | 0 | 0 | 8 |
| Grounding off (ablation) | stub | 34 | 32 | 0 | 0 | 2 | 0 |

Turns to reach a complete workflow (grounding on): 1 turn: 2, 2 turns: 3, 3 turns: 5, 4 turns: 5, 5 turns: 7, 6 turns: 3, 7 turns: 3, 8 turns: 2, 9 turns: 1.
<!-- eval:end -->

The ablation turns the grounding guard off and replays the same conversations. The `assumed` check in the harness does not reuse the guard's logic, so any hallucination that survives to the end of a conversation shows up there. Most of the 8 the guard rejects are later overwritten by what the user really says; the 2 conversations that still fail keep an "Inbox" label and a duplicate preference nobody stated, and an email action read out of "text me".

## Limitations

- The stub eval measures the deterministic layers against scripted model readings. It does not measure how well a real model reads free text; `--provider anthropic` runs the same set against Claude, and those results are not committed here.
- Grounding proves the user said the words, not that the words mean what the model claims. If the channel question is open and the user says "notify the finance team", `finance` would be accepted as a channel name. Restricting extraction to open requirements narrows this but does not close it.
- A change of mind is only recognised when the message contains a correction word ("actually", "instead", "change", "switch"). "Email, not Slack" without one of those words is not picked up as a correction.
- Workflows have one trigger, at most one condition with a single comparison, and one action. "Notify Slack and email the CFO" or "above ₹10,000 and from a vendor" cannot be expressed, and are not faked.
- Aliases in the catalog are English and hand-written. A synonym that is missing from the list gets a clarifying question rather than a wrong answer, but it still costs the user a turn.
- Optional details (Slack message text, email subject) are kept if volunteered but never asked for.
- Sessions live in one process's memory and are lost on restart. Running more than one worker would need a shared store.
- State-table row labels come from the catalog, so a Jira action shows "Issue Tracker" and "Project / Issue Type" while the reference path keeps the brief's wording. The table still has the reference's fixed eight rows: a node that needs two details shares one values row under a joined label, and optional details all land in "Additional Preferences".
- When one message is ambiguous twice ("add form responses to a spreadsheet"), the first is asked about and the second is parked, then asked as a narrowed question ("Google Sheets or Excel?") when its turn comes. What still does not work: only ambiguities between named options are parked, so a phrase that could answer two free-text questions is lost unless it came first; a narrowing is built from one message and a later message cannot add to it; and a change of mind about the first answer always keeps the parked second, even when the correction implies otherwise ("actually, keep everything in Google").
