from db import SessionLocal, Restaurant, init_db


def seed():
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(Restaurant).filter(Restaurant.id == 1).first()
        if existing:
            return  # already seeded

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
        )
        db.add(la_bella)
        db.commit()
        print("[SEED] La Bella Ristorante created (id=1)")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("Done.")
