# Voice AI Patient Registration

A phone-based patient intake system. A caller dials a US number, a voice agent
collects their demographic information conversationally, reads it back for
confirmation, and persists it. A REST API exposes the stored records.

| | |
|---|---|
| **Phone number** | `+1 (XXX) XXX-XXXX` — *fill in after provisioning* |
| **API base URL** | `https://<your-app>.onrender.com` — *fill in after deploying* |
| **Interactive docs** | `<API base URL>/docs` |

---

## Architecture

```
  Caller (PSTN)
       │
       ▼
  ┌─────────────────────────────────────────┐
  │ Vapi                                    │
  │  telephony · Deepgram STT · GPT-4o      │
  │  · ElevenLabs TTS                       │
  │  system prompt + 3 tool definitions     │
  └───────────────┬─────────────────────────┘
                  │  HTTPS tool calls
                  │  (x-vapi-secret)
                  ▼
  ┌─────────────────────────────────────────┐
  │ FastAPI                                 │
  │                                         │
  │  routers/vapi.py  ──┐                   │
  │                     ├──► crud.py ──┐    │  one service layer,
  │  routers/patients.py┘   (service)  │    │  two entry points
  │                                    │    │
  │  schemas.py · validators.py        │    │
  └────────────────────────────────────┼────┘
                                       ▼
                            ┌────────────────────┐
                            │ Supabase Postgres  │
                            └────────────────────┘
                                       ▲
  Reviewer ── GET /patients ───────────┘
```

**The key structural decision:** the voice tool handler and the REST router both
call the same functions in `app/crud.py`, and both validate through the same
Pydantic schemas in `app/schemas.py`. The agent has no privileged path to the
database — a record created by voice is subject to exactly the same constraints
as one created by `POST /patients`. The brief explicitly permits either calling
the REST API over HTTP or invoking the shared service layer; the latter was
chosen because an extra HTTP hop mid-call costs latency and adds a failure mode
for no benefit.

### Layout

```
app/
  main.py          FastAPI app, envelope-consistent exception handlers
  config.py        env-var settings (pydantic-settings)
  database.py      SQLAlchemy engine + session
  models.py        Patient ORM model, DB-level constraints
  schemas.py       Pydantic request/response schemas
  validators.py    speech-tolerant normalisation (see below)
  crud.py          service layer — the single write path
  routers/
    patients.py    the five REST endpoints
    vapi.py        Vapi tool-call webhook
prompts/
  assistant_prompt.md   system prompt + prompt-engineering rationale
scripts/
  create_assistant.py   creates the Vapi assistant from this repo
vapi/
  assistant_config.json generated reference copy of the assistant payload
tests/
  test_api.py      19 integration tests
seed.py            demo records
```

---

## Tech stack, and why

| Layer | Choice | Reasoning |
|---|---|---|
| Telephony + STT/TTS | **Vapi** | Provisions a real US number and handles the media pipeline. Building STT/TTS would consume the entire time budget and is not what the brief evaluates. |
| LLM | **GPT-4o** via Vapi | Strong instruction-following on multi-field extraction and self-correction. Billed through Vapi credits, so no second API account. |
| Backend | **FastAPI** | Pydantic gives request validation and OpenAPI docs for free — both are scored, and `/docs` doubles as a demo surface. |
| Database | **Supabase Postgres** | Managed, persistent, free tier without a credit card. Avoids the ephemeral-filesystem trap that silently wipes SQLite on PaaS redeploys. |
| ORM | **SQLAlchemy 2.0** (sync) | Sync `psycopg2` sidesteps asyncpg + PgBouncer prepared-statement failures. Concurrency here is one phone call at a time; async buys nothing. |
| Hosting | **Render** | Free tier, deploys from `render.yaml`. See the cold-start caveat under Limitations. |

### Agent design

The prompt and its rationale are in
[`prompts/assistant_prompt.md`](prompts/assistant_prompt.md). The decisions that
most affect call quality:

- **One question per turn.** Multi-field questions reliably produce partial
  answers and a confused follow-up.
- **Digits read in chunks.** `"seven one three... two one zero..."` survives a
  phone codec; a ten-digit string spoken flat does not.
- **Spelling confirmation on names only.** Over-confirming every field doubles
  call length; names are where homophone errors are both likeliest and costliest.
- **Tool errors are the recovery script.** `register_patient` returns
  `validation_errors` keyed by field, and the prompt instructs the model to
  re-prompt for exactly those fields. That is what produces *"I think I got your
  birth year wrong"* instead of *"something went wrong"*.
- **Optional fields offered once, as a group**, opt-in — as the brief specifies.

### Speech-tolerant validation

`app/validators.py` exists because speech-to-text output is messier than form
input. It normalises before validating, so the agent does not re-prompt for
things a human receptionist would simply have understood:

| Caller says | Stored |
|---|---|
| "March fifth, nineteen ninety" | `1990-03-05` |
| "California" | `CA` |
| "(713) 210-1234" / "+1 713 210 1234" | `7132101234` |
| "nizam at gmail dot com" | `nizam@gmail.com` |
| "prefer not to say" | `Decline to Answer` |
| "seven seven zero zero two one two three four" | `77002-1234` |

Genuinely invalid input is still rejected, with a message written to be spoken
aloud — a future date of birth returns *"Date of birth cannot be in the
future."*, not a schema error.

---

## API

All responses use the envelope `{"data": ..., "error": ...}`, including errors.

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/patients` | Filters: `?last_name=`, `?date_of_birth=`, `?phone_number=`, `?include_deleted=` |
| `GET` | `/patients/{id}` | `404` if missing or soft-deleted |
| `POST` | `/patients` | `201` + `Location` header |
| `PUT` | `/patients/{id}` | Partial updates; unknown fields rejected |
| `DELETE` | `/patients/{id}` | Soft delete — stamps `deleted_at` |
| `GET` | `/health` | Liveness probe / keep-warm target |
| `POST` | `/vapi/tools` | Vapi tool webhook (not for manual use) |

Status codes: `200`, `201`, `400` (bad query/empty update), `404`, `422`
(validation, with per-field detail), `500`.

```bash
curl -X POST "$API/patients" -H 'Content-Type: application/json' -d '{
  "first_name":"Jane","last_name":"Doe","date_of_birth":"03/05/1990",
  "sex":"Female","phone_number":"7132101234","address_line_1":"12 Oak St",
  "city":"Houston","state":"TX","zip_code":"77002"}'
```

Query filters normalise their input too, so `?phone_number=(713) 210-1234` and
`?phone_number=7132101234` return the same record.

### Voice agent tools

| Tool | Purpose |
|---|---|
| `lookup_patient` | Called right after the greeting. Detects returning callers by caller ID and offers to update instead of duplicating. |
| `register_patient` | Called only after the caller confirms the read-back. Returns per-field `validation_errors` on failure. |
| `update_patient` | Partial update of an existing record. |

The caller's number is read from Vapi's `call.customer.number` and used as a
fallback, so a record is never saved without a phone number even if the agent
forgets to ask.

---

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in DATABASE_URL
uvicorn app.main:app --reload
```

`http://localhost:8000/docs` for interactive docs. Omitting `DATABASE_URL`
falls back to local SQLite, which is fine for development.

```bash
python seed.py              # optional demo records
pytest -q                   # 19 tests, uses a throwaway SQLite file
```

To expose it to Vapi while developing:

```bash
cloudflared tunnel --url http://localhost:8000
```

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes in prod | Supabase **session pooler** URI (port `5432` on `*.pooler.supabase.com`). Not the direct `db.<ref>.supabase.co` host, which is IPv6-only, and not the `6543` transaction pooler, which breaks prepared statements. |
| `VAPI_SERVER_SECRET` | recommended | Shared secret Vapi sends as `x-vapi-secret`. |
| `ALLOW_UNAUTHENTICATED_TOOLS` | no | `true` while wiring up; set `false` in production to enforce the secret. |
| `VAPI_PRIVATE_KEY` | script only | Needed by `scripts/create_assistant.py`. |
| `PUBLIC_BASE_URL` | script only | Public HTTPS base URL of this API. |

No secrets are committed; `.env` is gitignored.

---

## Deploying

1. **Supabase** — create a project in a US region, copy the *session pooler*
   connection string.
2. **Render** — *New → Blueprint*, point at this repo. `render.yaml` supplies
   build and start commands; set `DATABASE_URL` and `VAPI_SERVER_SECRET` when
   prompted. Verify `GET /health`.
3. **Vapi assistant:**
   ```bash
   export VAPI_PRIVATE_KEY=...  PUBLIC_BASE_URL=https://your-app.onrender.com
   export VAPI_SERVER_SECRET=...
   python scripts/create_assistant.py
   ```
   Then attach the assistant to your phone number (the script prints the call).
4. **Keep-warm** — point a free uptime pinger at `/health` every 10 minutes.

---

## Known limitations and trade-offs

- **Render's free tier sleeps after 15 minutes idle** and takes ~50s to wake,
  which would strand a caller mid-conversation. Mitigated with a `/health`
  pinger; a paid instance or a small VPS removes the problem properly. This was
  a deliberate cost trade-off, not an oversight.
- **`create_all()` instead of migrations.** Appropriate for a fresh schema under
  a time limit; a longer-lived service needs Alembic.
- **Transcripts are logged, not stored.** `end-of-call-report` events are written
  to stdout but not persisted to a table linked to the patient record.
- **Duplicate detection is by phone number only.** Two family members sharing a
  landline would collide. Name + DOB matching would be the next refinement.
- **No auth on the REST API.** The brief did not require it and the data is
  synthetic. Production would need at minimum an API key on the patient routes.
- **`updated_at` relies on the ORM's `onupdate`**, so a write issued directly in
  SQL would not refresh it. A database trigger would be more robust.
- **Not HIPAA compliant, by design** — no encryption at rest beyond Supabase's
  defaults, no audit log, no BAA. Do not put real patient data in it.
- **English only.** The Spanish-switching bonus is not implemented; it needs a
  multilingual transcriber config and a second voice.

## Next steps

1. Persist call transcripts to a `call_logs` table keyed by `patient_id`.
2. Alembic migrations.
3. API-key auth plus per-IP rate limiting on the patient routes.
4. Name + date-of-birth fuzzy matching for duplicate detection.
5. Multi-language support triggered by `"hablo español"`.
6. A small read-only dashboard over `GET /patients`.
