# Riley: Voice AI Patient Registration

Riley is a voice agent for a fictional clinic, **Riverside Family Health**. She answers a real US phone
number, registers new patients through natural conversation, recognizes returning callers, and saves
every record to PostgreSQL. A REST API and a live web dashboard expose the stored data.

## Live demo

| | |
|---|---|
| **Phone number** | **+1 (732) 375-0415** |
| API base URL | https://zb-medical-dashboard.up.railway.app |
| Interactive API docs (Swagger) | https://zb-medical-dashboard.up.railway.app/docs |
| Dashboard (refreshes every 10 s) | https://zb-medical-dashboard.up.railway.app/dashboard |
| Health check | https://zb-medical-dashboard.up.railway.app/health |
| Write API key (`X-API-Key`) | Sent with the submission. Reads need no key |

All data is fictional. Please don't give real personal information on the call.

### Try saying...

1. **New patient:** "Hi, I'd like to register." Then answer Riley's questions, including a correction
   ("actually, it's spelled D-A-V-I-S").
2. **Out of order:** "I'm Sam Lee, born June 3rd 1985, and I live at 12 Main Street in Denver, Colorado."
3. **Invalid data:** a birth date in the future, or a 7-digit phone number. Riley re-asks for just that item.
4. **Start over:** "Can we start over?"
5. **Returning caller:** call again from the same phone. Riley finds your record, checks your date of
   birth, and offers to update it instead of creating a duplicate.
6. **Spanish:** "Hablo español."
7. **Hang up halfway:** the dashboard's *Recent calls* tab shows the call as *Incomplete*, with the
   transcript and everything collected so far. No partial patient is saved.

Keep the dashboard open while you call: the new record appears within 10 seconds.

---

## Architecture

```
                ┌───────────────────────────── Vapi ─────────────────────────────┐
 Caller ──PSTN──▶ Phone number ─▶ Deepgram STT ─▶ GPT-4o (prompt + tools) ─▶ TTS ──▶ Caller
                └───────────────────────────────┬─────────────────────────────────┘
                                                │ HTTPS webhooks (X-Vapi-Secret)
                                                ▼
┌──────────────────────────────── FastAPI app (Railway) ─────────────────────────────────┐
│  /vapi/tools   tool calls during the call ─┐                                            │
│  /vapi/events  end-of-call report ─────────┤                                            │
│  /patients     REST CRUD ──────────────────┼──▶ services/ ──▶ SQLAlchemy ──▶ PostgreSQL  │
│  /calls        call logs (read) ───────────┤        ▲                                   │
│  /dashboard    static HTML + JS ───────────┘        └── validators.py + schemas.py      │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

**Separation of concerns**

| Layer | Where | Responsibility |
|---|---|---|
| Telephony, speech, LLM loop | Vapi (configured as code in [`scripts/vapi_setup.py`](scripts/vapi_setup.py)) | Phone number, speech-to-text, text-to-speech, turn-taking, calling our tools |
| Conversation design | [`prompts/system_prompt.md`](prompts/system_prompt.md) | What Riley says and when she calls each tool |
| HTTP | [`app/api/`](app/api/) | Routing, auth, status codes, the `{data, error}` envelope. No business logic |
| Business logic | [`app/services/`](app/services/) | Create, update, soft-delete, lookup, identity checks, idempotency, call logging |
| Validation | [`app/validators.py`](app/validators.py) + [`app/schemas.py`](app/schemas.py) | The **only** place validation rules live |
| Data | [`app/models.py`](app/models.py) | Tables, types, and CHECK constraints that repeat the key rules at the database level |

The REST API and the voice tools call **the same service layer with the same Pydantic schemas**, so the
agent can never save something the API would reject. The LLM collects and converses; the server decides
what's valid.

**What happens during a call**

1. The caller gives their name. Riley calls `lookup_patient_by_phone` with the caller-ID number.
2. For a returning caller, Riley names the record and asks for their date of birth.
   `verify_patient_identity` compares it **on the server**. The stored date is never sent to the model.
3. For a new caller, Riley collects the required fields in any order, calls `validate_patient`, and
   re-asks only for fields with errors.
4. She reads everything back in three short chunks and waits for a yes after each.
5. `create_patient` (or `update_patient`) saves the record. The tool always returns HTTP 200 with a
   result Riley can read, so she never goes silent and never claims a save that didn't happen.
6. After hang-up, Vapi posts the end-of-call report (transcript, summary, reason). It is stored in
   `call_logs` and linked to the patient.

## Tech stack and why

| Layer | Choice | Why |
|---|---|---|
| Telephony, STT, TTS, orchestration | **Vapi** (free US number) | Handles audio, barge-in, and endpointing, so the work goes into the prompt, tools, and backend |
| LLM | **OpenAI GPT-4o**, temperature 0.3 | Reliable tool calling with low latency; a low temperature keeps data capture consistent |
| Speech-to-text | **Deepgram nova-3**, language `multi` | Accurate on names and numbers, and handles English/Spanish code-switching |
| Voice | **ElevenLabs Flash v2.5** (`sarah`) | Low latency and multilingual, so Riley can speak Spanish |
| Backend | **Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, psycopg 3** | Typed, validation built in, and Swagger docs for free |
| Database | **PostgreSQL** on Railway (SQLite for local dev and tests) | Real constraints, and data survives restarts and redeploys |
| Hosting | **Railway**, always on | Serverless is off: a cold start would make Vapi's tool calls time out |
| Dashboard | One static HTML file served by FastAPI | Nothing extra to build or deploy |
| Tests | pytest + FastAPI TestClient | 170 tests; they run on SQLite by default and also against Postgres |

## Conversation design

The full prompt, with comments explaining each section, is in
[`prompts/system_prompt.md`](prompts/system_prompt.md). The setup script strips the comments before
sending the prompt to Vapi.

- **Written for the ear.** One question at a time, short turns, no lists or symbols. Phone numbers are
  read in groups, dates as words, ZIP codes digit by digit.
- **The server is the source of truth.** The model converts speech into formats (MM/DD/YYYY, state
  codes), but `validate_patient` decides what's valid. Error messages are written to be spoken aloud
  ("Date of birth can't be in the future"), so Riley can relay them directly.
- **Chunked readback.** Callers can't follow a 16-field monologue, so Riley confirms three short groups
  and waits for a yes after each. If something is corrected, only that part is re-confirmed.
- **Nothing is saved before confirmation, and nothing is claimed that wasn't saved.** Tool results say
  exactly what happened: `ok`, per-field `errors`, `already_registered`, or `system_error`.
- **Identity before details.** A phone match only reveals the name on file. Updating requires the
  caller's date of birth, checked on the server, and the model never sees the stored date. After 3
  wrong attempts in one call, the record is locked for that call.
- **Idempotent saves.** If the LLM retries `create_patient` in the same call, the existing record is
  updated instead of creating a duplicate.
- **Resilience built into the prompt.** Start over, corrections, out-of-order answers, Spanish,
  off-topic requests, medical questions (911 for emergencies), requests for a human, "are you a
  robot?", abusive callers, and non-US addresses.
- **Silence.** If the caller goes quiet, Riley checks in after 10 seconds (up to twice), then says
  goodbye after a further 30 seconds of silence. Calls are capped at 10 minutes.
- **Tuned turn-taking.** Riley waits longer after the caller says a number (1.5 s) so she doesn't cut
  in while digits are being read out. Background noise is filtered with Krisp.

### Voice tools

| Tool | Purpose | Result the LLM sees |
|---|---|---|
| `lookup_patient_by_phone` | Find existing patients by phone | `{"found": true, "patients": [{patient_id, first_name, last_name}]}`: names only |
| `verify_patient_identity` | Check the date of birth the caller said against the record | `{"verified": true/false}`, or `locked` after 3 failures |
| `validate_patient` | Full validation, no save | `{"ok": true, "normalized": {...}}` or `{"ok": false, "errors": [{field, message}]}` |
| `create_patient` | Save a new patient (idempotent per call) | `{"ok": true, "patient_id", "first_name"}`, `errors`, `already_registered`, or `system_error` |
| `update_patient` | Partial update; requires `verified_date_of_birth` | Same shapes |
| `endCall` | Vapi built-in: hang up after the goodbye | — |

## API reference

Every response uses the same envelope:

```json
{ "data": { "...": "..." }, "error": null }
{ "data": null, "error": { "code": "validation_error", "message": "Some fields are invalid",
  "details": [ { "field": "date_of_birth", "message": "Date of birth can't be in the future" } ] } }
```

| Method | Path | Auth | Success | Errors |
|---|---|---|---|---|
| GET | `/health` | — | 200 `{status, db, db_engine}` | 503 if the database is unreachable |
| GET | `/patients` | — | 200 list, newest first. Filters: `last_name` (case-insensitive), `date_of_birth` (either format), `phone_number` (any format), `limit` (≤200), `offset` | 400 bad query parameter |
| GET | `/patients/{id}` | — | 200 | 400 malformed UUID, 404 not found or deleted |
| POST | `/patients` | `X-API-Key` | **201** with `patient_id` | 400 malformed JSON, 401, 422 with per-field details |
| PUT | `/patients/{id}` | `X-API-Key` | 200; **partial update**, only the fields sent change | 400, 401, 404, 422 (including trying to clear a required field) |
| DELETE | `/patients/{id}` | `X-API-Key` | 200; **soft delete**, sets `deleted_at` | 401, 404 (also if already deleted) |
| GET | `/calls` | — | 200 call logs, newest first | — |
| GET | `/patients/{id}/calls` | — | 200 calls linked to the patient | 404 |
| POST | `/vapi/tools`, `/vapi/events` | Vapi secret | Always 200 for tool calls | 401 without the secret |

Other status codes: unknown fields in a body are rejected with 422. Database outages return 503
`database_unavailable`, and any other unexpected error returns 500. Stack traces are logged, never
returned.

```bash
BASE=https://zb-medical-dashboard.up.railway.app
KEY=<write API key>

curl "$BASE/patients?last_name=zain"
curl "$BASE/patients?phone_number=(416)%20402-3292"

curl -X POST "$BASE/patients" -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
  "first_name": "Jane", "last_name": "Doe", "date_of_birth": "03/04/1990", "sex": "Female",
  "phone_number": "(212) 555-0123", "address_line_1": "123 Main St", "city": "Austin",
  "state": "Texas", "zip_code": "78701"}'
# → 201; phone normalized to 2125550123, state to TX, date of birth returned as 03/04/1990

curl -X PUT "$BASE/patients/<id>" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"city": "Dallas", "zip_code": "75201"}'
curl -X DELETE "$BASE/patients/<id>" -H "X-API-Key: $KEY"
```

## Data model

### `patients`

| Column | Type | Rules |
|---|---|---|
| `patient_id` | UUID, primary key | Generated |
| `first_name`, `last_name` | VARCHAR(50) NOT NULL | Letters (any alphabet), hyphens, apostrophes, single inner spaces. Title-cased only if given in all lower or all upper case |
| `date_of_birth` | DATE NOT NULL | Accepts MM/DD/YYYY or YYYY-MM-DD; returned as MM/DD/YYYY. Not in the future (clinic time zone) and not more than 130 years ago |
| `sex` | VARCHAR(20) NOT NULL, CHECK | `Male`, `Female`, `Other`, `Decline to Answer` (accepts "m", "f", "prefer not to say", ...) |
| `phone_number` | VARCHAR(10) NOT NULL, CHECK length 10 | US numbers: formatting and a leading 1 are stripped; area code and exchange can't start with 0 or 1 |
| `email` | VARCHAR(254) | Syntax-checked (no DNS lookup, to keep calls fast), stored lowercase |
| `address_line_1` / `address_line_2` | VARCHAR(200) NOT NULL / VARCHAR(100) | HTML and control characters rejected |
| `city` | VARCHAR(100) NOT NULL | 1–100 characters |
| `state` | CHAR(2) NOT NULL, CHECK length 2 | USPS code for the 50 states, DC, PR, GU, VI, AS, MP; full names accepted ("Texas" becomes TX) |
| `zip_code` | VARCHAR(10) NOT NULL | `12345` or `12345-6789` (`123456789` is accepted) |
| `insurance_provider` / `insurance_member_id` | VARCHAR(100) / VARCHAR(50) | Member ID: letters, digits, hyphens; spaces removed, uppercased |
| `preferred_language` | VARCHAR(50) NOT NULL, default `English` | "Español" becomes Spanish |
| `emergency_contact_name` / `_phone` | VARCHAR(100) / VARCHAR(10) | Same rules as names and phones |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | UTC; `updated_at` changes on every modification |
| `deleted_at` | TIMESTAMPTZ | Soft-delete marker; deleted rows are hidden from every read |

Indexes: `phone_number`, `lower(last_name)`, `date_of_birth`, `deleted_at`.

**Why phone numbers aren't unique:** families share phones. Duplicate detection is instead a lookup
plus a question, backed by a server-side rule: `create_patient` from a call refuses an exact duplicate
(same phone, name, and date of birth) and offers an update instead. A parent registering a child on
the same phone still works.

### `call_logs`

One row per Vapi call, upserted by `vapi_call_id`, because tool calls and the end-of-call report can
arrive in either order. It stores `outcome` (`created`, `updated`, `incomplete`, `failed`), the linked
`patient_id`, `caller_number`, `ended_reason`, `summary`, `transcript`, `started_at`/`ended_at`, and
`collected_data`: everything the agent sent to any tool during the call. That last field is how a
dropped call still shows what was gathered.

## Edge cases and resilience

| Scenario | What happens |
|---|---|
| Invalid date of birth (future, Feb 30, 1890) | The validator rejects it with a spoken reason; Riley re-asks for the date of birth only |
| Invalid phone (3 or 7 digits, bad area code) | Same, for the phone number only |
| Caller corrects something ("it's D-A-V-I-S") | The prompt tells Riley to use the corrected value and re-confirm just that piece |
| Out-of-order or bundled answers | Riley keeps everything said and never re-asks for it |
| Interruptions | Vapi barge-in; the prompt tells Riley to stop and respond to what the caller said |
| Start over | "No problem, let's start fresh": everything collected is discarded and she starts with the name |
| **Call drops mid-conversation** | No patient is saved (nothing is saved before confirmation). The call is logged as `incomplete` with transcript and collected data, and a warning is written to the logs |
| **Database write fails** | The tool returns HTTP 200 with `system_error`. Riley apologizes, offers one retry, then tells the caller their information was **not** saved. The call is marked `failed` with the data it had collected |
| Our server unreachable or times out | Vapi's configured failure message: "I'm having trouble reaching our system" |
| Duplicate caller | Lookup by caller ID right after the name, then identity check, then update. Server-side exact-duplicate guard as a backup |
| Date-of-birth guessing | Locked after 3 wrong attempts per call, enforced on the server |
| LLM retries a save | Idempotent per call: the retry updates the same patient |
| Tool-call payload shape changes | The parser accepts every documented shape (flat, OpenAI-style, `toolWithToolCallList`; arguments as an object or a JSON string) |
| Silence | Two check-ins, then a polite hang-up |
| Non-US address, off-topic requests, medical emergencies | Handled in the prompt (US-only explanation, steer back, 911) |

Automated tests cover every row that doesn't need a live phone call, including a simulated database
failure.

## Setup

### Run locally

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate     # or python3.12 -m venv .venv
uv pip install -r requirements-dev.txt                       # or pip install -r requirements-dev.txt
cp .env.example .env                                         # then set API_KEY and VAPI_WEBHOOK_SECRET
uvicorn app.main:app --reload                                # http://localhost:8000/dashboard
```

With `DATABASE_URL` unset, the app uses `./local.db` (SQLite). `SEED_ON_STARTUP=true` adds two
fictional patients when the table is empty.

### Tests

```bash
pytest                                                                  # SQLite, about 1 s
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/db pytest       # same suite on Postgres
```

`tests/test_validators.py` covers every normalization rule, `tests/test_patients_api.py` covers the
REST API and envelope, `tests/test_vapi_webhook.py` covers the tools, payload shapes, idempotency,
failures and the end-of-call report, and `tests/test_vapi_setup.py` keeps the Vapi config, prompt and
server in sync.

### Deploy on Railway

1. New Project → Deploy from GitHub repo, then add **PostgreSQL**.
2. On the web service, set the variables below, with `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Settings → Networking → Generate Domain. Leave **Serverless off**.
4. `railway.json` sets the start command and the `/health` check; `.python-version` pins Python 3.12.
   Tables are created on startup.

**Persistence check:** register a patient, click **Redeploy** in Railway, then reload `/patients`: the
record is still there. `/health` reports `"db_engine": "postgresql"`, and startup logs a warning if it
ever falls back to SQLite.

### Configure Vapi

```bash
python scripts/vapi_setup.py --dry-run   # print the tool and assistant payloads (secret redacted)
python scripts/vapi_setup.py             # create or update the 5 tools and the assistant, bind the number
```

The script is idempotent: tools are matched by name and the assistant by name. **Re-run it after any
change to the prompt.** It needs `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `PUBLIC_BASE_URL`, and the
same `VAPI_WEBHOOK_SECRET` as the server.

## Environment variables

| Variable | Used by | Required | Example / notes |
|---|---|---|---|
| `DATABASE_URL` | server | in production | Railway's `postgresql://…`, rewritten to the psycopg 3 driver automatically. Unset means local SQLite |
| `API_KEY` | server | yes | Long random string, required as `X-API-Key` on POST, PUT and DELETE. Writes are refused if unset |
| `VAPI_WEBHOOK_SECRET` | server + setup script | yes | Vapi sends it as `X-Vapi-Secret` (`Authorization: Bearer` also accepted) |
| `SEED_ON_STARTUP` | server | no | `true` adds 2 fictional patients to an empty table |
| `LOG_LEVEL` | server | no | `INFO` |
| `CLINIC_TIMEZONE` | server | no | `America/New_York`; defines "today" for the date-of-birth check |
| `VAPI_API_KEY` | setup script | yes | Vapi private key; never used by the running server |
| `VAPI_PHONE_NUMBER_ID` | setup script | yes | The number's ID in the Vapi dashboard |
| `PUBLIC_BASE_URL` | setup script | yes | `https://zb-medical-dashboard.up.railway.app` |

Secrets live only in environment variables; `.env` is gitignored and `.env.example` has placeholders.

## Observability

Logs go to stdout as one JSON object per line, which Railway parses into searchable fields:

- every request: method, path, status, latency;
- every tool call: tool name, call ID, arguments, result;
- every end-of-call report: call ID, outcome, ended reason, and the **final collected payload**
  (a warning when no patient was saved);
- every database or unexpected error, with the stack trace.

Secrets are never logged. Transcripts and summaries are also stored in `call_logs` and shown on the
dashboard.

## Known limitations and trade-offs

- **Reads are open.** Anyone with the URL can list patients, for reviewer convenience with fictional
  data. Writes need the API key. Production would need authentication on every read.
- **No HIPAA controls.** No encryption at rest beyond Railway's defaults, no audit log, no BAA with
  vendors, and Vapi stores recordings and transcripts. Out of scope, as the brief says.
- **A phone match reveals a name.** Anyone calling from a number (or claiming it) hears the name on
  file. This matches the requested "we already have a record for [First Name] [Last Name]" behaviour;
  everything beyond the name requires the date of birth.
- **Duplicates are detected by phone only** (plus the exact-match guard). The same person calling from
  a new phone becomes a new record.
- **One patient per call.** Per-call idempotency means a second person can't be registered on the
  same call; call again.
- **Canadian numbers pass phone validation.** They share the US 10-digit format (NANP). Addresses must
  be in the US.
- **Speech recognition errors on unusual names.** Mitigated by spelling and readback, not eliminated.
- **`create_all` instead of migrations.** Fine for a fixed schema; adding a column in production would
  need Alembic.
- **Single worker, single phone number.** Enough for a demo. Concurrent calls are limited by the Vapi
  plan.
- **Deliberate deviations from the brief:** names may contain inner spaces ("Mary Ann", "De La Cruz");
  database outages return 503 rather than 500, since 503 says "try again later".

**Vapi notes, verified on 2026-09-23.** The docs disagree on the tool-call shape (`arguments` vs
`parameters`, flat vs `function.*`), so the parser accepts all of them. `silenceTimeoutSeconds` no
longer exists, so silence uses `customer.speech.timeout` hooks. Summaries are **off unless
`analysisPlan.summaryPlan` is enabled**, which was confirmed on a live call despite the docs.

## Next steps

- Alembic migrations, and authentication on read endpoints.
- Resume a dropped call from its `collected_data` ("Welcome back, shall we pick up where we left off?").
- Address verification (USPS or Smarty) and SMS confirmation after registration.
- A Vapi Simulations eval suite that replays the edge-case script on every prompt change.
- Rate limiting on the webhooks and the API.
- Replace the free Vapi number with a Twilio number for more concurrency and caller-ID reliability.

## Project layout

```
app/
  main.py              app factory, request logging, startup (create tables, seed)
  config.py            settings from environment variables
  db.py                engine, session, DATABASE_URL normalization
  models.py            Patient, CallLog (+ CHECK constraints)
  validators.py        every normalization and validation rule
  schemas.py           Pydantic models built from the validators; error formatting
  errors.py            exception handlers producing the {data, error} envelope
  security.py          X-API-Key and Vapi webhook secret checks
  seed.py              two fictional demo patients
  logging_config.py    JSON logs to stdout
  services/            patients.py, calls.py, voice_tools.py (business logic)
  api/                 patients.py, calls.py, vapi.py, dashboard.py (HTTP only)
  static/dashboard.html
prompts/system_prompt.md   Riley's system prompt, with design comments
scripts/vapi_setup.py      Vapi tools, assistant, and phone binding as code
tests/                     170 pytest tests
```
