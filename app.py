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
from dotenv import load_dotenv
from graph import build_graph, new_initial_state

load_dotenv()  # loads GROQ_API_KEY (and WEBHOOK_URL) from a local .env file, if present

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


def get_secret(key: str):
    """st.secrets.get() raises StreamlitSecretNotFoundError (rather than
    returning None) when no secrets.toml file exists at all — which is the
    normal case for local development using only a .env file. This wraps
    that access safely so local runs don't crash."""
    try:
        return st.secrets.get(key, None)
    except Exception:
        return None


api_key = os.environ.get("GROQ_API_KEY") or get_secret("GROQ_API_KEY")
if not api_key:
    api_key = st.text_input("Enter your Groq API key to begin:", type="password")
    if not api_key:
        st.info("An API key is required to run the assistant.")
        st.stop()

# --- Thread ID lives in the URL query params (not just session_state), so an
# actual browser refresh (F5) keeps ?thread_id=... in the address bar and
# automatically reconnects to the same SqliteSaver-persisted conversation. ---
if "thread_id" not in st.query_params:
    st.query_params["thread_id"] = str(uuid.uuid4())
st.session_state.thread_id = st.query_params["thread_id"]
config = {"configurable": {"thread_id": st.session_state.thread_id}}

if "graph_app" not in st.session_state:
    st.session_state.graph_app = build_graph(api_key=api_key)

# --- Rebuild the visible chat log + booking-summary state from the persisted
# graph state whenever we don't already have it in session_state (i.e. right
# after a real refresh). ---
if "display_messages" not in st.session_state:
    current = st.session_state.graph_app.get_state(config)
    if current and current.values.get("messages"):
        st.session_state.display_messages = list(current.values["messages"])
        st.session_state.last_state = dict(current.values)
    else:
        st.session_state.display_messages = []
        st.session_state.last_state = {}

EXAMPLE_QUESTIONS = [
    "What services does the clinic offer?",
    "I'd like to book tomorrow at 10am, email a@b.com",
    "Book me for 2026-07-20 at 09:00, jane@example.com",
]


def render_reply(reply: str, stage: str):
    """Nicer visual treatment when a booking is actually confirmed, vs. a
    plain message for everything else (general replies, follow-up prompts,
    negotiation)."""
    if stage == "confirmed":
        st.success(f"🎉 **Booking Confirmed!**\n\n{reply}")
    else:
        st.markdown(reply)


def ask_question(question: str):
    """Shared handler so both typed input and example-question buttons run
    through the exact same graph invocation + persistence logic."""
    st.session_state.display_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Working on it..."):
            current = st.session_state.graph_app.get_state(config)
            if current and current.values.get("messages"):
                state = dict(current.values)
            else:
                state = new_initial_state()
            state["messages"] = state.get("messages", []) + [{"role": "user", "content": question}]

            result = st.session_state.graph_app.invoke(state, config=config)
            reply = result.get("reply", "Sorry, I didn't catch that — could you rephrase?")
            stage = result.get("stage", "idle")
            render_reply(reply, stage)

            full_messages = result.get("messages", []) + [{"role": "assistant", "content": reply}]
            st.session_state.graph_app.update_state(config, {"messages": full_messages})
            st.session_state.last_state = result

    st.session_state.display_messages.append(
        {"role": "assistant", "content": reply, "stage": stage}
    )


# --- Render chat history ---
for msg in st.session_state.display_messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_reply(msg["content"], msg.get("stage", "idle"))
        else:
            st.markdown(msg["content"])

# --- Clickable example questions (only shown before the first message) ---
if not st.session_state.display_messages:
    st.write("**Try asking:**")
    cols = st.columns(len(EXAMPLE_QUESTIONS))
    for col, q in zip(cols, EXAMPLE_QUESTIONS):
        if col.button(q, use_container_width=True):
            ask_question(q)
            st.rerun()

# --- Handle new typed input ---
user_input = st.chat_input("Type your message...")
if user_input:
    ask_question(user_input)

with st.sidebar:
    st.header("Booking Summary")
    last = st.session_state.get("last_state", {})
    if last.get("date") or last.get("time") or last.get("email"):
        st.markdown(f"📅 **Date:** {last.get('date') or '_not set_'}")
        st.markdown(f"⏰ **Time:** {last.get('time') or '_not set_'}")
        st.markdown(f"✉️ **Email:** {last.get('email') or '_not set_'}")
        stage = last.get("stage", "idle")
        status_label = {
            "confirmed": "✅ Confirmed",
            "collecting": "📝 In progress",
            "negotiating": "🔄 Choosing a new slot",
            "idle": "—",
        }.get(stage, stage)
        st.markdown(f"**Status:** {status_label}")
    else:
        st.caption("No booking in progress yet — ask to schedule an appointment to see details here.")

    st.divider()
    if st.button("Start new conversation"):
        st.query_params["thread_id"] = str(uuid.uuid4())
        st.session_state.pop("display_messages", None)
        st.session_state.pop("last_state", None)
        st.rerun()