"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Setup check

Confirms the two things the app needs before it can answer a question:
  1. The read-only database role can connect and see the warehouse.
  2. Gemini answers, through the same route the app uses (Vertex AI or the
     Gemini Developer API, as set in .env), on the main model and each backup.

Run from the project root:  python scripts/check_setup.py
Nothing is written to the database. Each Gemini check is one tiny request.
"""

import os
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "metadata").is_dir())
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

EXPECTED = {"view": 13, "dim/fact table": 9, "ml table": 4}


def check_database() -> bool:
    import psycopg2

    schema = os.getenv("DB_SCHEMA", "warehouse")
    print(f"\n--- Database ({os.getenv('DB_USER')} @ {os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}) ---")
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=10,
        )
        conn.set_session(readonly=True)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT current_user")
            print("Connected as:", cur.fetchone()[0])

            cur.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (schema,),
            )
            rows = cur.fetchall()
            found = {"view": 0, "dim/fact table": 0, "ml table": 0}
            for name, kind in rows:
                if kind == "VIEW":
                    found["view"] += 1
                elif name.startswith("ml_"):
                    found["ml table"] += 1
                else:
                    found["dim/fact table"] += 1
            ok = True
            for kind, expected in EXPECTED.items():
                mark = "OK" if found[kind] == expected else "MISMATCH"
                ok &= found[kind] == expected
                print(f"  {kind:15} {found[kind]:>3} found, {expected} expected  [{mark}]")

            # Read-only guarantees, checked without changing anything.
            cur.execute(
                "SELECT has_table_privilege(current_user, %s, 'INSERT'), "
                "       has_table_privilege(current_user, %s, 'DELETE')",
                (f"{schema}.dim_order", f"{schema}.dim_order"),
            )
            can_insert, can_delete = cur.fetchone()
            ro = not (can_insert or can_delete)
            print(f"  write access to {schema}.dim_order: {'none' if ro else 'PRESENT'}  [{'OK' if ro else 'PROBLEM'}]")
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'staging'")
            staging_visible = cur.fetchone()[0]
            print(f"  staging schema visible: {'no' if not staging_visible else 'YES'}  [{'OK' if not staging_visible else 'PROBLEM'}]")
            ok = ok and ro and not staging_visible
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print("Database check: FAILED\n ", exc)
        return False
    print("Database check:", "PASSED" if ok else "FAILED")
    return ok


def check_gemini() -> bool:
    from google.genai import types

    from app.llm.sql_generator import DEFAULT_FALLBACK_MODELS, DEFAULT_MODEL, _make_client

    vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes"}
    if vertex:
        print(f"\n--- Gemini via Vertex AI (project {os.getenv('GOOGLE_CLOUD_PROJECT')}, "
              f"location {os.getenv('GOOGLE_CLOUD_LOCATION', 'global')}) ---")
        key = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not key or not Path(key).is_file():
            print(f"Key file not found: {key or '(GOOGLE_APPLICATION_CREDENTIALS is empty)'}")
            print("Gemini check: FAILED")
            return False
    else:
        print("\n--- Gemini via the Gemini Developer API (AI Studio key) ---")

    try:
        client = _make_client()
    except Exception as exc:  # noqa: BLE001
        print("Could not create the Gemini client:", exc)
        print("Gemini check: FAILED")
        return False

    main = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
    backups = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS).split(",") if m.strip()]
    config = types.GenerateContentConfig(
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    results = {}
    for model in [main] + [m for m in backups if m != main]:
        try:
            reply = client.models.generate_content(model=model, contents="Reply with the single word OK.", config=config)
            results[model] = True
            print(f"  {model:22} answered: {(reply.text or '').strip()[:20]!r}")
        except Exception as exc:  # noqa: BLE001
            results[model] = False
            print(f"  {model:22} FAILED: {str(exc)[:160]}")

    ok = any(results.values())
    if ok and not results[main]:
        print("Main model failed but a backup answered: the app will work, using the backup.")
    print("Gemini check:", "PASSED" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    db_ok = check_database()
    llm_ok = check_gemini()
    print("\n--- Summary ---")
    print(f"Database:  {'OK' if db_ok else 'FAILED'}")
    print(f"Gemini:    {'OK' if llm_ok else 'FAILED'}")
    if db_ok and llm_ok:
        print("\nEnvironment check complete. Ready to start building.")
        sys.exit(0)
    print("\nFix the failing check(s) above before moving on.")
    sys.exit(1)
