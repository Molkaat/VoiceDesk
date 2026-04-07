import json
import re
from datetime import datetime, date
from typing import Optional
from groq import Groq
from config import GROQ_API_KEY
from db import SessionLocal, Restaurant, Booking

client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.3-70b-versatile"

# In-memory conversation histories: session_id -> list of messages
_histories: dict[str, list[dict]] = {}

# Partial booking state per session
_booking_state: dict[str, dict] = {}


def _get_restaurant(restaurant_id: int = 1) -> Optional[Restaurant]:
    db = SessionLocal()
    try:
        return db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    finally:
        db.close()


def _build_system_prompt(restaurant: Restaurant) -> str:
    now = datetime.now()
    today = now.strftime("%A, %d %B %Y")
    current_time = now.strftime("%H:%M")
    return f"""You are {restaurant.agent_name}, the voice assistant for {restaurant.name}.

Personality: {restaurant.personality}

Menu:
{restaurant.menu_text}

Opening hours:
{restaurant.hours}

Booking rules:
{restaurant.booking_rules}

FAQs:
{restaurant.faqs}

Today is {today}. Current time: {current_time}.

Instructions:
- Keep replies short (1–3 sentences), natural, and conversational — this is a phone call.
- Never use markdown, bullet points, or lists in your reply.
- If the caller wants to book a table, collect: their name, party size, preferred date, and preferred time.
- Once you have all four booking fields, respond with a JSON block on its own line in exactly this format:
  BOOKING_JSON:{{"name":"...","party_size":N,"date":"YYYY-MM-DD","time":"HH:MM"}}
  Then immediately follow with a warm verbal confirmation sentence.
- If the caller asks something not covered, politely say you're not sure and offer to help with bookings or menu questions."""


def _try_extract_booking(text: str) -> Optional[dict]:
    """Extract booking JSON if the model included it."""
    match = re.search(r"BOOKING_JSON:(\{[^\n]+\})", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _save_booking(session_id: str, booking_data: dict, restaurant_id: int = 1):
    db = SessionLocal()
    try:
        booking = Booking(
            restaurant_id=restaurant_id,
            caller_phone=_booking_state.get(session_id, {}).get("caller_phone"),
            name=booking_data["name"],
            party_size=int(booking_data["party_size"]),
            date=booking_data["date"],
            time=booking_data["time"],
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)
        print(
            f"\n[BOOKING CONFIRMED] #{booking.id} | "
            f"{booking.name} | party of {booking.party_size} | "
            f"{booking.date} at {booking.time} | restaurant_id={restaurant_id}\n"
        )
        return booking
    finally:
        db.close()


def _clean_reply(text: str) -> str:
    """Remove BOOKING_JSON line from the spoken reply."""
    return re.sub(r"BOOKING_JSON:\{[^\n]+\}\n?", "", text).strip()


def chat(session_id: str, user_message: str, restaurant_id: int = 1) -> str:
    """Process a user utterance and return the assistant's spoken reply."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Sorry, I couldn't load the restaurant configuration."

    if session_id not in _histories:
        _histories[session_id] = []

    history = _histories[session_id]
    history.append({"role": "user", "content": user_message})

    system_prompt = _build_system_prompt(restaurant)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system_prompt}] + history,
        temperature=0.7,
        max_tokens=300,
    )

    assistant_text = response.choices[0].message.content or ""
    history.append({"role": "assistant", "content": assistant_text})

    # Handle booking extraction
    booking_data = _try_extract_booking(assistant_text)
    if booking_data:
        _save_booking(session_id, booking_data, restaurant_id)

    spoken_reply = _clean_reply(assistant_text)
    return spoken_reply


def clear_session(session_id: str):
    _histories.pop(session_id, None)
    _booking_state.pop(session_id, None)


def get_greeting(restaurant_id: int = 1) -> str:
    """Return the opening greeting for a new call."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Hello, how can I help you today?"
    system_prompt = _build_system_prompt(restaurant)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "[The caller just connected. Greet them warmly in one sentence.]"},
        ],
        temperature=0.8,
        max_tokens=80,
    )
    return response.choices[0].message.content or f"Ciao! Welcome to {restaurant.name}, I'm {restaurant.agent_name}. How can I help you today?"
