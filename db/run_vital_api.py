"""Start the local demo after checking the existing PostgreSQL connection."""

import getpass
import os

import uvicorn

from postgres_db import connect_db, psycopg


def main():
    # Explicit demo mapping for the user's registered test subject.
    os.environ.setdefault("VITAL_DEFAULT_SUBJECT_ID", "1")
    print("DB: {}:{} / {} / user={}".format(
        os.getenv("PGHOST", "127.0.0.1"), os.getenv("PGPORT", "5432"),
        os.getenv("PGDATABASE", "capstone"), os.getenv("PGUSER", "postgres")))
    print("Demo subject_id:", os.environ["VITAL_DEFAULT_SUBJECT_ID"])
    print("Timestamps without an offset are interpreted as Korean time (+09:00).")
    if "PGPASSWORD" not in os.environ:
        os.environ["PGPASSWORD"] = getpass.getpass("PostgreSQL password (hidden): ")
    try:
        subject_id = int(os.environ["VITAL_DEFAULT_SUBJECT_ID"])
        with connect_db() as connection:
            row = connection.execute(
                "SELECT display_name FROM public.subjects WHERE subject_id = %s",
                (subject_id,),
            ).fetchone()
            if row is None:
                print("The demo subject_id is not registered in subjects.")
                return 1
            for table in ("public.vital_event_logs", "public.vital_summaries"):
                if connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
                    print("Required table is missing:", table)
                    return 1
    except ValueError:
        print("VITAL_DEFAULT_SUBJECT_ID must be a positive integer.")
        return 1
    except psycopg.Error as exc:
        # Do not print connection strings or passwords.
        print("Database check failed:", type(exc).__name__)
        print("Check the password and PGHOST/PGPORT/PGDATABASE/PGUSER settings.")
        return 1
    print("Database check passed. Open http://127.0.0.1:8000/docs")
    uvicorn.run("vital_api:app", host="127.0.0.1", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
