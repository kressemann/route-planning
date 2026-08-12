"""Flask-App fuer die Tourenplanung.

Nur lesender Zoho-Zugriff, keine Schreiboperationen nach Zoho. Persistiert
werden ausschliesslich Tourentwuerfe und Routenprofile in SQLite.
"""

from __future__ import annotations

import datetime as _dt
import functools
import logging
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, render_template, request

from . import db
from .gsmtasks import DropoffTask, GSMtasksClient, GSMtasksError, build_description, build_external_id
from .pdf_export import build_pdf
from .quantities import build_stops, produktart_from_item
from .xlsx_export import build_workbook, workbook_to_bytes
from .zoho import ZohoError, ZohoInventory

logger = logging.getLogger(__name__)

PRODUKTART_LABEL = "Produktart"


def _parse_date(value: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Ungueltiges Datum: {value!r} (erwartet YYYY-MM-DD)") from exc


def create_app(
    db_path: Path | None = None,
    zoho_client_factory=None,
    gsmtasks_client_factory=None,
) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or db.DEFAULT_DB_PATH
    app.config["ZOHO_CLIENT_FACTORY"] = zoho_client_factory or ZohoInventory.from_env
    app.config["GSMTASKS_CLIENT_FACTORY"] = gsmtasks_client_factory or GSMtasksClient.from_env

    with app.app_context():
        conn = db.get_connection(app.config["DB_PATH"])
        db.init_db(conn)
        conn.close()

    def get_db() -> Any:
        if "db_conn" not in g:
            g.db_conn = db.get_connection(app.config["DB_PATH"])
        return g.db_conn

    @app.teardown_appcontext
    def close_db(_exc: BaseException | None) -> None:
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    def handle_errors(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except ZohoError as exc:
                logger.error("Zoho-Fehler: %s", exc)
                return jsonify({"error": f"Zoho-Zugriff fehlgeschlagen: {exc}"}), 502
            except GSMtasksError as exc:
                logger.error("GSMtasks-Fehler: %s", exc)
                return jsonify({"error": f"GSMtasks-Zugriff fehlgeschlagen: {exc}"}), 502

        return wrapper

    @app.get("/")
    def index() -> str:
        return render_template("index.html", today=_dt.date.today().isoformat())

    @app.post("/api/tour/load")
    @handle_errors
    def load_tour() -> Response:
        payload = request.get_json(silent=True) or {}
        delivery_date = _parse_date(payload.get("date", ""))

        client: ZohoInventory = app.config["ZOHO_CLIENT_FACTORY"]()
        orders = client.sales_orders_shipped_on(delivery_date)

        def get_produktart(item_id: str) -> str | None:
            item = client.get_item(item_id)
            return produktart_from_item(item, PRODUKTART_LABEL)

        stops, warnings = build_stops(orders, get_produktart)

        conn = get_db()
        db.replace_stops_from_zoho(conn, delivery_date.isoformat(), stops, warnings)
        draft = db.get_draft(conn, delivery_date.isoformat())
        return jsonify(draft)

    @app.get("/api/draft/<delivery_date>")
    @handle_errors
    def get_draft(delivery_date: str) -> Response:
        _parse_date(delivery_date)
        draft = db.get_draft(get_db(), delivery_date)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404
        return jsonify(draft)

    @app.put("/api/draft/<delivery_date>")
    @handle_errors
    def save_draft(delivery_date: str) -> Response:
        _parse_date(delivery_date)
        payload = request.get_json(silent=True) or {}
        stops_payload = payload.get("stops")
        if not isinstance(stops_payload, list):
            raise ValueError("Feld 'stops' fehlt oder ist keine Liste.")
        for item in stops_payload:
            if not isinstance(item, dict) or "customer_id" not in item:
                raise ValueError("Jeder Stopp braucht mindestens 'customer_id'.")

        conn = get_db()
        db.save_stop_order_and_quantities(conn, delivery_date, stops_payload)
        draft = db.get_draft(conn, delivery_date)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404
        return jsonify(draft)

    @app.delete("/api/draft/<delivery_date>/stops/<customer_id>")
    @handle_errors
    def delete_stop(delivery_date: str, customer_id: str) -> Response:
        _parse_date(delivery_date)
        conn = get_db()
        deleted = db.delete_stop(conn, delivery_date, customer_id)
        if not deleted:
            return jsonify({"error": "Stopp oder Tourentwurf nicht gefunden."}), 404
        draft = db.get_draft(conn, delivery_date)
        return jsonify(draft)

    @app.post("/api/draft/<delivery_date>/gsmtasks")
    @handle_errors
    def transfer_to_gsmtasks(delivery_date: str) -> Response:
        """Uebertraegt den aktuellen Tourentwurf als GSMtasks drop_off-Tasks.

        Wird ausschliesslich nach expliziter Bestaetigung im Web aufgerufen
        (Modal "Jetzt uebertragen") - es gibt keinen automatischen Aufruf.
        Reihenfolge und Mengen kommen unveraendert aus dem aktuellen Entwurf
        (geloeschte Stopps sind darin bereits nicht mehr enthalten, manuelle
        Mengenueberschreibungen sind bereits eingerechnet). Ein zweiter,
        bereits vollstaendig erfolgreicher Versand fuer dasselbe Lieferdatum
        wird blockiert (siehe db.has_successful_gsmtasks_transfer).
        """
        _parse_date(delivery_date)
        conn = get_db()
        draft = db.get_draft(conn, delivery_date)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404
        if not draft["stops"]:
            return jsonify({"error": "Der Tourentwurf enthaelt keine Stopps."}), 400
        if db.has_successful_gsmtasks_transfer(conn, delivery_date):
            return jsonify({"error": "Diese Tour wurde bereits an GSMtasks uebertragen."}), 409

        client: GSMtasksClient = app.config["GSMTASKS_CLIENT_FACTORY"]()
        account_url = client.resolve_account_url()

        results = []
        for index, stop in enumerate(draft["stops"]):
            task = DropoffTask(
                customer_id=stop["customer_id"],
                external_id=build_external_id(delivery_date, stop["customer_id"]),
                reference=f"{delivery_date} {stop['customer_name']}"[:100],
                customer_name=stop["customer_name"],
                address=stop["address"],
                description=build_description(stop["kisten"], stop["schnittware"], stop["kresse"]),
                position=(index + 1) * 100,
                phone=stop.get("phone", ""),
                email=stop.get("email", ""),
            )
            results.append(client.create_dropoff_order(task, account_url))

        batch = db.record_gsmtasks_transfer(conn, delivery_date, results)
        return jsonify(batch)

    @app.get("/api/profiles")
    @handle_errors
    def list_profiles() -> Response:
        return jsonify({"profiles": db.list_profiles(get_db())})

    @app.post("/api/profiles")
    @handle_errors
    def save_profile() -> Response:
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        delivery_date = payload.get("date", "")
        if not name:
            raise ValueError("Profilname fehlt.")
        _parse_date(delivery_date)

        conn = get_db()
        draft = db.get_draft(conn, delivery_date)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404

        customer_ids = [stop["customer_id"] for stop in draft["stops"]]
        db.save_profile(conn, name, customer_ids)
        return jsonify({"name": name, "customer_ids": customer_ids})

    @app.post("/api/profiles/<name>/apply")
    @handle_errors
    def apply_profile(name: str) -> Response:
        payload = request.get_json(silent=True) or {}
        delivery_date = payload.get("date", "")
        _parse_date(delivery_date)

        conn = get_db()
        draft = db.apply_profile_order(conn, delivery_date, name)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404
        return jsonify(draft)

    @app.get("/api/draft/<delivery_date>/xlsx")
    @handle_errors
    def download_xlsx(delivery_date: str) -> Response:
        _parse_date(delivery_date)
        draft = db.get_draft(get_db(), delivery_date)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404

        wb = build_workbook(draft["stops"])
        data = workbook_to_bytes(wb)
        filename = f"tour_{delivery_date}.xlsx"
        return Response(
            data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/draft/<delivery_date>/pdf")
    @handle_errors
    def download_pdf(delivery_date: str) -> Response:
        _parse_date(delivery_date)
        draft = db.get_draft(get_db(), delivery_date)
        if draft is None:
            return jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404

        data = build_pdf(draft["stops"], delivery_date)
        filename = f"Lieferliste_{delivery_date}.pdf"
        return Response(
            data,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app
