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
from .gsmtasks import (
    EXTERNAL_ID_PREFIX,
    OUTCOME_EXISTING,
    OUTCOME_NOT_SENT,
    OUTCOME_UNCLEAR,
    DropoffTask,
    GSMtasksClient,
    GSMtasksError,
    OrderResult,
    build_external_id,
    missing_address_parts,
)
from .pdf_export import build_pdf
from .quantities import build_stops, produktart_from_item, ve_stueck_from_item
from .xlsx_export import build_workbook, workbook_to_bytes
from .zoho import ZohoError, ZohoInventory

logger = logging.getLogger(__name__)

PRODUKTART_LABEL = "Produktart"
VE_STUECK_LABEL = "VE Stück"


def _parse_date(value: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Ungueltiges Datum: {value!r} (erwartet YYYY-MM-DD)") from exc


def create_app(
    db_path: Path | None = None,
    zoho_client_factory=None,
    gsmtasks_client_factory=None,
    external_id_prefix: str = EXTERNAL_ID_PREFIX,
) -> Flask:
    app = Flask(__name__)
    # Nur isolierte Tests setzen einen eigenen Praefix (eindeutige Test-IDs).
    app.config["GSMTASKS_EXTERNAL_ID_PREFIX"] = external_id_prefix
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

        def get_ve_stueck(item_id: str) -> int | None:
            return ve_stueck_from_item(client.get_item(item_id), VE_STUECK_LABEL)

        stops, warnings = build_stops(orders, get_produktart, get_ve_stueck)

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

    def _plan_gsmtasks_transfer(conn: Any, client: Any, delivery_date: str, draft: dict) -> list[dict]:
        """Plant die Uebertragung je Stopp in Tour-Reihenfolge - ausschliesslich
        lesend (lokale Historie + GET /orders/?external_id=). Status je Stopp:

        * ``transferred``        - laut lokaler Historie bereits angelegt
        * ``existing``           - in GSMtasks mit unserer external_id vorhanden
        * ``address_incomplete`` - Strasse/PLZ/Ort/Land fehlt, wird nicht gesendet
        * ``new``                - wird angelegt
        """
        states = db.gsmtasks_stop_states(conn, delivery_date)
        plan = []
        for index, stop in enumerate(draft["stops"]):
            external_id = build_external_id(
                delivery_date, stop["customer_id"], app.config["GSMTASKS_EXTERNAL_ID_PREFIX"]
            )
            entry = {
                "stop": stop,
                "position": (index + 1) * 100,
                "external_id": external_id,
                "order_url": None,
                "previous_outcome": states.get(stop["customer_id"], {}).get("outcome"),
            }
            state = states.get(stop["customer_id"], {})
            if state.get("transferred"):
                entry["status"] = "transferred"
                entry["order_url"] = state.get("order_url")
            elif missing_address_parts(stop.get("address_parts")):
                entry["status"] = "address_incomplete"
            else:
                existing = client.find_orders_by_external_id(external_id)
                if existing:
                    entry["status"] = "existing"
                    entry["order_url"] = existing[0].get("url")
                else:
                    entry["status"] = "new"
            plan.append(entry)
        return plan

    def _plan_summary(plan: list[dict]) -> dict[str, Any]:
        counts = {status: 0 for status in ("new", "existing", "transferred", "address_incomplete")}
        for entry in plan:
            counts[entry["status"]] += 1
        return {
            "stop_count": len(plan),
            "new_count": counts["new"],
            "existing_count": counts["existing"],
            "transferred_count": counts["transferred"],
            "address_incomplete": [
                {
                    "customer_name": e["stop"]["customer_name"],
                    "missing": missing_address_parts(e["stop"].get("address_parts")),
                }
                for e in plan
                if e["status"] == "address_incomplete"
            ],
            "unclear_before": sum(1 for e in plan if e["previous_outcome"] == OUTCOME_UNCLEAR
                                  and e["status"] != "transferred"),
            "stops": [
                {"customer_name": e["stop"]["customer_name"], "status": e["status"],
                 "order_url": e["order_url"]}
                for e in plan
            ],
        }

    def _load_draft_for_gsmtasks(conn: Any, delivery_date: str) -> tuple[dict | None, Response | None]:
        # Dauerhafte Sperre zuerst - vor jedem GSMtasks-Zugriff (auch lesend).
        lock = db.get_gsmtasks_lock(conn, delivery_date)
        if lock is not None:
            return None, (
                jsonify({
                    "error": f"GSMtasks-Uebertragung fuer {delivery_date} ist dauerhaft gesperrt: "
                    f"{lock['reason']}",
                    "locked": lock,
                }),
                423,
            )
        draft = db.get_draft(conn, delivery_date)
        if draft is None:
            return None, (jsonify({"error": "Kein Tourentwurf fuer dieses Datum."}), 404)
        if not draft["stops"]:
            return None, (jsonify({"error": "Der Tourentwurf enthaelt keine Stopps."}), 400)
        return draft, None

    @app.get("/api/draft/<delivery_date>/gsmtasks/precheck")
    @handle_errors
    def gsmtasks_precheck(delivery_date: str) -> Response:
        """Rein lesende Vorabpruefung fuer den Bestaetigungsdialog: API-Schluessel
        gueltig, Adressen vollstaendig, welche Stopps neu bzw. schon vorhanden
        sind. Legt nichts an."""
        _parse_date(delivery_date)
        conn = get_db()
        draft, error = _load_draft_for_gsmtasks(conn, delivery_date)
        if error:
            return error
        client: GSMtasksClient = app.config["GSMTASKS_CLIENT_FACTORY"]()
        client.resolve_account_url()  # prueft den API-Schluessel (GET /accounts/)
        summary = _plan_summary(_plan_gsmtasks_transfer(conn, client, delivery_date, draft))
        summary["ready"] = summary["new_count"] > 0 and not summary["address_incomplete"]
        return jsonify(summary)

    @app.post("/api/draft/<delivery_date>/gsmtasks")
    @handle_errors
    def transfer_to_gsmtasks(delivery_date: str) -> Response:
        """Uebertraegt den aktuellen Tourentwurf als GSMtasks drop_off-Tasks.

        Nur nach expliziter Bestaetigung im Web (``{"confirm": true}``).
        Reihenfolge kommt aus dem Entwurf (position 100, 200, ...), keine
        Fahrerzuweisung (kein assignee, auto_assign False).

        Duplikatschutz - alles VOR dem ersten Schreibzugriff:
        * Stopps, die laut lokaler Historie schon angelegt sind, werden
          uebersprungen.
        * Fuer alle uebrigen wird in GSMtasks nach unserer external_id gesucht
          (lesend); vorhandene werden als "existing" vermerkt, nicht neu angelegt.
        * Kann diese Pruefung nicht vollstaendig erfolgen, wird nichts gesendet.
        * Unvollstaendige Adressen blockieren die gesamte Uebertragung.
        Waehrend des Sendens: ist der Ausgang eines Stopps unklar (Timeout,
        Abbruch, 5xx), werden die restlichen Stopps nicht mehr gesendet; der
        naechste Versuch klaert den Stopp per Suche nach der external_id.
        Bestehende GSMtasks-Daten werden nie geaendert oder geloescht.
        """
        _parse_date(delivery_date)
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm") is not True:
            raise ValueError("Uebertragung nicht bestaetigt - bitte im Dialog bestaetigen.")

        conn = get_db()
        draft, error = _load_draft_for_gsmtasks(conn, delivery_date)
        if error:
            return error
        if draft["gsmtasks"]["already_transferred"]:
            return jsonify({"error": "Diese Tour wurde bereits vollstaendig an GSMtasks uebertragen."}), 409

        client: GSMtasksClient = app.config["GSMTASKS_CLIENT_FACTORY"]()
        account_url = client.resolve_account_url()
        plan = _plan_gsmtasks_transfer(conn, client, delivery_date, draft)

        incomplete = [e["stop"]["customer_name"] for e in plan if e["status"] == "address_incomplete"]
        if incomplete:
            raise ValueError(
                "Nichts uebertragen: Lieferadresse unvollstaendig (Strasse/PLZ/Ort/Land) bei "
                + ", ".join(incomplete)
                + ". Bitte Adresse in Zoho ergaenzen und \"Tour laden\" ausfuehren."
            )

        results: list[OrderResult] = []
        stop_sending = False
        for entry in plan:
            stop = entry["stop"]
            if entry["status"] == "transferred":
                continue
            if entry["status"] == "existing":
                results.append(OrderResult(
                    customer_id=stop["customer_id"], customer_name=stop["customer_name"],
                    external_id=entry["external_id"], outcome=OUTCOME_EXISTING,
                    order_url=entry["order_url"],
                    error="Bereits in GSMtasks vorhanden - nicht erneut angelegt",
                ))
                continue
            if stop_sending:
                results.append(OrderResult(
                    customer_id=stop["customer_id"], customer_name=stop["customer_name"],
                    external_id=entry["external_id"], outcome=OUTCOME_NOT_SENT,
                    error="Nicht gesendet, weil ein vorheriger Stopp einen unklaren Ausgang hatte",
                ))
                continue
            task = DropoffTask(
                customer_id=stop["customer_id"],
                external_id=entry["external_id"],
                customer_name=stop["customer_name"],
                address_parts=stop["address_parts"],
                position=entry["position"],
                delivery_date=delivery_date,
            )
            result = client.create_dropoff_order(task, account_url)
            results.append(result)
            if result.outcome == OUTCOME_UNCLEAR:
                stop_sending = True

        batch = db.record_gsmtasks_transfer(conn, delivery_date, results)
        logger.info(
            "GSMtasks-Uebertragung %s: Status %s, %s erfolgreich, %s nicht erfolgreich",
            delivery_date, batch["status"], batch["success_count"], batch["failure_count"],
        )
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
        # Ein bestehendes Profil verliert dabei keine Kunden, die an diesem
        # Liefertag zufaellig keinen Auftrag haben.
        existing = db.get_profile(conn, name)
        if existing is not None:
            customer_ids = db.merge_profile_order(existing["customer_ids"], customer_ids)
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
