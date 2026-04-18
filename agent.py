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

# Call context per session: 'new', 'cancel', or 'modify'
_call_context: dict[str, str] = {}

# Caller phone per session
_caller_phone: dict[str, str] = {}


def _get_restaurant(restaurant_id: int = 1) -> Optional[Restaurant]:
    db = SessionLocal()
    try:
        return db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    finally:
        db.close()


def _build_system_prompt(restaurant: Restaurant, context: str = "new") -> str:
    now = datetime.now()
    today = now.strftime("%A, %d %B %Y")
    current_time = now.strftime("%H:%M")
    
    # Build context-specific instruction
    context_instruction = ""
    if context == "cancel":
        context_instruction = "The caller is calling to CANCEL an existing reservation. Help them cancel it by asking for their name and booking details."
    elif context == "modify":
        context_instruction = "The caller is calling to MODIFY an existing reservation. Help them modify it by asking for their name and what they'd like to change (date, time, or party size)."
    elif context == "manager":
        context_instruction = "The caller is requesting to speak with a manager. Provide the manager's direct line: +33 7 58 08 78 25. You can say: 'I can transfer you to our manager. Their direct line is +33 7 58 08 78 25. You can also leave a message and they'll get back to you.'"
    else:
        context_instruction = "The caller may want to make a new booking or ask questions."
    
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

Call Context: {context_instruction}

Instructions:
- Keep replies short (1–3 sentences), natural, and conversational — this is a phone call.
- Never use markdown, bullet points, or lists in your reply.
- If the caller wants to book a table, collect: their name, party size (NUMBER of people), preferred date (YYYY-MM-DD format), and preferred time (HH:MM format).
- Once you have all four booking fields (name, party_size as integer, date, time), respond with a JSON block on its own line in exactly this format:
  BOOKING_JSON:{{"name":"Guest Name","party_size":N,"date":"YYYY-MM-DD","time":"HH:MM"}}
  Where N is an integer (like 2, 4, 6, etc). Then immediately follow with a warm verbal confirmation sentence.
- IMPORTANT: party_size MUST be a number, not a string. Examples: "party_size":2 NOT "party_size":"2"
- If the caller asks something not covered, politely say you're not sure and offer to help with bookings or menu questions."""


def _try_extract_booking(text: str) -> Optional[dict]:
    """Extract booking JSON if the model included it."""
    match = re.search(r"BOOKING_JSON:(\{[^\n]+\})", text)
    if match:
        try:
            booking_json = json.loads(match.group(1))
            print(f"\n[BOOKING EXTRACTED] {booking_json}\n")
            return booking_json
        except json.JSONDecodeError as e:
            print(f"\n[BOOKING JSON ERROR] Failed to parse: {match.group(1)} | Error: {e}\n")
            return None
    return None


def _is_manager_request(user_message: str) -> bool:
    """Check if user is asking to speak with manager."""
    lower_msg = user_message.lower()
    manager_keywords = [
        "speak with manager",
        "speak to manager",
        "talk to manager",
        "get manager",
        "speak with owner",
        "speak to owner",
        "manager please",
        "can i speak to",
        "can i talk to",
        "i want to speak",
        "i need to speak",
        "request manager",
        "ask for manager",
        "call manager",
        "get the manager",
        "speak with the manager",
        "speak to the manager",
    ]
    return any(keyword in lower_msg for keyword in manager_keywords)


def _save_booking(session_id: str, booking_data: dict, restaurant_id: int = 1):
    db = SessionLocal()
    try:
        party_size = int(booking_data.get("party_size", 1))
        caller_phone = _caller_phone.get(session_id)  # Get phone from session state
        booking = Booking(
            restaurant_id=restaurant_id,
            caller_phone=caller_phone,
            name=booking_data.get("name", "Guest"),
            party_size=party_size,
            date=booking_data.get("date"),
            time=booking_data.get("time"),
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)
        print(
            f"\n[BOOKING CONFIRMED] #{booking.id} | "
            f"{booking.name} | party of {booking.party_size} | "
            f"{booking.date} at {booking.time} | phone={caller_phone} | restaurant_id={restaurant_id}\n"
        )
        return booking
    finally:
        db.close()


def _clean_reply(text: str) -> str:
    """Remove BOOKING_JSON line from the spoken reply."""
    return re.sub(r"BOOKING_JSON:\{[^\n]+\}\n?", "", text).strip()


def chat(session_id: str, user_message: str, restaurant_id: int = 1) -> tuple[str, bool]:
    """Process a user utterance and return the assistant's spoken reply and whether a manager transfer is needed."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Sorry, I couldn't load the restaurant configuration.", False

    if session_id not in _histories:
        _histories[session_id] = []

    history = _histories[session_id]
    
    # Check if user is requesting manager (before adding to history)
    manager_requested = _is_manager_request(user_message)
    
    history.append({"role": "user", "content": user_message})

    # Get context for this session
    context = _call_context.get(session_id, "new")
    system_prompt = _build_system_prompt(restaurant, context)

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
        print(f"[CHAT] Booking data extracted: {booking_data}")
        _save_booking(session_id, booking_data, restaurant_id)
    else:
        print(f"[CHAT] No booking extracted from: {assistant_text[:100]}")

    spoken_reply = _clean_reply(assistant_text)
    return spoken_reply, manager_requested


def clear_session(session_id: str):
    _histories.pop(session_id, None)
    _booking_state.pop(session_id, None)
    _call_context.pop(session_id, None)
    _caller_phone.pop(session_id, None)


def set_call_context(session_id: str, context: str):
    """Set the context for a call (new, cancel, modify, manager)."""
    if context in ["new", "cancel", "modify", "manager"]:
        _call_context[session_id] = context
        print(f"[CONTEXT SET] Session {session_id}: {context}")
    else:
        print(f"[CONTEXT ERROR] Unknown context: {context}")


def set_caller_phone(session_id: str, phone: str):
    """Store the caller's phone number for this session."""
    _caller_phone[session_id] = phone
    print(f"[PHONE SET] Session {session_id}: {phone}")


def get_greeting(restaurant_id: int = 1, context: str = "new") -> str:
    """Return the opening greeting for a new call."""
    restaurant = _get_restaurant(restaurant_id)
    if not restaurant:
        return "Hello, how can I help you today?"
    system_prompt = _build_system_prompt(restaurant, context)
    
    # Customize user prompt based on context
    if context == "cancel":
        user_prompt = "[The caller just connected and wants to CANCEL their reservation. Greet them warmly and briefly acknowledge this.]"
    elif context == "modify":
        user_prompt = "[The caller just connected and wants to MODIFY their reservation. Greet them warmly and briefly acknowledge this.]"
    elif context == "manager":
        user_prompt = "[The caller just connected and is requesting to speak with the manager. Greet them warmly and provide the manager's contact information.]"
    else:
        user_prompt = "[The caller just connected. Greet them warmly in one sentence.]"
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=80,
    )
    return response.choices[0].message.content or f"Ciao! Welcome to {restaurant.name}, I'm {restaurant.agent_name}. How can I help you today?"
