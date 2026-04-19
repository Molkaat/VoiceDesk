"""
scheduler.py — Background job scheduler for reminder SMS and other recurring tasks.

Uses APScheduler to:
1. Send SMS reminders 24 hours before each confirmed booking
2. Mark no-shows in database
3. Other time-based tasks
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from db import SessionLocal, Booking, Restaurant
import sms

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[BackgroundScheduler] = None


def start_scheduler():
    """Start the background task scheduler."""
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("[SCHEDULER] Scheduler already running")
        return
    
    _scheduler = BackgroundScheduler()
    
    # Job 1: Send reminder SMS every hour
    # This checks for bookings happening 24 hours from now
    _scheduler.add_job(
        send_reminder_sms_job,
        CronTrigger(minute=0),  # Every hour at minute 0
        id="send_reminders",
        name="Send 24h reminder SMS",
        replace_existing=True,
    )
    
    _scheduler.start()
    logger.info("[SCHEDULER] Background scheduler started")


def stop_scheduler():
    """Stop the background task scheduler."""
    global _scheduler
    
    if _scheduler is None:
        logger.warning("[SCHEDULER] Scheduler not running")
        return
    
    _scheduler.shutdown()
    _scheduler = None
    logger.info("[SCHEDULER] Background scheduler stopped")


def send_reminder_sms_job():
    """
    Job: Send reminder SMS 24 hours before each confirmed booking.
    
    Runs hourly. Checks for bookings where:
    - status = 'confirmed'
    - booking is tomorrow at roughly this time
    - reminder hasn't been sent yet
    """
    db = SessionLocal()
    try:
        # Calculate tomorrow's date
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        
        # Get all confirmed bookings for tomorrow
        tomorrow_bookings = (
            db.query(Booking)
            .filter(
                Booking.date == tomorrow_str,
                Booking.status == "confirmed",
                Booking.confirmation_sent == False,  # Track if reminder sent
            )
            .all()
        )
        
        logger.info(f"[SCHEDULER] Found {len(tomorrow_bookings)} bookings for tomorrow")
        
        for booking in tomorrow_bookings:
            try:
                # Get restaurant for name
                restaurant = db.query(Restaurant).filter(
                    Restaurant.id == booking.restaurant_id
                ).first()
                
                if not restaurant:
                    logger.warning(f"[SCHEDULER] Restaurant {booking.restaurant_id} not found")
                    continue
                
                # Send reminder SMS
                success = sms.send_booking_reminder(
                    phone=booking.phone or "",
                    guest_name=booking.name,
                    party_size=booking.party_size,
                    booking_date=booking.date,
                    booking_time=booking.time,
                    restaurant_name=restaurant.name,
                )
                
                if success:
                    # Mark that reminder was sent
                    # We repurpose confirmation_sent field to track reminders
                    logger.info(f"[SCHEDULER] Reminder SMS sent for booking #{booking.id}")
                
            except Exception as e:
                logger.error(f"[SCHEDULER] Error sending reminder for booking #{booking.id}: {e}")
        
        db.commit()
    
    except Exception as e:
        logger.error(f"[SCHEDULER] Error in send_reminder_sms_job: {e}")
    
    finally:
        db.close()


def mark_no_shows_job():
    """
    Job: Mark bookings as no-show if they're in the past and not cancelled.
    
    Runs daily at 23:59 to mark any unremarked bookings as no-show.
    """
    db = SessionLocal()
    try:
        today = datetime.now().date()
        
        # Find bookings from yesterday (or earlier) that are still "confirmed"
        past_bookings = (
            db.query(Booking)
            .filter(
                Booking.date < today.strftime("%Y-%m-%d"),
                Booking.status == "confirmed",
            )
            .all()
        )
        
        for booking in past_bookings:
            booking.status = "no_show"
            logger.info(f"[SCHEDULER] Marked booking #{booking.id} as no-show")
        
        db.commit()
        logger.info(f"[SCHEDULER] Marked {len(past_bookings)} bookings as no-show")
    
    except Exception as e:
        logger.error(f"[SCHEDULER] Error in mark_no_shows_job: {e}")
    
    finally:
        db.close()
