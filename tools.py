"""
tools.py
Mocked-but-functional tools used by the Scheduling Coordinator agent for
BrightPath Wellness Clinic.

- check_availability(date): looks up a mock in-memory/SQLite appointment calendar
- reserve_slot(date, time, email): writes a reservation to a local SQLite DB
- send_booking_notification(email, details): fires a mock webhook (e.g. webhook.site)
  to simulate sending an email/WhatsApp appointment confirmation
"""

import sqlite3
import os
import requests
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "scheduling.db"

# Appointment slots offered every day at the clinic, unless already booked
AVAILABLE_SLOTS = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]

# Optional: set WEBHOOK_URL env var to a real https://webhook.site/<id> or
# Pipedream endpoint to see real mock notification requests land somewhere.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://webhook.site/#!/unique-id-here")


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(date, time)
        )
        """
    )
    conn.commit()
    conn.close()


_init_db()


def check_availability(date: str) -> dict:
    """Return which slots are still free for a given YYYY-MM-DD date."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT time FROM reservations WHERE date = ?", (date,))
    booked = {row[0] for row in cur.fetchall()}
    conn.close()

    free = [slot for slot in AVAILABLE_SLOTS if slot not in booked]
    return {
        "date": date,
        "free_slots": free,
        "booked_slots": sorted(booked),
        "fully_booked": len(free) == 0,
    }


def reserve_slot(date: str, time: str, email: str) -> dict:
    """Attempt to reserve a slot. Returns success/failure so the agent can
    negotiate an alternative instead of failing silently."""
    if time not in AVAILABLE_SLOTS:
        return {"success": False, "reason": f"'{time}' is not a valid business-hours slot."}

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO reservations (date, time, email, created_at) VALUES (?, ?, ?, ?)",
            (date, time, email, datetime.utcnow().isoformat()),
        )
        conn.commit()
        success = True
        reason = "Slot reserved successfully."
    except sqlite3.IntegrityError:
        success = False
        reason = f"The {time} slot on {date} is already taken."
    finally:
        conn.close()

    return {"success": success, "reason": reason, "date": date, "time": time, "email": email}


def send_booking_notification(email: str, details: str) -> dict:
    """Simulate sending a confirmation email/WhatsApp message by POSTing to a
    mock webhook endpoint (webhook.site / Pipedream). Never raises on network
    failure — notification failures shouldn't crash the booking flow."""
    payload = {"to": email, "message": details, "sent_at": datetime.utcnow().isoformat()}
    try:
        if WEBHOOK_URL and "unique-id-here" not in WEBHOOK_URL:
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=5)
            delivered = resp.ok
        else:
            # No real webhook configured — just log locally so the flow still works.
            delivered = True
        return {"delivered": delivered, "payload": payload}
    except requests.RequestException as e:
        return {"delivered": False, "payload": payload, "error": str(e)}
