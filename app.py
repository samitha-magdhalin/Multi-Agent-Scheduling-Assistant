"""
app.py
Streamlit UI for the BrightPath Wellness Clinic Scheduling Assistant
(Triage Agent + Booking Specialist / Scheduling Coordinator).

Run locally:
    streamlit run app.py

Requires GROQ_API_KEY. Optionally set WEBHOOK_URL (e.g. a webhook.site URL)
to see real mock notification requests.
"""

import os
import uuid
import streamlit as st
from graph import build_graph, new_initial_state

st.set_page_config(page_title="BrightPath Scheduling Assistant", page_icon="🌿", layout="centered")

# Visual theme (colors, font, sidebar background) is set natively via
# .streamlit/config.toml — this is more robust than injected CSS since it's
# applied consistently by Streamlit itself to headers, sidebar, buttons, and
# the chat input, and won't break across Streamlit version updates.

st.title("🌿 BrightPath Wellness Clinic — Scheduling Assistant")
st.caption(
    "A Triage Agent routes general questions vs. appointment requests to a "
    "Scheduling Coordinator, which validates dates/times and books your visit."
)

api_key = os.environ.get("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", None)
if not api_key:
    api_key = st.text_input("Enter your Groq API key to begin:", type="password")
    if not api_key:
        st.info("An API key is required to run the assistant.")
        st.stop()

# --- Thread ID lives in the URL query params (not just session_state), so an
# actual browser refresh (F5) keeps ?thread_id=... in the address bar and
# automatically reconnects to the same SqliteSaver-persisted conversation —
# this is what makes state genuinely "survive a refresh" rather than
# requiring the user to manually copy/paste a thread ID. ---
if "thread_id" not in st.query_params:
    st.query_params["thread_id"] = str(uuid.uuid4())
st.session_state.thread_id = st.query_params["thread_id"]
config = {"configurable": {"thread_id": st.session_state.thread_id}}

def load_thread_state(config):
    current = st.session_state.graph_app.get_state(config)
    thread_state = new_initial_state()
    if current and current.values:
        thread_state.update(dict(current.values))
    return thread_state

if "graph_app" not in st.session_state:
    st.session_state.graph_app = build_graph(api_key=api_key)

# --- Rebuild the visible chat log from the persisted graph state whenever we
# don't already have it in session_state (i.e. right after a real refresh).
# This is what makes the chat log itself reappear after F5, not just the
# agent's internal memory. ---
if "display_messages" not in st.session_state:
    current = st.session_state.graph_app.get_state(config)
    if current and current.values.get("messages"):
        st.session_state.display_messages = list(current.values["messages"])
    else:
        st.session_state.display_messages = []

thread_state = load_thread_state(config)
example_user_input = None

with st.sidebar:
    st.header("Session")
    st.caption("Your thread ID lives in the page URL — refreshing this page keeps your conversation.")
    st.text_input(
        "Thread ID (shareable via URL)",
        value=st.session_state.thread_id,
        key="thread_display",
        disabled=True,
    )
    resume_id = st.text_input("Resume a different Thread ID")
    if st.button("Resume") and resume_id:
        st.query_params["thread_id"] = resume_id
        st.session_state.pop("display_messages", None)
        st.rerun()
    if st.button("Start new conversation"):
        st.query_params["thread_id"] = str(uuid.uuid4())
        st.session_state.pop("display_messages", None)
        st.rerun()
    st.divider()

    st.subheader("Booking summary")
    if thread_state["date"] or thread_state["time"] or thread_state["email"]:
        st.write(f"**Date:** {thread_state['date'] or '—'}")
        st.write(f"**Time:** {thread_state['time'] or '—'}")
        st.write(f"**Email:** {thread_state['email'] or '—'}")
        if thread_state["stage"] == "confirmed":
            st.success("✅ Booking confirmed")
        elif thread_state["stage"] == "negotiating":
            st.info("Waiting for your preferred alternative time or date.")
        else:
            st.info("Collecting booking details...")
    else:
        st.write("No booking details collected yet.")
    st.divider()

    st.subheader("Example questions")
    if st.button("What services do you offer?"):
        example_user_input = "Hi, what services does the clinic offer?"
    if st.button("Book tomorrow at 10am"):
        example_user_input = "I'd like to book an appointment tomorrow at 10am, email me at jane@example.com"
    if st.button("Book 2026-07-15 at 09:00"):
        example_user_input = "Book me for 2026-07-15 at 09:00, my email is jane@example.com"

for msg in st.session_state.display_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if example_user_input:
    user_input = example_user_input
else:
    user_input = st.chat_input("Type your message...")

if user_input:
    st.session_state.display_messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Working on it..."):
            # Pull existing persisted state for this thread (if any), append new message
            current = st.session_state.graph_app.get_state(config)
            if current and current.values.get("messages"):
                state = dict(current.values)
            else:
                state = new_initial_state()
            state["messages"] = state.get("messages", []) + [{"role": "user", "content": user_input}]

            result = st.session_state.graph_app.invoke(state, config=config)
            reply = result.get("reply", "Sorry, I didn't catch that — could you rephrase?")
            st.markdown(reply)

            # Persist the assistant's reply into the same "messages" list so a
            # future refresh can rebuild the full visible chat log (see above),
            # not just resume the agent's internal booking-state memory.
            full_messages = result.get("messages", []) + [{"role": "assistant", "content": reply}]
            st.session_state.graph_app.update_state(config, {"messages": full_messages})

    st.session_state.display_messages.append({"role": "assistant", "content": reply})

    if result.get("stage") == "confirmed":
        st.success("Your appointment is booked! Here are the details:")
        st.write(f"**Date:** {result.get('date')}  ")
        st.write(f"**Time:** {result.get('time')}  ")
        st.write(f"**Email:** {result.get('email')}  ")
        if "confirmation" in reply.lower():
            st.caption(reply)
