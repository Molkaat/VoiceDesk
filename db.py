from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    ForeignKey, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Restaurant(Base):
    __tablename__ = "restaurants"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String,  nullable=False)
    agent_name      = Column(String,  nullable=False)
    personality     = Column(Text,    nullable=False)
    voice_id        = Column(String,  nullable=False)
    menu_text       = Column(Text,    nullable=False)
    hours           = Column(Text,    nullable=False)                 # Human-readable hours (for display)
    hours_structured = Column(Text,  nullable=True)                   # JSON: {"monday": {"lunch": ["12:00", "14:30"], "dinner": ["18:00", "23:00"]}, ...}
    booking_rules   = Column(Text,    nullable=False)
    faqs            = Column(Text,    nullable=False)
    contact_email   = Column(String,  nullable=True)   # used in system prompt escalation copy
    max_covers      = Column(Integer, default=50,     nullable=False)  # total seats available
    avg_cover       = Column(Integer, default=45,     nullable=False)  # average revenue per cover (€) - owner configurable
    default_language = Column(String(10), default="en", nullable=False)  # ISO 639-1 code: en, fr, ar, etc.
    preview_mode    = Column(Boolean, default=False,   nullable=False)  # Owner preview mode - skips database writes

    bookings  = relationship("Booking",  back_populates="restaurant")
    call_logs = relationship("CallLog",  back_populates="restaurant")
    guests    = relationship("Guest",    back_populates="restaurant")
    transfers = relationship("Transfer", back_populates="restaurant")
    slot_configs = relationship("SlotConfig", back_populates="restaurant")
    slot_overrides = relationship("SlotOverride", back_populates="restaurant")


class Guest(Base):
    """
    One record per unique phone number per restaurant.
    Populated/updated on every confirmed booking so the agent can
    detect returning callers and staff can see visit history.
    """
    __tablename__ = "guests"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    phone         = Column(String,  nullable=False, index=True)
    name          = Column(String,  nullable=True)   # last known name
    language      = Column(String(10), nullable=True)
    visit_count   = Column(Integer, default=0,     nullable=False)
    no_show_count = Column(Integer, default=0,     nullable=False)
    last_visit    = Column(String,  nullable=True)   # YYYY-MM-DD of last booking date
    preferences   = Column(Text,    nullable=True)   # free-text notes (allergies, seating, etc.)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="guests")
    bookings   = relationship("Booking",    back_populates="guest")

    __table_args__ = (
        # one guest profile per phone number per restaurant
        # use UniqueConstraint if you want to enforce at DB level:
        # UniqueConstraint("restaurant_id", "phone", name="uq_guest_restaurant_phone"),
    )


class Booking(Base):
    __tablename__ = "bookings"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    guest_id      = Column(Integer, ForeignKey("guests.id"),      nullable=True)  # null if phone unknown
    call_log_id   = Column(Integer, ForeignKey("call_logs.id"),   nullable=True)  # ties booking to call
    session_id    = Column(String,  nullable=True, index=True)    # WebSocket session_id - enables safe multi-call booking retrieval

    # Core fields (from BOOKING_JSON)
    name          = Column(String,  nullable=False)
    phone         = Column(String,  nullable=True)
    party_size    = Column(Integer, nullable=False)
    date          = Column(String,  nullable=False)   # YYYY-MM-DD
    time          = Column(String,  nullable=False)   # HH:MM
    occasion      = Column(String,  nullable=True)    # "anniversary", "birthday", etc.
    special_requests = Column(Text, nullable=True)
    has_allergy   = Column(Boolean, default=False,    nullable=False)

    # Operational metadata
    language      = Column(String(10), nullable=True, default="en")
    source        = Column(String,  nullable=False, default="phone_agent")
    status        = Column(String,  nullable=False, default="confirmed")
    # status values: confirmed | pending | cancelled | waitlist | no_show

    confirmation_sent = Column(Boolean, default=False, nullable=False)
    
    # Google Calendar sync
    calendar_event_id = Column(String, nullable=True)  # Google Calendar event ID
    calendar_synced   = Column(Boolean, default=False, nullable=False)  # Whether synced to calendar
    
    created_at    = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="bookings")
    guest      = relationship("Guest",      back_populates="bookings")
    call_log   = relationship("CallLog",    back_populates="bookings")


class CallLog(Base):
    __tablename__ = "call_logs"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    session_id    = Column(String,  nullable=False, index=True)
    caller_phone  = Column(String,  nullable=True)

    # Call quality & routing
    transcript        = Column(Text,    nullable=True)
    duration_seconds  = Column(Integer, nullable=True)
    turns_to_complete = Column(Integer, nullable=True)   # exchanges before booking confirmed
    language          = Column(String(10), nullable=True)
    escalated         = Column(Boolean, default=False, nullable=False)
    escalation_reason = Column(String,  nullable=True)
    # escalation_reason values: modify_reservation | complaint | large_group |
    #                           out_of_scope | caller_requested_human

    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="call_logs")
    bookings   = relationship("Booking",    back_populates="call_log")
    transfers  = relationship("Transfer",   back_populates="call_log")


class Transfer(Base):
    """
    Created whenever the agent outputs a TRANSFER_JSON.
    Gives your ops team a clean queue of calls that need human follow-up.
    """
    __tablename__ = "transfers"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    call_log_id   = Column(Integer, ForeignKey("call_logs.id"),   nullable=True)

    reason        = Column(String,  nullable=False)
    # reason values: modify_reservation | complaint | large_group |
    #                out_of_scope | caller_requested_human

    caller_name   = Column(String,  nullable=True)
    callback      = Column(String,  nullable=True)   # phone to call back
    booking_date  = Column(String,  nullable=True)   # YYYY-MM-DD if relevant
    notes         = Column(Text,    nullable=True)
    resolved      = Column(Boolean, default=False, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="transfers")
    call_log   = relationship("CallLog",    back_populates="transfers")


class SlotConfig(Base):
    """
    Configurable time slots per day per restaurant.
    Allows owner to set max covers per slot and close slots for private events.
    """
    __tablename__ = "slot_configs"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    day_of_week   = Column(String,  nullable=False)  # "monday" through "sunday"
    slot_time     = Column(String,  nullable=False)  # HH:MM format
    service       = Column(String,  nullable=False)  # "lunch" or "dinner"
    max_covers    = Column(Integer, default=30,      nullable=False)
    is_closed     = Column(Boolean, default=False,   nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="slot_configs")


class SlotOverride(Base):
    """
    Override slots for specific dates (e.g., 'closed for wedding on Saturday').
    Overrides SlotConfig rules for specific date.
    """
    __tablename__ = "slot_overrides"

    id            = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    date          = Column(String,  nullable=False)  # YYYY-MM-DD format
    slot_time     = Column(String,  nullable=True)   # HH:MM format, null = entire day closed
    is_closed     = Column(Boolean, default=True,    nullable=False)
    reason        = Column(String,  nullable=True)   # "wedding", "staff training", etc.
    created_at    = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="slot_overrides")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()