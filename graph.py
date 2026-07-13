"""
graph.py
Multi-agent scheduling workflow built with LangGraph.

Agents:
- Triage Agent: classifies each incoming message as "general" or "booking intent"
  and routes accordingly.
- Booking Specialist: extracts date/time/email, normalizes relative dates
  ("tomorrow") to YYYY-MM-DD using the actual current date, calls the mocked
  tools (check_availability, reserve_slot, send_booking_notification), and
  negotiates alternative slots instead of failing silently.

State persistence uses LangGraph's SqliteSaver so conversation threads survive
page refreshes (keyed by thread_id).
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import TypedDict, Optional, Literal

import dateparser
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from tools import check_availability, reserve_slot, send_booking_notification


class BookingState(TypedDict):
    messages: list  # list of {"role": "user"/"assistant", "content": str}
    intent: Optional[Literal["general", "booking"]]
    date: Optional[str]       # resolved YYYY-MM-DD
    time: Optional[str]       # HH:MM
    email: Optional[str]
    stage: str                 # "collecting" | "confirmed" | "negotiating"
    reply: Optional[str]       # the agent's latest reply to show the user


def _llm(api_key: Optional[str] = None):
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0,
        api_key=api_key or os.environ.get("GROQ_API_KEY"),
    )


def _last_user_message(state: BookingState) -> str:
    for msg in reversed(state["messages"]):
        if msg["role"] == "user":
            return msg["content"]
    return ""


# ---------------------------------------------------------------------------
# TRIAGE AGENT
# ---------------------------------------------------------------------------
def triage_node(state: BookingState, api_key: Optional[str] = None) -> BookingState:
    user_msg = _last_user_message(state)
    llm = _llm(api_key)

    prompt = f"""Classify the customer's message as either "booking" (they want to
schedule, check, reschedule, or book an appointment) or "general" (anything else,
e.g. greetings or general questions).

Message: "{user_msg}"

Respond with ONLY one word: booking or general."""

    result = llm.invoke(prompt).content.strip().lower()
    intent = "booking" if "booking" in result else "general"

    new_state = dict(state)
    new_state["intent"] = intent
    if intent == "general" and state.get("stage") != "collecting":
        new_state["reply"] = (
            "I'm BrightPath Wellness Clinic's scheduling assistant. I can answer general "
            "questions, or help you check availability and book an appointment — just let me know!"
        )
    return new_state


def route_after_triage(state: BookingState) -> str:
    # Only skip triage if we're genuinely mid-booking-conversation already
    # (i.e. a previous turn started collecting slot details or was negotiating).
    if state.get("stage") in ("collecting", "negotiating"):
        return "booking"
    return "booking" if state["intent"] == "booking" else "general_reply"


def general_reply_node(state: BookingState) -> BookingState:
    new_state = dict(state)
    if not new_state.get("reply"):
        new_state["reply"] = (
            "Happy to help! I can answer general questions, or schedule/check/book "
            "an appointment for you — just tell me what you'd like to do."
        )
    return new_state


# ---------------------------------------------------------------------------
# BOOKING SPECIALIST
# ---------------------------------------------------------------------------
def _resolve_relative_date(text: str) -> Optional[str]:
    """Turn phrases like 'tomorrow', 'next Monday' into an actual YYYY-MM-DD
    string based on the real current date, per the Input Normalization rule."""
    parsed = dateparser.parse(
        text,
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    return None


def _extract_booking_fields(user_msg: str, state: BookingState, api_key: Optional[str]) -> dict:
    """Use the LLM to extract date phrase / time / email from the message,
    then normalize the date deterministically (not left to the LLM to guess)."""
    llm = _llm(api_key)
    today_str = datetime.now().strftime("%Y-%m-%d (%A)")

    prompt = f"""Today's date is {today_str}.
Extract booking details from the customer's message as JSON with keys:
"date_phrase" (the raw date expression they used, e.g. "tomorrow", "next Friday", "2026-07-15", or null),
"time" (in 24h HH:MM format if mentioned, else null),
"email" (if mentioned, else null).

Message: "{user_msg}"

Respond with ONLY valid JSON, no other text."""

    raw = llm.invoke(prompt).content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"date_phrase": None, "time": None, "email": None}

    resolved_date = state.get("date")
    if data.get("date_phrase"):
        resolved = _resolve_relative_date(data["date_phrase"])
        if resolved:
            resolved_date = resolved

    return {
        "date": resolved_date,
        "time": data.get("time") or state.get("time"),
        "email": data.get("email") or state.get("email"),
    }


def booking_node(state: BookingState, api_key: Optional[str] = None) -> BookingState:
    user_msg = _last_user_message(state)
    new_state = dict(state)
    new_state["stage"] = "collecting"

    fields = _extract_booking_fields(user_msg, state, api_key)
    new_state["date"] = fields["date"]
    new_state["time"] = fields["time"]
    new_state["email"] = fields["email"]

    missing = []
    if not new_state["date"]:
        missing.append("date")
    if not new_state["time"]:
        missing.append("preferred time")
    if not new_state["email"]:
        missing.append("email address")

    if missing:
        new_state["reply"] = (
            f"Sure, I can help you schedule that! Could you tell me your {', '.join(missing)}? "
            f"(Clinic hours slots: 09:00, 10:00, 11:00, 14:00, 15:00, 16:00)"
        )
        return new_state

    # We have date, time, and email -> check availability first
    availability = check_availability(new_state["date"])
    if new_state["time"] not in availability["free_slots"]:
        # Negotiate: offer real alternatives instead of failing silently
        new_state["stage"] = "negotiating"
        new_state["time"] = None  # ask again
        if availability["free_slots"]:
            new_state["reply"] = (
                f"Sorry, {fields['time']} on {new_state['date']} is already booked. "
                f"Available slots that day are: {', '.join(availability['free_slots'])}. "
                f"Which would you like instead?"
            )
        else:
            new_state["reply"] = (
                f"Unfortunately {new_state['date']} is fully booked. "
                f"Could you try a different date?"
            )
            new_state["date"] = None
        return new_state

    # Slot looks free -> attempt reservation (double-checked at DB level for race conditions)
    reservation = reserve_slot(new_state["date"], new_state["time"], new_state["email"])
    if not reservation["success"]:
        new_state["stage"] = "negotiating"
        availability = check_availability(new_state["date"])
        new_state["time"] = None
        new_state["reply"] = (
            f"{reservation['reason']} Other available slots: "
            f"{', '.join(availability['free_slots']) or 'none left that day'}. "
            f"Which would you like instead?"
        )
        return new_state

    details = f"Appointment confirmed for {new_state['date']} at {new_state['time']}."
    notification = send_booking_notification(new_state["email"], details)
    new_state["stage"] = "confirmed"
    confirmation_note = (
        " A confirmation notification has been sent."
        if notification["delivered"]
        else " (Note: the confirmation notification could not be delivered, but your booking is saved.)"
    )
    new_state["reply"] = f"{details}{confirmation_note}"
    return new_state


# ---------------------------------------------------------------------------
# GRAPH ASSEMBLY
# ---------------------------------------------------------------------------
def build_graph(api_key: Optional[str] = None, db_path: str = "checkpoints.sqlite"):
    graph = StateGraph(BookingState)

    graph.add_node("triage", lambda s: triage_node(s, api_key))
    graph.add_node("booking", lambda s: booking_node(s, api_key))
    graph.add_node("general_reply", general_reply_node)

    graph.set_entry_point("triage")
    graph.add_conditional_edges(
        "triage", route_after_triage, {"booking": "booking", "general_reply": "general_reply"}
    )
    graph.add_edge("booking", END)
    graph.add_edge("general_reply", END)

    # SqliteSaver persists thread state (per thread_id) across page refreshes.
    # Using a raw sqlite3 connection directly (check_same_thread=False for Streamlit's
    # threaded execution model) keeps this compatible across langgraph versions.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return graph.compile(checkpointer=checkpointer)


def new_initial_state() -> BookingState:
    return {
        "messages": [],
        "intent": None,
        "date": None,
        "time": None,
        "email": None,
        "stage": "idle",
        "reply": None,
    }
