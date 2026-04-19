from db import SessionLocal, Restaurant, SlotConfig, init_db
import json


def seed():
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(Restaurant).filter(Restaurant.id == 1).first()
        if existing:
            return  # already seeded

        # Structured hours: day → {period → [open, close]}
        hours_structured = {
            "monday": None,  # Closed
            "tuesday": {
                "lunch": ["12:00", "15:00"],
                "dinner": ["18:00", "23:00"]
            },
            "wednesday": {
                "lunch": ["12:00", "15:00"],
                "dinner": ["18:00", "23:00"]
            },
            "thursday": {
                "lunch": ["12:00", "15:00"],
                "dinner": ["18:00", "23:00"]
            },
            "friday": {
                "lunch": ["12:00", "15:00"],
                "dinner": ["18:00", "23:00"]
            },
            "saturday": {
                "lunch": ["12:00", "15:00"],
                "dinner": ["18:00", "23:00"]
            },
            "sunday": {
                "lunch": ["12:00", "15:00"],
                "dinner": ["18:00", "23:00"]
            }
        }

        la_bella = Restaurant(
            name="La Bella Ristorante",
            agent_name="Sofia",
            personality=(
                "Warm, welcoming, and charming with a slight Italian flair. "
                "You speak naturally like a friendly host, never robotic. "
                "Occasionally use a gentle Italian expression like 'Perfetto!' or 'Benvenuto!' but keep it subtle."
            ),
            voice_id="21m00Tcm4TlvDq8ikWAM",  # ElevenLabs Rachel
            menu_text=(
                "Starters: Bruschetta €7, Burrata €9, Arancini €8.\n"
                "Mains: Margherita pizza €12, Pasta carbonara €14, "
                "Penne arrabbiata €13, Grilled salmon €18, Bistecca fiorentina €26.\n"
                "Desserts: Tiramisu €6, Panna cotta €5, Gelato €4.\n"
                "Drinks: House wine €5/glass, Bottle of house wine €20, "
                "Aperol Spritz €8, Soft drinks €3, Still/sparkling water €2."
            ),
            hours=(
                "Tuesday to Sunday: 12:00–15:00 (lunch) and 18:00–23:00 (dinner).\n"
                "Closed on Mondays."
            ),
            hours_structured=json.dumps(hours_structured),
            booking_rules=(
                "Reservations are required for parties of 4 or more. "
                "Maximum party size is 8. "
                "For walk-ins of 1–3 people we usually have availability but cannot guarantee a table. "
                "Please arrive within 15 minutes of your reservation time."
            ),
            faqs=(
                "Parking: Free parking available on Via Roma, 2 minutes walk from the restaurant.\n"
                "Vegetarian options: Yes, several dishes are vegetarian including Margherita pizza, "
                "Penne arrabbiata, Bruschetta, and more. Please ask your server.\n"
                "Vegan options: Some dishes can be adapted — please ask when booking.\n"
                "Delivery: We do not offer delivery at this time.\n"
                "Allergens: Please inform us of any allergies when booking.\n"
                "Dress code: Smart casual.\n"
                "Children: Welcome! We have high chairs available.\n"
                "Payment: We accept cash and all major cards."
            ),
            max_covers=50,  # total restaurant capacity
            avg_cover=45,   # average revenue per cover in euros (owner configurable)
            default_language="fr",  # Rennes is in France, French-speaking
        )
        db.add(la_bella)
        db.commit()
        print("[SEED] La Bella Ristorante created (id=1)")
    finally:
        db.close()


def seed_slot_configs():
    """Seed default SlotConfig rows for La Bella."""
    db = SessionLocal()
    try:
        # Check if already seeded
        existing = db.query(SlotConfig).filter(SlotConfig.restaurant_id == 1).first()
        if existing:
            print("[SEED] SlotConfig already exists for restaurant_id=1")
            return

        restaurant_id = 1
        lunch_slots = ["12:00", "12:30", "13:00", "13:30", "14:00", "14:30"]
        dinner_slots = ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30", "22:00", "22:30"]
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        open_days = ["tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

        for day in days:
            if day == "monday":
                # Monday: all slots closed
                for slot in lunch_slots + dinner_slots:
                    service = "lunch" if slot in lunch_slots else "dinner"
                    sc = SlotConfig(
                        restaurant_id=restaurant_id,
                        day_of_week=day,
                        slot_time=slot,
                        service=service,
                        max_covers=30,
                        is_closed=True,
                    )
                    db.add(sc)
            else:
                # Tuesday-Sunday: open slots
                for slot in lunch_slots:
                    sc = SlotConfig(
                        restaurant_id=restaurant_id,
                        day_of_week=day,
                        slot_time=slot,
                        service="lunch",
                        max_covers=30,
                        is_closed=False,
                    )
                    db.add(sc)
                for slot in dinner_slots:
                    sc = SlotConfig(
                        restaurant_id=restaurant_id,
                        day_of_week=day,
                        slot_time=slot,
                        service="dinner",
                        max_covers=30,
                        is_closed=False,
                    )
                    db.add(sc)

        db.commit()
        print("[SEED] SlotConfig rows created for La Bella")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    seed_slot_configs()
    print("Done.")
