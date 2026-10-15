"""Admin-Kommandos fuer die Tourenplanung (nur lokal im Container, kein Web).

    python -m routeplanning.admin lock-gsmtasks 2026-10-15 "Tour manuell in GSMtasks angelegt"
    python -m routeplanning.admin show-gsmtasks-locks
    python -m routeplanning.admin unlock-gsmtasks 2026-10-15 --confirm

Eine Sperre verhindert jede GSMtasks-Uebertragung (Vorabpruefung und
Transfer) fuer diesen Liefertag. Sie wirkt unabhaengig vom Tourentwurf und
bleibt bei "Tour laden", Neustart und Deployment erhalten.
"""

from __future__ import annotations

import argparse
import sys

from . import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m routeplanning.admin")
    sub = parser.add_subparsers(dest="command", required=True)

    lock = sub.add_parser("lock-gsmtasks", help="GSMtasks-Uebertragung eines Liefertags dauerhaft sperren")
    lock.add_argument("date", help="Liefertag (JJJJ-MM-TT)")
    lock.add_argument("reason", help="Grund (wird in der Oberflaeche angezeigt)")

    sub.add_parser("show-gsmtasks-locks", help="Alle Sperren anzeigen")

    unlock = sub.add_parser("unlock-gsmtasks", help="Sperre aufheben (nur mit --confirm)")
    unlock.add_argument("date")
    unlock.add_argument("--confirm", action="store_true", help="Aufheben ausdruecklich bestaetigen")

    args = parser.parse_args(argv)
    conn = db.get_connection()
    db.init_db(conn)
    try:
        if args.command == "lock-gsmtasks":
            lock_row = db.lock_gsmtasks_transfer(conn, args.date, args.reason)
            print(f"Gesperrt: {lock_row['delivery_date']} seit {lock_row['created_at']} - {lock_row['reason']}")
        elif args.command == "show-gsmtasks-locks":
            rows = conn.execute(
                "SELECT delivery_date, reason, created_at FROM gsmtasks_transfer_locks ORDER BY delivery_date"
            ).fetchall()
            for row in rows:
                print(f"{row['delivery_date']}  seit {row['created_at']}  {row['reason']}")
            if not rows:
                print("Keine Sperren.")
        elif args.command == "unlock-gsmtasks":
            if not args.confirm:
                print("Nicht aufgehoben: --confirm fehlt.", file=sys.stderr)
                return 2
            removed = db.unlock_gsmtasks_transfer(conn, args.date)
            print("Sperre aufgehoben." if removed else "Keine Sperre fuer dieses Datum.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
