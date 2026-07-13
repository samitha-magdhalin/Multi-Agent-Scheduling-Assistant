# BrightPath Wellness Clinic — Multi-Agent Scheduling Assistant

A Streamlit app powered by a LangGraph state machine with two agents — a
**Triage Agent** and a **Booking Specialist** — that handles appointment
scheduling for a fictional wellness clinic, **BrightPath Wellness Clinic**.
It validates dates/times, negotiates alternatives when a slot is taken, and
sends mock confirmations.

## Screenshot

![BrightPath Scheduling Assistant dashboard](screenshots/dashboard.png)

*Chat interface showing the Triage Agent routing a general question, followed
by the Booking Specialist confirming an appointment.*

## Tech Stack

Python, Streamlit, LangGraph, LangChain Groq, Groq API, Llama 3.3 70B, SQLite, `dateparser`, Streamlit Community Cloud, Git, GitHub

## Architecture

```
User message
    │
    ▼
┌─────────────┐   general question    ┌────────────────┐
│ Triage Agent│ ───────────────────▶  │ General Reply  │
└─────────────┘                       └────────────────┘
    │ booking intent
    ▼
┌────────────────────┐
│ Booking Specialist  │
│  - extract fields   │  (LLM extracts date phrase / time / email)
│  - normalize date    │  (dateparser resolves "tomorrow" → YYYY-MM-DD
│    ("tomorrow"→ISO)  │   using the REAL current date)
│  - check_availability│ ─▶ tools.py (SQLite-backed mock calendar)
│  - reserve_slot       │ ─▶ tools.py
│  - negotiate if taken │
│  - send_notification  │ ─▶ mock webhook (webhook.site / Pipedream)
└────────────────────┘
    │
    ▼
Reply shown to user + state checkpointed to SQLite (survives refresh)
```

### Agents
- **Triage Agent** (`triage_node` in `graph.py`): classifies each incoming
  message as `general` or `booking` intent using Llama 3.3 70B (via Groq),
  and routes control accordingly. Once a booking conversation is in progress,
  subsequent turns stay routed to the Booking Specialist until the booking is
  confirmed. (Fixed a routing bug where the initial conversation state
  defaulted to `"collecting"`, which caused every first message to bypass
  the Triage Agent — the default is now a neutral `"idle"` state.)
- **Booking Specialist** (`booking_node` in `graph.py`): extracts date/time/
  email from the message, resolves relative dates (e.g. "tomorrow", "next
  Friday") to a real `YYYY-MM-DD` string via `dateparser` anchored to the
  actual current date/time — done deterministically, not left to the LLM to
  guess. It then calls the mocked tools and negotiates alternative slots if a
  requested time is unavailable, rather than failing silently.

### Tools (`tools.py`, mocked but functional)
- `check_availability(date)` — reads booked slots from a local SQLite table
  and returns the remaining free slots for that day.
- `reserve_slot(date, time, email)` — inserts a row into SQLite; a `UNIQUE`
  constraint prevents double-booking (so simultaneous requests fail safely).
- `send_booking_notification(email, details)` — POSTs a JSON payload to a
  mock webhook endpoint (e.g. https://webhook.site) to simulate an email/
  WhatsApp confirmation. Set `WEBHOOK_URL` in `.env` to see the requests land
  on a real (free) endpoint; otherwise it no-ops safely.

### State persistence
LangGraph's `SqliteSaver` checkpoints the full conversation state keyed by
`thread_id` into `checkpoints.sqlite`. The `thread_id` itself lives in the
page's URL query params (`?thread_id=...`), so an actual browser refresh
(F5) keeps the same URL and **automatically** reconnects to the same
conversation — both the agent's internal booking-state memory and the
visible chat log are restored, not just resumable via manual copy/paste.
The sidebar also lets you deliberately switch to a different thread ID
(e.g. to share/resume a specific conversation on another device) or start
a fresh one.

## Setup (local)

```bash
git clone <your-repo-url>
cd assignment2-scheduling-agent
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: add GROQ_API_KEY, optionally WEBHOOK_URL

streamlit run app.py
```

> **Note on Python version**: Python 3.11 or 3.12 is recommended over very
> new releases (e.g. 3.14) for smooth, no-compiler-needed installs, and to
> avoid a known LangChain-core/Pydantic incompatibility with Python 3.14's
> newer type-annotation evaluation.

## Deploying to Render (free tier)

Render is the recommended free-tier host for this project — unlike Vercel
(built for serverless/static apps), Render runs a real persistent Python web
service, which Streamlit needs.

1. Push this folder to a public GitHub repo, including:
   - `.streamlit/config.toml` (theme — not secrets)
   - `.python-version` (pins the Python version — see below)
2. Go to https://dashboard.render.com → **New +** → **Web Service** → connect
   your GitHub repo.
3. Configure:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**:
     ```
     streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
     ```
     (Render assigns the port dynamically via the `$PORT` env var — Streamlit
     must bind to it and to `0.0.0.0`, not `localhost`, to be reachable.)
4. Under **Environment**, add:
   ```
   GROQ_API_KEY = your-key-here
   WEBHOOK_URL = https://webhook.site/your-unique-id   (optional)
   ```
5. Click **Create Web Service**. Render builds and deploys automatically; it
   also auto-redeploys on every push to your branch.

> **Note on Python version**: Render's current default Python version is
> **3.14.3** (as of services created after 2026-02-11) — the same version
> that causes the LangChain/Pydantic typing incompatibility mentioned above.
> The `.python-version` file in this repo (containing `3.11`) tells Render to
> use Python 3.11 instead. This only needs to be a file in your repo root —
> no dashboard setting required. (Render also supports overriding via a
> `PYTHON_VERSION` environment variable if you prefer that instead.)

> **Note on persistence on Render's free tier**: free-tier services spin
> down after a period of inactivity and lose their local disk on redeploy —
> so `scheduling.db`/`checkpoints.sqlite` will reset then too, same caveat as
> below for Streamlit Cloud. This is normal for a free-tier demo.

## Deploying to Streamlit Community Cloud (free, alternative)

1. Push this folder to a public GitHub repo, including the `.streamlit/`
   folder (theme config, not secrets).
2. Go to https://share.streamlit.io → "New app" → select repo/branch, main
   file path `app.py`.
3. **Before deploying**, click **"Advanced settings"** and set the Python
   version to **3.11** (Streamlit Cloud currently defaults to a very new
   Python version that has compatibility issues with LangChain — see note
   above). This can only be set at deploy time; if you need to change it on
   an already-deployed app, delete and redeploy.
4. In **Secrets**, add:
   ```toml
   GROQ_API_KEY = "your-key-here"
   WEBHOOK_URL = "https://webhook.site/your-unique-id"
   ```
5. Deploy.

> Note: Streamlit Community Cloud apps use ephemeral storage — the SQLite
> files (`scheduling.db`, `checkpoints.sqlite`) will reset if the app
> container restarts/redeploys. This still fully satisfies the assignment's
> "survives page refresh" requirement within a running session; for
> production use you'd swap SQLite for a hosted Postgres/Redis checkpointer.

## Example conversation

```
User: Hi, what services does the clinic offer?
Agent: (general reply, routed by Triage Agent)

User: I'd like to book an appointment tomorrow at 10am, my email is jane@example.com
Agent: [resolves "tomorrow" to real date] → checks availability → reserves →
        "Appointment confirmed for 2026-07-14 at 10:00. A confirmation
        notification has been sent."

User: Actually book me for 10am again on the same day
Agent: "Sorry, 10:00 on 2026-07-14 is already booked. Available slots that
        day are: 09:00, 11:00, 14:00, 15:00, 16:00. Which would you like
        instead?"
```

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit chat UI with thread persistence controls |
| `graph.py` | LangGraph state machine: Triage + Booking Specialist nodes |
| `tools.py` | Mocked calendar tools + mock notification webhook |
| `.streamlit/config.toml` | Native Streamlit theme |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for required environment variables |
#   M u l t i - A g e n t - S c h e d u l i n g - A s s i s t a n t  
 