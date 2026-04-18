"""
Migration script to add escalated column to call_logs table if it doesn't exist
"""
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

# Parse the database URL (expects sqlite:///voicedesk.db or similar)
db_url = os.getenv("DATABASE_URL", "sqlite:///voicedesk.db")
db_path = db_url.replace("sqlite:///", "")

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if call_logs table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='call_logs'")
    table_exists = cursor.fetchone() is not None
    
    if not table_exists:
        print("✓ call_logs table doesn't exist yet (will be created on server start)")
        conn.close()
        exit(0)
    
    # Check if escalated column already exists
    cursor.execute("PRAGMA table_info(call_logs)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "escalated" not in columns:
        print("Adding 'escalated' column to call_logs table...")
        cursor.execute("""
            ALTER TABLE call_logs 
            ADD COLUMN escalated BOOLEAN DEFAULT 0 NOT NULL
        """)
        conn.commit()
        print("✓ Column added successfully")
    else:
        print("✓ Column 'escalated' already exists")
    
    conn.close()
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
