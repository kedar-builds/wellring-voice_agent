"""Time each ALTER TABLE migration in init_pg_tables to find the hang."""
import time
from src.database import get_pg_conn

def timed(label, fn):
    t0 = time.time()
    fn()
    print(label, round(time.time() - t0, 1), "s", flush=True)

columns_to_ensure = {
    "users": [
        ("clerk_id", "TEXT UNIQUE"),
        ("name", "TEXT NOT NULL DEFAULT 'Elderly'"),
        ("age", "INTEGER"),
        ("role", "TEXT NOT NULL DEFAULT 'elderly'"),
        ("phone", "TEXT"),
        ("email", "TEXT"),
        ("medical_conditions", "TEXT[]"),
        ("medications", "TEXT[]"),
        ("medical_notes", "TEXT"),
        ("caregiver_for_user_id", "UUID REFERENCES users(user_id) ON DELETE SET NULL"),
        ("relationship", "TEXT"),
        ("caregiver_name", "TEXT"),
        ("caregiver_phone", "TEXT"),
        ("caregiver_email", "TEXT"),
        ("voice_id", "TEXT"),
        ("tts_provider", "TEXT NOT NULL DEFAULT 'elevenlabs'"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ],
    "assessments": [
        ("intent", "TEXT NOT NULL DEFAULT 'health_issue'"),
        ("symptoms", "TEXT[] NOT NULL DEFAULT '{}'"),
        ("severity", "TEXT NOT NULL DEFAULT 'low'"),
        ("confidence", "NUMERIC(4,3) NOT NULL DEFAULT 1.000"),
        ("score", "INTEGER NOT NULL DEFAULT 0"),
        ("base_score", "INTEGER NOT NULL DEFAULT 0"),
        ("risk_level", "TEXT NOT NULL DEFAULT 'LOW'"),
        ("category", "TEXT NOT NULL DEFAULT 'GENERAL'"),
        ("action", "TEXT NOT NULL DEFAULT 'monitor'"),
        ("message", "TEXT NOT NULL DEFAULT ''"),
        ("steps", "TEXT[] NOT NULL DEFAULT '{}'"),
        ("breakdown", "TEXT[] NOT NULL DEFAULT '{}'"),
        ("bolna_call_id", "TEXT"),
        ("recording_url", "TEXT"),
        ("transcript", "TEXT"),
        ("emotion_analysis", "TEXT"),
        ("assessed_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ],
}

for table, cols in columns_to_ensure.items():
    for col_name, col_type in cols:
        def run():
            with get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
        timed(f"{table}.{col_name}:", run)
