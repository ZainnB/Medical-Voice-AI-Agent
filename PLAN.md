# PLAN.md — Voice AI Patient Registration Agent (CareCloud Take-Home)

> This file is the single source of truth for the build. Read it fully before writing code.
> When the plan and the assessment PDF disagree, the PDF wins. When something here is
> ambiguous, choose the simplest option that works end-to-end, and note the choice in the README.

---

## 0. Goal and deliverables

A reviewer dials a **US phone number**, talks naturally to a voice agent that registers them as a patient,
and the record is saved to a **persistent database**. On a second call, the agent recognizes them and the data
is still there. A **REST API** and a **web dashboard** expose the stored records.

**Submission (all must be live):**
1. Repository URL (GitHub)
2. US phone number to call
3. API base URL (Railway), plus `/docs` (Swagger)
4. Dashboard URL (`<base>/dashboard`)
5. Credentials/notes for testing (API key for write endpoints, sample script of things to say)

**Grading (20% each):** Working system · Conversational quality · Technical architecture ·
Code quality & docs · Edge cases & resilience. The reviewers explicitly prefer a *simple system that works
flawlessly* over an ambitious one that breaks.

---

## 1. Tech stack (fixed — do not change without asking)

| Layer | Choice | Reason |
|---|---|---|
| Telephony + STT + TTS + LLM orchestration | **Vapi** (free US number) | Removes audio plumbing; we focus on prompt, tools, backend |
| LLM | **OpenAI `gpt-4o`** via Vapi (temperature 0.3) | Reliable tool calling, low latency |
| Transcriber | **Deepgram `nova-3`** (or `nova-2` if unavailable), language `multi` for EN/ES | Good accuracy on names/numbers, code-switching for Spanish bonus |
| Voice | A multilingual voice (ElevenLabs `eleven_multilingual_v2`/turbo, or Cartesia sonic multilingual) | Must be able to speak Spanish for the bonus |
| Backend | **Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, psycopg 3** | Fast to build, validation built in, auto Swagger docs |
| Database | **PostgreSQL on Railway** (SQLite for local dev + tests) | Real constraints, survives restarts |
| Hosting | **Railway** (always-on web service + Postgres plugin) | No cold starts (cold starts break Vapi tool calls) |
| Dashboard | Single static HTML page + vanilla JS served by FastAPI | No extra build or deploy |
| Tests | **pytest + FastAPI TestClient** on SQLite | Covers API validation and tool webhook |

---

## 2. Architecture

```
                ┌───────────────────────────── Vapi ─────────────────────────────┐
 Caller ──PSTN──▶  Phone number ─▶ Deepgram STT ─▶ GPT-4o (system prompt + tools) ─▶ TTS ──▶ Caller
                └───────────────────────────────┬─────────────────────────────────┘
                                                │  HTTPS webhooks (x-vapi-secret)
                                                ▼
┌──────────────────────────────── FastAPI app (Railway) ─────────────────────────────────┐
│  /vapi/tools   (tool-calls)  ──┐                                                       │
│  /vapi/events  (end-of-call) ──┤                                                       │
│  /patients     (REST, CRUD)  ──┼──▶  services/ (patients, calls)  ──▶  SQLAlchemy ──▶ Postgres
│  /calls        (REST, read)  ──┤          ▲                                            │
│  /dashboard    (static HTML) ──┘          └── validators.py (single source of validation rules)
└────────────────────────────────────────────────────────────────────────────────────────┘
```

**Key principle — separation of concerns:**
- `api/` = HTTP concerns only (routing, status codes, envelope).
- `services/` = business logic (create/update/find/soft-delete, idempotency, call logging).
- `validators.py` + `schemas.py` = the *only* place validation and normalization rules live.
- The Vapi tool webhook and the REST API both call the **same service layer**, so the voice agent
  can never save data the API would reject.
- Telephony/LLM configuration lives in `prompts/system_prompt.md` and `scripts/vapi_setup.py` (config as code).

---

## 3. Repository layout

```
.
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory, routers, exception handlers, startup (create tables, seed)
│   ├── config.py            # pydantic-settings: reads env vars
│   ├── db.py                # engine, SessionLocal, get_db dependency, DATABASE_URL normalization
│   ├── models.py            # SQLAlchemy models: Patient, CallLog (+ Appointment if stretch goal done)
│   ├── schemas.py           # Pydantic models: PatientCreate, PatientUpdate, PatientOut, CallLogOut, envelope
│   ├── validators.py        # normalize/validate: names, phone, DOB, state, zip, sex, member id
│   ├── errors.py            # custom exceptions + handlers producing the {data, error} envelope
│   ├── logging_config.py    # JSON-ish structured logging to stdout
│   ├── security.py          # API key dependency, Vapi webhook secret check
│   ├── seed.py              # 2 demo patients (fake data)
│   ├── services/
│   │   ├── patients.py
│   │   └── calls.py
│   ├── api/
│   │   ├── patients.py      # /patients CRUD
│   │   ├── calls.py         # /calls, /patients/{id}/calls
│   │   ├── vapi.py          # /vapi/tools, /vapi/events
│   │   └── dashboard.py     # /dashboard, /health
│   └── static/
│       └── dashboard.html
├── prompts/
│   └── system_prompt.md     # The voice agent system prompt, with comments explaining design choices
├── scripts/
│   └── vapi_setup.py        # Creates/updates Vapi tools + assistant + binds phone number (idempotent)
├── tests/
│   ├── conftest.py
│   ├── test_validators.py
│   ├── test_patients_api.py
│   └── test_vapi_webhook.py
├── .env.example
├── .gitignore
├── Procfile                 # web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
├── railway.json             # optional: healthcheck path /health
├── requirements.txt
└── README.md
```

---

## 4. Environment variables

| Var | Required | Example | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes (prod) | `postgresql://...` (Railway provides) | Default `sqlite:///./local.db` locally. **Normalize** `postgres://` and `postgresql://` → `postgresql+psycopg://` in `db.py` |
| `API_KEY` | yes | long random string | Required as `X-API-Key` header on POST/PUT/DELETE `/patients` |
| `VAPI_WEBHOOK_SECRET` | yes | long random string | Vapi sends it on every webhook; reject requests without it (401) |
| `VAPI_API_KEY` | setup script only | Vapi private key | Used by `scripts/vapi_setup.py`, never by the running server |
| `VAPI_PHONE_NUMBER_ID` | setup script only | uuid | The free number's ID from the Vapi dashboard |
| `PUBLIC_BASE_URL` | setup script only | `https://xyz.up.railway.app` | Used to build tool/server URLs |
| `SEED_ON_STARTUP` | no | `true` | Inserts 2 demo patients if the table is empty |
| `LOG_LEVEL` | no | `INFO` | |
| `CLINIC_TIMEZONE` | no | `America/New_York` | Used for "today" when checking DOB not in future |

Never commit `.env`. Provide `.env.example` with placeholders.

---

## 5. Data model

### 5.1 `patients`

| Column | Type | Constraint / rule |
|---|---|---|
| `patient_id` | UUID PK | default `uuid4` |
| `first_name` | VARCHAR(50) NOT NULL | 1–50 chars; letters, hyphens, apostrophes (also allow internal single spaces, e.g. "Mary Ann" — document this deviation) |
| `last_name` | VARCHAR(50) NOT NULL | same as first_name ("De La Cruz", "O'Brien", "Smith-Jones") |
| `date_of_birth` | DATE NOT NULL | not in the future; not more than 130 years ago |
| `sex` | VARCHAR(20) NOT NULL | CHECK in (`Male`,`Female`,`Other`,`Decline to Answer`) |
| `phone_number` | VARCHAR(10) NOT NULL | exactly 10 digits, NANP: area code and exchange first digit 2–9. CHECK length = 10 |
| `email` | VARCHAR(254) NULL | valid email (email-validator), stored lowercase |
| `address_line_1` | VARCHAR(200) NOT NULL | non-empty |
| `address_line_2` | VARCHAR(100) NULL | |
| `city` | VARCHAR(100) NOT NULL | 1–100 chars |
| `state` | CHAR(2) NOT NULL | valid USPS code (50 states + DC + PR, GU, VI, AS, MP); CHECK length = 2 |
| `zip_code` | VARCHAR(10) NOT NULL | `^\d{5}(-\d{4})?$` |
| `insurance_provider` | VARCHAR(100) NULL | |
| `insurance_member_id` | VARCHAR(50) NULL | alphanumeric (allow hyphens), stored uppercase |
| `preferred_language` | VARCHAR(50) NOT NULL | default `English` |
| `emergency_contact_name` | VARCHAR(100) NULL | |
| `emergency_contact_phone` | VARCHAR(10) NULL | same rule as phone_number |
| `created_at` | TIMESTAMPTZ NOT NULL | server default now() UTC |
| `updated_at` | TIMESTAMPTZ NOT NULL | default now(), `onupdate` now() UTC |
| `deleted_at` | TIMESTAMPTZ NULL | soft delete marker |

Indexes: `phone_number`, `lower(last_name)` (or plain `last_name`), `date_of_birth`, `deleted_at`.
**Phone is NOT unique** (families share phones). Duplicate detection is a lookup plus a question from the agent,
not a DB constraint. Document this decision.

Put CHECK constraints in `__table_args__` so the DB enforces them independently of Pydantic.
Use `Base.metadata.create_all()` on startup (no Alembic, given the time box; list Alembic under Next Steps).

### 5.2 `call_logs` (transcripts, observability, bonus)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `vapi_call_id` | VARCHAR(100) UNIQUE NOT NULL | from `message.call.id` |
| `patient_id` | UUID FK → patients NULL | set when create/update succeeds during this call |
| `caller_number` | VARCHAR(20) NULL | from `message.call.customer.number` |
| `outcome` | VARCHAR(20) NOT NULL | `created` · `updated` · `incomplete` · `failed` (default `incomplete`) |
| `ended_reason` | VARCHAR(100) NULL | from end-of-call-report |
| `summary` | TEXT NULL | Vapi analysis summary |
| `transcript` | TEXT NULL | full transcript text |
| `collected_data` | JSON NULL | last payload the agent sent to a tool (useful for dropped calls) |
| `started_at` / `ended_at` | TIMESTAMPTZ NULL | |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

Upsert by `vapi_call_id`: tool calls may arrive before the end-of-call report, or the reverse.

---

## 6. Validation and normalization (`app/validators.py`)

One module, pure functions, fully unit-tested. Used by Pydantic validators (`field_validator`) so both
the REST API and the tool webhook get identical behavior. Each returns a normalized value or raises
`ValueError` with a **human-readable message** (the agent may speak it).

| Field | Accept (input) | Normalize to | Reject with message |
|---|---|---|---|
| names | `"  mary-ann "`, `"o'brien"` | trimmed, collapse spaces; keep caller's casing, but title-case if all-lower/all-upper | "First name can only contain letters, spaces, hyphens, and apostrophes" |
| `date_of_birth` | `MM/DD/YYYY`, `YYYY-MM-DD` | `date` object; API **outputs `MM/DD/YYYY`** to match the spec | "Date of birth can't be in the future", "…doesn't look like a real date" |
| `sex` | `male`, `m`, `female`, `f`, `other`, `decline`, `prefer not to say` | canonical enum | "Sex must be Male, Female, Other, or Decline to Answer" |
| phones | `(416) 555-0123`, `+1 212 555 0123`, `2125550123` | 10 digits (strip non-digits, strip leading `1` if 11 digits) | "Phone number must be a 10-digit US number" |
| `state` | `tx`, `Texas`, `TX` | `TX` | "…isn't a valid US state" |
| `zip_code` | `12345`, `12345-6789`, `123456789` | `12345` or `12345-6789` | "ZIP code must be 5 digits or ZIP+4" |
| `email` | any case | lowercase | "That email address isn't valid" |
| `insurance_member_id` | `abc 123-45` | `ABC123-45` (strip spaces, upper) | "Member ID can only contain letters, numbers, and hyphens" |
| `preferred_language` | `spanish`, `Español` | `Spanish` (title-case; map Español→Spanish) | — |

Strip leading/trailing whitespace from every string. Treat empty strings as `None` for optional fields.
Reject HTML/control characters in free-text fields (basic sanitization).

---

## 7. REST API

All responses use the envelope: `{"data": <object|list|null>, "error": null | {"code": str, "message": str, "details": [ {"field": str, "message": str} ]}}`

| Method | Path | Auth | Success | Errors |
|---|---|---|---|---|
| GET | `/health` | none | 200 `{"data":{"status":"ok","db":"ok"}}` | 500 if DB unreachable |
| GET | `/patients` | none | 200 list (excludes soft-deleted). Query: `last_name` (case-insensitive exact), `date_of_birth` (either format), `phone_number` (normalized), `limit` (default 50, max 200), `offset` | 400 bad query param |
| GET | `/patients/{id}` | none | 200 | 400 malformed UUID, 404 not found/deleted |
| POST | `/patients` | `X-API-Key` | **201** created record incl. `patient_id` | 401, 400 malformed JSON, 422 validation |
| PUT | `/patients/{id}` | `X-API-Key` | 200 updated record; **partial update** (only provided fields change); cannot null out required fields | 401, 400, 404, 422 |
| DELETE | `/patients/{id}` | `X-API-Key` | 200 with record showing `deleted_at` | 401, 404 (incl. already deleted) |
| GET | `/calls` | none | 200 list of call logs, newest first (`limit`, `offset`) | |
| GET | `/patients/{id}/calls` | none | 200 call logs for that patient | 404 |

Implementation notes:
- Override FastAPI's `RequestValidationError` handler → 422 envelope with per-field `details`.
- JSON decode errors → 400. `HTTPException` → envelope. Catch-all `Exception` → 500 envelope with a generic message (log the stack trace; never leak it).
- Unknown fields in POST/PUT bodies → 422 (`model_config = ConfigDict(extra="forbid")`).
- Protect the reads? **No**: reads are open for reviewer convenience (all data is fake). Document this trade-off. Writes require `X-API-Key`.
- Swagger at `/docs` must show the `X-API-Key` header.

---

## 8. Vapi integration

### 8.1 Webhook security
Every `/vapi/*` request must carry the shared secret. Accept **either** `x-vapi-secret: <VAPI_WEBHOOK_SECRET>`
**or** `Authorization: Bearer <VAPI_WEBHOOK_SECRET>` (Vapi supports both, depending on how the server credential is configured).
Compare with `hmac.compare_digest`. Missing or wrong → 401.
**Verify the current header names in https://docs.vapi.ai/server-url/server-authentication before implementing.**

### 8.2 `POST /vapi/tools` — tool calls

Vapi sends:
```json
{ "message": {
    "type": "tool-calls",
    "call": { "id": "call-uuid", "customer": { "number": "+14165550123" } },
    "toolCallList": [ { "id": "call_abc", "name": "create_patient", "arguments": { ... } } ]
} }
```
**Parse defensively.** Across Vapi versions a tool call may look like `{id, name, arguments}` or
`{id, type: "function", function: {name, arguments}}`, and `arguments` may be an object **or a JSON string**.
Handle all of these. Handle multiple tool calls in one request.

Respond (**always HTTP 200**, even on validation/business errors, so the LLM gets to read the error):
```json
{ "results": [ { "toolCallId": "call_abc", "result": "<compact JSON string>" } ] }
```
The `result` is a compact JSON **string** the LLM can reason about, e.g.
`{"ok":false,"errors":[{"field":"date_of_birth","message":"Date of birth can't be in the future"}]}`.
Only an unexpected server bug should return a non-200 status, and even then try to return a result string.

Log every tool call (tool name, call id, arguments, outcome) to stdout.

### 8.3 Tools (define in `scripts/vapi_setup.py`)

| Tool | Args | Behavior | Result |
|---|---|---|---|
| `lookup_patient_by_phone` | `phone_number` | Normalize, then find non-deleted patients with that phone (most recent first) | `{"found":true,"patients":[{"patient_id","first_name","last_name","date_of_birth"}]}` or `{"found":false}`. Return **only** these fields; the agent must verify DOB with the caller before discussing details |
| `validate_patient` | all patient fields (all optional) | Runs the full validation without saving | `{"ok":true,"normalized":{...}}` or `{"ok":false,"errors":[...]}` |
| `create_patient` | all patient fields (required ones marked required) | Validate, then insert. **Idempotent per call**: if this `vapi_call_id` already created a patient, update that patient instead of inserting a duplicate. Link call_log → patient, outcome `created` | `{"ok":true,"patient_id":...,"first_name":...}` or `{"ok":false,"errors":[...]}` or `{"ok":false,"system_error":true,"message":"..."}` on DB failure |
| `update_patient` | `patient_id` + any fields | Partial update via the same service. Link call_log, outcome `updated` | same shape |
| `endCall` | (Vapi built-in) | Hang up after the goodbye line | — |

JSON schemas for the tools: every property has a clear `description` telling the LLM the expected format,
for example `"date_of_birth": "Date of birth in MM/DD/YYYY format"`, `"state": "2-letter US state code, e.g. TX"`,
`"sex": "One of: Male, Female, Other, Decline to Answer"`, `"phone_number": "10 digits, no formatting"`.

On any tool call, upsert the call_log row and store the arguments in `collected_data`. That way,
if the call drops before saving, we still have what was collected.

Configure tool `messages` (spoken while the tool runs):
- `create_patient` request-start: "One moment while I save that for you."
- `lookup_patient_by_phone` request-start: "Let me check our system."
- request-failed (all): "I'm having trouble reaching our system."

### 8.4 `POST /vapi/events` — server messages
Handle `end-of-call-report`: upsert call_log with `transcript` (`message.artifact.transcript` or build from
`message.artifact.messages`), `summary` (`message.analysis.summary`), `ended_reason`, start/end times.
If no patient was saved during the call, set outcome `incomplete` and log a WARNING with the `collected_data`
(this covers "connection drops mid-call"). Ignore other message types (return 200 `{}`).
Log the final collected payload for every call (observability requirement).

### 8.5 Assistant configuration (`scripts/vapi_setup.py`)
An idempotent script using the Vapi REST API (`https://api.vapi.ai`, `Authorization: Bearer $VAPI_API_KEY`):
1. Create or update the 4 function tools (find existing ones by name via `GET /tool`), with `server.url = {PUBLIC_BASE_URL}/vapi/tools` and the secret.
2. Create or update the assistant "Patient Intake — Riley" (find by name):
   - `model`: provider `openai`, model `gpt-4o`, temperature `0.3`, `toolIds`: [4 tools], the endCall tool enabled, system message = contents of `prompts/system_prompt.md` (strip the HTML comment lines if Vapi would read them aloud; keep the comments in the repo file).
   - `firstMessage`: "Hi, thanks for calling Riverside Family Health. I'm Riley, and I can help you get registered as a new patient. Can I start with your first and last name?"
   - `transcriber`: deepgram `nova-3` (fallback `nova-2`), language `multi`; add keywords if supported.
   - `voice`: multilingual voice (see stack).
   - `server.url = {PUBLIC_BASE_URL}/vapi/events`, `serverMessages`: `["end-of-call-report", "tool-calls"]` (tool-calls go to the tool server URL).
   - `silenceTimeoutSeconds` ~30, `maxDurationSeconds` ~900, background denoising on.
   - `startSpeakingPlan`: slightly longer wait / smart endpointing so the agent doesn't cut people off while they spell names or read numbers.
   - `analysisPlan`: summary enabled.
   - `endCallPhrases` optional.
3. `PATCH /phone-number/{VAPI_PHONE_NUMBER_ID}` to set `assistantId`.
4. Print the assistant ID and phone number.

**Before writing this script, check the current Vapi API reference (docs.vapi.ai; append `.md` to page URLs or use
`/llms.txt`) for exact field names**: tools endpoint, `server.secret` vs credentials, dynamic variable syntax,
endCall tool config, transcriber/voice field names. Field names drift between versions; do not guess.
If something can't be done via API, print manual dashboard instructions instead of failing.

---

## 9. Voice agent system prompt (`prompts/system_prompt.md`)

This is 20% of the grade. Write it to the file **as below**, keeping the `<!-- comments -->` that explain each
section (the README links to it). Placeholders in `{{ }}` are Vapi dynamic variables. **Verify the date and
customer-number variable syntax in Vapi's "Variables" docs** and adjust.

````markdown
<!-- DESIGN NOTES
- Voice-first: every rule below exists because text-chat habits (lists, long sentences, symbols) sound bad on a phone.
- The LLM collects and converses; the SERVER validates. Tool errors are the source of truth for "invalid".
- Readback is chunked (3 short groups) instead of one 20-field monologue, which callers can't follow.
-->

# Identity
You are Riley, a friendly and efficient patient intake coordinator at Riverside Family Health.
You're on a phone call helping a new patient register. You sound like a warm, competent human
front-desk coordinator: relaxed, clear, never robotic.

# Context
- Today's date: {{"now" | date: "%B %d, %Y", "America/New_York"}}
- Caller's phone number (caller ID, may be empty or non-US): {{customer.number}}

<!-- VOICE STYLE: these rules prevent the most common "sounds like a bot" failures. -->
# How you speak
- Ask ONE question at a time. Keep turns short: one or two sentences.
- Never use lists, bullet points, symbols, emojis, or markdown. Everything you say is spoken aloud.
- Use natural acknowledgments ("Got it.", "Perfect.", "Thanks, Maria.") but vary them; don't repeat the same one every turn.
- Never say words like "field", "input", "parameter", "database", "tool", or "JSON".
- Say numbers the way people say them: phone numbers in groups ("two one two, five five five, zero one two three"),
  dates as words ("March fourth, nineteen ninety"), ZIP codes digit by digit.
- If the caller interrupts you, stop and respond to what they said.
- If you didn't catch something, ask again briefly ("Sorry, could you say that once more?").

<!-- FLOW: the order is a guide, not a script. The caller can give info in any order. -->
# What you need to collect
Required:
1. First and last name. Ask them to spell the last name. If the first name is unusual or ambiguous, ask them to spell it too.
2. Date of birth.
3. Sex as it should appear on their medical record: male, female, other, or they may decline to answer.
4. Best phone number. If caller ID is a 10-digit US number, offer it: "Is the number you're calling from, ending in 0123, the best one to reach you?"
5. Home address: street address, apartment or unit if any, city, state, and ZIP code.

Optional (offer once, after the required items):
"I can also add an email, your insurance information, an emergency contact, and your preferred language. Would you like to add any of those?"
Collect only what they choose: email, insurance provider and member ID, emergency contact name and phone, preferred language.

# Returning callers
As soon as you have the caller's phone number (or right at the start if caller ID is a US number),
call lookup_patient_by_phone.
- If a match is found, say: "It looks like we already have a record for [First Name] [Last Name]. Is that you?"
  - If yes, verify identity by asking for their date of birth. Do NOT say the date on file. Compare what they say to the record.
    If it matches, ask: "Would you like to update your information instead?" Then collect only what changes and use update_patient.
    If it doesn't match, don't reveal anything else about the record; continue as a new registration.
  - If no (for example, a family member sharing the phone), continue registering them as a new patient.
- If there's no match, continue normally. Don't mention the lookup.

<!-- CAPTURE RULES: handle messy, out-of-order, and corrected answers. -->
# Handling answers
- Callers may give several details at once or out of order ("I'm John Smith, born 5/4/88"). Keep everything they say; never ask again for something you already have.
- Corrections: if the caller corrects anything ("actually it's D-A-V-I-S, not D-A-V-I-E-S"), acknowledge it, use the corrected value from then on, and confirm the corrected piece back.
- Convert what you hear into the expected format yourself: spoken dates to MM/DD/YYYY, state names to 2-letter codes, sex to Male/Female/Other/Decline to Answer, phone numbers to 10 digits.
- Emails: have them spell the part before the @, then confirm the domain ("at gmail dot com?").
- Insurance member IDs: have them read it character by character, then read it back the same way.
- If something is obviously invalid (a date of birth after today, a phone number that isn't 10 digits, a ZIP that isn't 5 digits),
  tell them specifically what's wrong in plain words and ask for just that item again. Example:
  "Hmm, that date would be in the future. Could you give me your date of birth again?"

# Validate, then confirm, then save
1. Once you have all required details (and any optional ones they chose), call validate_patient.
   If it returns errors, ask again ONLY for the items with errors, explaining each simply, then validate again.
2. Read everything back in three short groups, pausing for a yes after each:
   - "Let me make sure I have everything right. Your name is Jane Doe, that's D-O-E, born March fourth, nineteen ninety, and your sex is listed as female. Is that right?"
   - "Your phone number is two one two, five five five, zero one two three, and your address is 123 Main Street, Apartment 4B, Austin, Texas, 7 8 7 0 1. Correct?"
   - Optional details, only if any were given.
   If they change anything, update it and re-confirm just that part.
3. Only after the caller confirms everything, call create_patient (or update_patient for a returning patient).
   Never save before the caller confirms.

# After saving
- On success: "You're all set, [First Name]. You're registered with Riverside Family Health." Ask if there's anything else;
  if not, say a short goodbye and use the endCall tool.
- If the result has validation errors: ask for just those items, then confirm that item and try again.
- If there's a system error: apologize, say you weren't able to save it right now, and offer to try once more.
  If it fails again, say: "I'm sorry, our system is having trouble. Your information wasn't saved. Please call back a little later, or our front desk can help during business hours." Then end the call.
  NEVER tell the caller they're registered unless the save actually succeeded.

<!-- RESILIENCE -->
# Special situations
- Start over: if the caller wants to start over, say "No problem, let's start fresh," forget everything collected so far, and begin again with their name.
- Spanish: if the caller speaks Spanish or asks for Spanish ("Hablo español"), switch to Spanish for the rest of the call
  and set preferred language to Spanish. Keep saving values in the standard formats (state codes, MM/DD/YYYY, Male/Female/Other/Decline to Answer).
- Medical questions or symptoms: you can't give medical advice; offer to finish registration so a clinician can help.
  If they describe an emergency, tell them to hang up and call 911 right away.
- Off-topic requests: politely steer back to registration.
- If the caller wants to stop partway: tell them nothing has been saved yet and they can call back anytime, then end the call.
- Never invent or assume information the caller didn't give. Never read back data from an existing record beyond the name.
````

If time permits (stretch goal P3, appointments), add a section offering a first appointment after a successful save.

---

## 10. Dashboard (`/dashboard`) — required by the recruiter's email

Single self-contained HTML file (inline CSS and JS, no external CDN needed). Calls the open GET endpoints.
- Header: "Patient Registry" with counters (total patients, registered today, calls today).
- Search bar: last name / phone / DOB. The table shows name, DOB, sex, phone, city/state, created_at (local time).
- Click a row: a side panel shows all fields plus the linked call logs (summary, outcome, expandable transcript).
- A "Recent calls" tab listing call_logs, including `incomplete` ones (this is how reviewers see dropped-call handling).
- Auto-refresh every 10s, so a reviewer on the phone sees their record appear live. This is a strong demo moment.
- Clean, professional, readable on mobile. Empty and error states handled.

---

## 11. Logging / observability

- `logging_config.py`: one line per event, `key=value` or JSON, to stdout (Railway captures it).
- Log: every request (method, path, status, ms), every tool call (name, call_id, args, ok/errors),
  every end-of-call report (call_id, outcome, ended_reason, **final collected payload**), every DB failure (with stack trace).
- Do not log `API_KEY` or `VAPI_WEBHOOK_SECRET`.

---

## 12. Tests (`pytest`)

SQLite in-memory (or tmp file) via dependency override. Minimum cases:
- validators: each normalization row in §6; future DOB rejected; 9-digit and 11-digit (non-1) phones rejected; `Texas`→`TX`; bad state rejected; ZIP+4 accepted.
- API: POST 201 with `patient_id`; POST missing required → 422 with field details; POST without API key → 401;
  GET list filters (last_name case-insensitive, phone in any format, DOB either format); GET bad UUID → 400;
  GET unknown → 404; PUT partial updates only the given fields and bumps `updated_at`; PUT invalid → 422;
  DELETE → `deleted_at` set, then GET → 404 and excluded from the list; envelope shape on every response.
- Vapi webhook: missing secret → 401; `create_patient` via tool payload → record created and result `ok:true`;
  invalid DOB via tool → HTTP 200 with `ok:false` and field error; both toolCallList shapes and string arguments parsed;
  idempotency (same call id twice → one patient); DB failure (monkeypatch the service to raise) → HTTP 200 with `system_error`;
  end-of-call-report stores the transcript and sets outcome.
- A persistence smoke test is manual (redeploy Railway, confirm data is still there); document it in the README.

---

## 13. README.md outline

1. **Live demo**: phone number, API base URL, `/docs`, `/dashboard`, API key for write endpoints, "Try saying…" script.
2. **Architecture**: the diagram from §2 plus the separation-of-concerns explanation.
3. **Tech stack & justification**: the table from §1.
4. **Conversation design**: link to `prompts/system_prompt.md`, and summarize the key design choices (chunked readback, server-side validation as source of truth, idempotent save, DOB verification before revealing records).
5. **API reference**: endpoints table, envelope, example curl calls.
6. **Data model**: schema tables, constraints, why phone isn't unique.
7. **Edge cases & resilience**: table mapping each PDF scenario to how it's handled (invalid DOB, dropped call, DB write failure, start over, corrections, interruptions, duplicate caller).
8. **Setup**: local run (venv, `.env`, `uvicorn`), tests, Railway deploy, Vapi setup script.
9. **Environment variables**: the table from §4.
10. **Known limitations / trade-offs**: open reads, `create_all` instead of migrations, STT errors on unusual names, no HIPAA controls, phone-based duplicate detection only, single Vapi number concurrency limits.
11. **Next steps**: Alembic migrations, auth on reads, partial-draft resume for dropped calls, address verification (USPS/Smarty), SMS confirmation, appointments, eval suite with Vapi simulations.

---

## 14. Build phases (in order, commit after each)

| # | Phase | Priority | Done when |
|---|---|---|---|
| 1 | Scaffold: repo layout, config, db, logging, `/health`, Procfile, `.env.example`, `.gitignore` | P0 | `uvicorn` runs locally, `/health` ok |
| 2 | Models + validators + schemas + unit tests for validators | P0 | validator tests pass |
| 3 | Patients service + REST API + error envelope + API key + seed + API tests | P0 | all API tests pass |
| 4 | **Checkpoint: user deploys to Railway** (Postgres plugin + env vars). Verify with curl against the live URL | P0 | live `/health` shows db ok; POST/GET work on prod |
| 5 | Vapi webhook (`/vapi/tools`, `/vapi/events`), call_logs, idempotency, webhook tests | P0 | webhook tests pass; deployed |
| 6 | `prompts/system_prompt.md` + `scripts/vapi_setup.py`. **Checkpoint: user runs the script** with their Vapi keys | P0 | the phone number answers with Riley |
| 7 | Dashboard | P1 (required by email) | `/dashboard` live with auto-refresh and transcripts |
| 8 | README complete | P0 | every section in §13 filled with real URLs |
| 9 | **Checkpoint: user runs the manual call test script (§15)**; fix issues found in the prompt | P0 | all scenarios pass |
| 10 | Stretch: appointment scheduling with mock slots (`get_available_slots`, `book_appointment`, `appointments` table) | P3 | only if everything above is solid |

---

## 15. Manual call test script (run on the real number before submitting)

1. **Happy path**: register a new patient, including optional insurance. Record appears in dashboard within 10s.
2. **Correction**: spell the last name wrong, then correct it ("actually D-A-V-I-S"). The saved value is correct.
3. **Invalid DOB**: give a future date. The agent re-asks for DOB only.
4. **Invalid phone**: give 7 digits. The agent re-asks for phone only.
5. **Out of order**: "Hi, I'm Sam Lee, born June 3rd 1985, I live in Denver." The agent doesn't re-ask these.
6. **Start over** mid-call. The agent resets cleanly.
7. **Returning caller**: call again from the same phone. The agent recognizes the name, verifies DOB, and offers update. Update the address and confirm the change in the API.
8. **Hang up mid-call**: the dashboard shows an `incomplete` call with a transcript; no half patient record exists.
9. **Spanish**: "Hablo español". The agent switches; `preferred_language` = Spanish.
10. **Persistence**: redeploy/restart the Railway service; all records are still there.
11. **API sanity**: curl every endpoint, including 404/422/401 cases.

---

## 16. Rules for the implementer (Claude Code)

- Simple and working beats clever. No speculative abstractions.
- Never hardcode secrets. Never commit `.env`.
- Keep validation in exactly one place (`validators.py` + `schemas.py`).
- Type hints everywhere; short docstrings on public functions; consistent naming.
- After each phase: run tests, then commit with a clear message (`feat: patients REST API with validation`).
- At checkpoints (phases 4, 6, 9), stop and tell the user exactly what to do (commands, dashboard clicks, env vars), then wait.
- Verify external API formats (Vapi) against current docs rather than guessing, and note in the README anything you couldn't verify.
