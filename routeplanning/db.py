"""SQLite-Persistenz fuer Tourentwuerfe und Routenprofile.

Ein Tourentwurf gehoert zu genau einem Liefertag (``delivery_date``). Beim
erneuten "Tour laden" werden die Stopps aus Zoho neu aufgebaut; bereits
gesetzte manuelle Mengen und die Reihenfolge bleiben fuer Kunden erhalten,
die weiterhin einen Auftrag haben.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from .gsmtasks import OrderResult
from .quantities import ArticleWarning, Stop

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "route_planning.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS route_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_date TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'entwurf',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES route_drafts(id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    order_numbers TEXT NOT NULL DEFAULT '[]',
    kisten_auto INTEGER NOT NULL DEFAULT 0,
    kisten_override INTEGER,
    schnittware_auto REAL NOT NULL DEFAULT 0,
    schnittware_override REAL,
    kresse_auto INTEGER NOT NULL DEFAULT 0,
    kresse_override INTEGER,
    not_in_profile INTEGER NOT NULL DEFAULT 0,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    UNIQUE(draft_id, customer_id)
);

CREATE TABLE IF NOT EXISTS route_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES route_drafts(id) ON DELETE CASCADE,
    customer_name TEXT NOT NULL,
    article_name TEXT NOT NULL,
    quantity REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS route_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    customer_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Ein Batch = ein Klick auf "Jetzt uebertragen" fuer ein Lieferdatum.
CREATE TABLE IF NOT EXISTS gsmtasks_transfer_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stop_count INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    status TEXT NOT NULL
);

-- Ergebnis je Stopp innerhalb eines Batches (fuer Erfolgreich/Fehlgeschlagen-Anzeige).
CREATE TABLE IF NOT EXISTS gsmtasks_transfer_stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES gsmtasks_transfer_batches(id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    gsmtasks_order_id TEXT,
    gsmtasks_order_url TEXT,
    gsmtasks_task_url TEXT,
    error_message TEXT
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Migriert bereits existierende Datenbanken um neue Spalten - noetig,
    weil ``CREATE TABLE IF NOT EXISTS`` bestehende Tabellen nicht aendert."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    _ensure_column(conn, "route_stops", "phone", "phone TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "route_stops", "email", "email TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# -- Tourentwuerfe --------------------------------------------------------


def replace_stops_from_zoho(
    conn: sqlite3.Connection,
    delivery_date: str,
    stops: list[Stop],
    warnings: list[ArticleWarning],
) -> int:
    """Baut die Stopps eines Tourentwurfs aus einem frischen Zoho-Abruf auf.

    Bestehende Reihenfolge und manuelle Mengenueberschreibungen bleiben fuer
    Kunden erhalten, die weiterhin in ``stops`` vorkommen.
    """
    now = _now()
    row = conn.execute(
        "SELECT id FROM route_drafts WHERE delivery_date = ?", (delivery_date,)
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO route_drafts (delivery_date, status, created_at, updated_at) "
            "VALUES (?, 'entwurf', ?, ?)",
            (delivery_date, now, now),
        )
        draft_id = cur.lastrowid
        existing = {}
    else:
        draft_id = row["id"]
        conn.execute("UPDATE route_drafts SET updated_at = ? WHERE id = ?", (now, draft_id))
        existing = {
            r["customer_id"]: r
            for r in conn.execute(
                "SELECT * FROM route_stops WHERE draft_id = ?", (draft_id,)
            ).fetchall()
        }

    conn.execute("DELETE FROM route_stops WHERE draft_id = ?", (draft_id,))
    conn.execute("DELETE FROM route_warnings WHERE draft_id = ?", (draft_id,))

    # Kunden, die vorher schon eine Position hatten, behalten ihre relative
    # Reihenfolge; neue Kunden werden dahinter angehaengt (Reihenfolge wie
    # von Zoho geliefert).
    ordered = sorted(
        stops,
        key=lambda s: (
            existing[s.customer_id]["position"] if s.customer_id in existing else 10_000_000,
        ),
    )

    for position, stop in enumerate(ordered):
        prev = existing.get(stop.customer_id)
        kisten_override = prev["kisten_override"] if prev is not None else None
        schnittware_override = prev["schnittware_override"] if prev is not None else None
        kresse_override = prev["kresse_override"] if prev is not None else None
        not_in_profile = prev["not_in_profile"] if prev is not None else 0
        conn.execute(
            "INSERT INTO route_stops ("
            "  draft_id, customer_id, customer_name, address, position, order_numbers,"
            "  kisten_auto, kisten_override, schnittware_auto, schnittware_override,"
            "  kresse_auto, kresse_override, not_in_profile, phone, email"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id,
                stop.customer_id,
                stop.customer_name,
                stop.address,
                position,
                json.dumps(stop.order_numbers),
                stop.kisten,
                kisten_override,
                stop.schnittware,
                schnittware_override,
                stop.kresse,
                kresse_override,
                not_in_profile,
                stop.phone,
                stop.email,
            ),
        )

    for warning in warnings:
        conn.execute(
            "INSERT INTO route_warnings (draft_id, customer_name, article_name, quantity) "
            "VALUES (?, ?, ?, ?)",
            (draft_id, warning.customer_name, warning.article_name, warning.quantity),
        )

    conn.commit()
    return draft_id


def get_draft(conn: sqlite3.Connection, delivery_date: str) -> dict[str, Any] | None:
    draft_row = conn.execute(
        "SELECT * FROM route_drafts WHERE delivery_date = ?", (delivery_date,)
    ).fetchone()
    if draft_row is None:
        return None
    stops = conn.execute(
        "SELECT * FROM route_stops WHERE draft_id = ? ORDER BY position ASC",
        (draft_row["id"],),
    ).fetchall()
    warnings = conn.execute(
        "SELECT * FROM route_warnings WHERE draft_id = ?", (draft_row["id"],)
    ).fetchall()
    return {
        "delivery_date": draft_row["delivery_date"],
        "status": draft_row["status"],
        "updated_at": draft_row["updated_at"],
        "stops": [_stop_row_to_dict(r) for r in stops],
        "warnings": [
            {
                "customer_name": w["customer_name"],
                "article_name": w["article_name"],
                "quantity": w["quantity"],
            }
            for w in warnings
        ],
        "gsmtasks": {
            "already_transferred": has_successful_gsmtasks_transfer(conn, draft_row["delivery_date"]),
            "last_batch": get_latest_gsmtasks_batch(conn, draft_row["delivery_date"]),
        },
    }


def _stop_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    kisten = row["kisten_override"] if row["kisten_override"] is not None else row["kisten_auto"]
    schnittware = (
        row["schnittware_override"]
        if row["schnittware_override"] is not None
        else row["schnittware_auto"]
    )
    kresse = row["kresse_override"] if row["kresse_override"] is not None else row["kresse_auto"]
    return {
        "customer_id": row["customer_id"],
        "customer_name": row["customer_name"],
        "address": row["address"],
        "order_numbers": json.loads(row["order_numbers"]),
        "kisten": kisten,
        "kisten_auto": row["kisten_auto"],
        "kisten_manual": row["kisten_override"] is not None,
        "schnittware": schnittware,
        "schnittware_auto": row["schnittware_auto"],
        "schnittware_manual": row["schnittware_override"] is not None,
        "kresse": kresse,
        "kresse_auto": row["kresse_auto"],
        "kresse_manual": row["kresse_override"] is not None,
        "not_in_profile": bool(row["not_in_profile"]),
        "phone": row["phone"],
        "email": row["email"],
    }


def save_stop_order_and_quantities(
    conn: sqlite3.Connection, delivery_date: str, stops_payload: list[dict[str, Any]]
) -> None:
    """Speichert Reihenfolge und manuelle Mengen aus der Weboberflaeche.

    ``stops_payload`` ist die vollstaendige, aktuell sichtbare Stoppliste in
    ihrer finalen Reihenfolge: ``[{customer_id, kisten, schnittware, kresse}, ...]``.
    Ein Wert, der vom automatisch berechneten Wert abweicht, wird als manuelle
    Ueberschreibung gespeichert; stimmt er wieder ueberein, wird die
    Ueberschreibung aufgehoben.
    """
    draft_row = conn.execute(
        "SELECT id FROM route_drafts WHERE delivery_date = ?", (delivery_date,)
    ).fetchone()
    if draft_row is None:
        raise ValueError(f"Kein Tourentwurf fuer {delivery_date}")
    draft_id = draft_row["id"]

    for position, item in enumerate(stops_payload):
        row = conn.execute(
            "SELECT kisten_auto, schnittware_auto, kresse_auto FROM route_stops "
            "WHERE draft_id = ? AND customer_id = ?",
            (draft_id, item["customer_id"]),
        ).fetchone()
        if row is None:
            continue

        kisten_override = None if int(item["kisten"]) == row["kisten_auto"] else int(item["kisten"])
        schnittware_override = (
            None
            if float(item["schnittware"]) == float(row["schnittware_auto"])
            else float(item["schnittware"])
        )
        kresse_override = None if int(item["kresse"]) == row["kresse_auto"] else int(item["kresse"])

        conn.execute(
            "UPDATE route_stops SET position = ?, kisten_override = ?, "
            "schnittware_override = ?, kresse_override = ? "
            "WHERE draft_id = ? AND customer_id = ?",
            (
                position,
                kisten_override,
                schnittware_override,
                kresse_override,
                draft_id,
                item["customer_id"],
            ),
        )

    conn.execute(
        "UPDATE route_drafts SET updated_at = ? WHERE id = ?", (_now(), draft_id)
    )
    conn.commit()


def delete_stop(conn: sqlite3.Connection, delivery_date: str, customer_id: str) -> bool:
    """Entfernt einen einzelnen Stopp aus dem Tourentwurf.

    Wirkt ausschliesslich auf den lokalen Tourentwurf (SQLite) - keine
    Zoho-Daten werden veraendert. Der Stopp verschwindet dadurch automatisch
    aus ``get_draft`` und damit aus jedem darauf aufbauenden Export (XLSX).
    Gibt ``True`` zurueck, wenn ein Stopp geloescht wurde, sonst ``False``.
    """
    draft_row = conn.execute(
        "SELECT id FROM route_drafts WHERE delivery_date = ?", (delivery_date,)
    ).fetchone()
    if draft_row is None:
        return False
    draft_id = draft_row["id"]

    cur = conn.execute(
        "DELETE FROM route_stops WHERE draft_id = ? AND customer_id = ?",
        (draft_id, customer_id),
    )
    if cur.rowcount == 0:
        return False

    conn.execute("UPDATE route_drafts SET updated_at = ? WHERE id = ?", (_now(), draft_id))
    conn.commit()
    return True


# -- Routenprofile ----------------------------------------------------------


def save_profile(conn: sqlite3.Connection, name: str, customer_ids: list[str]) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO route_profiles (name, customer_ids_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET customer_ids_json = excluded.customer_ids_json, "
        "updated_at = excluded.updated_at",
        (name, json.dumps(customer_ids), now, now),
    )
    conn.commit()


def get_profile(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM route_profiles WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        return None
    return {
        "name": row["name"],
        "customer_ids": json.loads(row["customer_ids_json"]),
        "updated_at": row["updated_at"],
    }


def list_profiles(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM route_profiles ORDER BY name ASC").fetchall()
    return [r["name"] for r in rows]


def apply_profile_order(
    conn: sqlite3.Connection, delivery_date: str, profile_name: str
) -> dict[str, Any] | None:
    """Sortiert die Stopps eines Tourentwurfs nach einem gespeicherten Profil.

    Kunden aus dem Profil ohne heutigen Auftrag werden ignoriert. Kunden mit
    Auftrag, die nicht im Profil stehen, werden ans Ende gesetzt und als
    "nicht im Profil" markiert.
    """
    profile = get_profile(conn, profile_name)
    if profile is None:
        raise ValueError(f"Profil '{profile_name}' nicht gefunden")

    draft_row = conn.execute(
        "SELECT id FROM route_drafts WHERE delivery_date = ?", (delivery_date,)
    ).fetchone()
    if draft_row is None:
        return None
    draft_id = draft_row["id"]

    current_stops = {
        r["customer_id"]: r
        for r in conn.execute(
            "SELECT * FROM route_stops WHERE draft_id = ?", (draft_id,)
        ).fetchall()
    }

    ordered_customer_ids = [cid for cid in profile["customer_ids"] if cid in current_stops]
    remaining = [cid for cid in current_stops if cid not in set(ordered_customer_ids)]
    # Reihenfolge der uebrigen Kunden bleibt wie bisher (aktuelle Position).
    remaining.sort(key=lambda cid: current_stops[cid]["position"])

    final_order = ordered_customer_ids + remaining
    in_profile = set(ordered_customer_ids)

    for position, customer_id in enumerate(final_order):
        conn.execute(
            "UPDATE route_stops SET position = ?, not_in_profile = ? "
            "WHERE draft_id = ? AND customer_id = ?",
            (position, 0 if customer_id in in_profile else 1, draft_id, customer_id),
        )

    conn.execute(
        "UPDATE route_drafts SET updated_at = ? WHERE id = ?", (_now(), draft_id)
    )
    conn.commit()
    return get_draft(conn, delivery_date)


# -- GSMtasks-Uebertragung ---------------------------------------------------


def has_successful_gsmtasks_transfer(conn: sqlite3.Connection, delivery_date: str) -> bool:
    """True, wenn es fuer dieses Lieferdatum bereits einen vollstaendig
    erfolgreichen Uebertragungsversuch gab (alle Stopps erfolgreich).
    Blockiert einen zweiten Versand - siehe app.py."""
    row = conn.execute(
        "SELECT 1 FROM gsmtasks_transfer_batches WHERE delivery_date = ? AND status = 'success' LIMIT 1",
        (delivery_date,),
    ).fetchone()
    return row is not None


def record_gsmtasks_transfer(
    conn: sqlite3.Connection, delivery_date: str, results: list[OrderResult]
) -> dict[str, Any]:
    """Speichert das Ergebnis eines Uebertragungsversuchs (ein Klick auf
    "Jetzt uebertragen") als Batch mit einer Zeile je Stopp."""
    now = _now()
    success_count = sum(1 for r in results if r.success)
    failure_count = len(results) - success_count
    if not results:
        status = "failed"
    elif failure_count == 0:
        status = "success"
    elif success_count == 0:
        status = "failed"
    else:
        status = "partial"

    cur = conn.execute(
        "INSERT INTO gsmtasks_transfer_batches "
        "(delivery_date, started_at, finished_at, stop_count, success_count, failure_count, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (delivery_date, now, now, len(results), success_count, failure_count, status),
    )
    batch_id = cur.lastrowid

    for result in results:
        conn.execute(
            "INSERT INTO gsmtasks_transfer_stops ("
            "  batch_id, customer_id, customer_name, external_id, success,"
            "  gsmtasks_order_id, gsmtasks_order_url, gsmtasks_task_url, error_message"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_id,
                result.customer_id,
                result.customer_name,
                result.external_id,
                1 if result.success else 0,
                result.order_id,
                result.order_url,
                result.task_url,
                result.error,
            ),
        )

    conn.commit()
    return get_gsmtasks_batch(conn, batch_id)


def get_gsmtasks_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any]:
    batch = conn.execute(
        "SELECT * FROM gsmtasks_transfer_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    stops = conn.execute(
        "SELECT * FROM gsmtasks_transfer_stops WHERE batch_id = ? ORDER BY id ASC",
        (batch_id,),
    ).fetchall()
    return {
        "delivery_date": batch["delivery_date"],
        "started_at": batch["started_at"],
        "finished_at": batch["finished_at"],
        "stop_count": batch["stop_count"],
        "success_count": batch["success_count"],
        "failure_count": batch["failure_count"],
        "status": batch["status"],
        "stops": [
            {
                "customer_id": s["customer_id"],
                "customer_name": s["customer_name"],
                "success": bool(s["success"]),
                "error": s["error_message"],
            }
            for s in stops
        ],
    }


def get_latest_gsmtasks_batch(conn: sqlite3.Connection, delivery_date: str) -> dict[str, Any] | None:
    batch = conn.execute(
        "SELECT id FROM gsmtasks_transfer_batches WHERE delivery_date = ? ORDER BY id DESC LIMIT 1",
        (delivery_date,),
    ).fetchone()
    if batch is None:
        return None
    return get_gsmtasks_batch(conn, batch["id"])
