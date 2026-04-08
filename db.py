from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Restaurant(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    agent_name = Column(String, nullable=False)
    personality = Column(Text, nullable=False)
    voice_id = Column(String, nullable=False)
    menu_text = Column(Text, nullable=False)
    hours = Column(Text, nullable=False)
    booking_rules = Column(Text, nullable=False)
    faqs = Column(Text, nullable=False)

    bookings = relationship("Booking", back_populates="restaurant")
    call_logs = relationship("CallLog", back_populates="restaurant")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    caller_phone = Column(String, nullable=True)
    name = Column(String, nullable=False)
    party_size = Column(Integer, nullable=False)
    date = Column(String, nullable=False)
    time = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="bookings")


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    session_id = Column(String, nullable=False)
    transcript = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    escalated = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="call_logs")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
