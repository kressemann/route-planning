"""GSMtasks-Client: Verbindungstest und Schreibintegration.

``test_connection`` bleibt ein reiner Health-Check (GET). Die Schreibmethoden
(``resolve_account_url``, ``create_dropoff_order``) legen genau einen Order
mit genau einem drop_off-Task an (``POST /orders/``) - sie werden ausschliess-
lich vom manuellen "An GSMtasks uebertragen"-Workflow nach expliziter
Bestaetigung im Web aufgerufen (siehe app.py). Es gibt keine automatische
Uebertragung und keinen Aufruf einer Routenoptimierungs-API. Zoho ist von
dieser Datei nicht betroffen und bleibt unveraendert read-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

#: Aktuelle offizielle API (siehe https://developer.gsmtasks.com,
#: OpenAPI-Schema unter https://api.gsmtasks.com/docs/schema/). Die alte,
#: beim ersten Verbindungstest verwendete Basis "https://gsmtasks.com/api/tasks"
#: funktioniert weiterhin (gleicher Account, gleicher Key), ist laut
#: Aufgabenstellung aber nicht mehr die aktuelle - deshalb hier umgestellt.
BASE_URL = "https://api.gsmtasks.com"

#: Die API verlangt eine Versionsangabe im Accept-Header, sonst HTTP 406
#: ("Unspecified version in `Accept` header"). Entspricht der Version im
#: offiziellen OpenAPI-Schema (openapi.info.version).
API_VERSION = "2.11.4"

DROP_OFF_CATEGORY = "drop_off"


class GSMtasksError(RuntimeError):
    pass


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Liest KEY=VALUE-Zeilen. Umgebungsvariablen haben Vorrang."""
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    if os.environ.get("GSMTASKS_API_KEY"):
        values["GSMTASKS_API_KEY"] = os.environ["GSMTASKS_API_KEY"]
    return values


def build_external_id(delivery_date: str, customer_id: str) -> str:
    """Deterministische external_id je Stopp (Lieferdatum + Zoho-customer_id).

    Wird sowohl als Order- als auch als Task-external_id verwendet. GSMtasks
    erkennt anhand der Order-external_id bereits vorhandene Bestellungen und
    aktualisiert sie statt sie zu duplizieren (offizielle API-Doku) - das ist
    die serverseitige zweite Schutzschicht gegen Duplikate. Die primaere
    Schutzschicht ist die lokale Pruefung in db.py vor dem Versand.
    """
    return f"routeplanning::{delivery_date}::{customer_id}"


def build_description(kisten: float, schnittware: float, kresse: float) -> str:
    """Fahrer-lesbare Task-Beschreibung. Nullmengen werden weggelassen."""
    lines = []
    if kisten:
        lines.append(f"Kisten: {_format_quantity(kisten)}")
    if schnittware:
        lines.append(f"Schnittware: {_format_quantity(schnittware)}")
    if kresse:
        lines.append(f"Kresse: {_format_quantity(kresse)}")
    return "\n".join(lines)


def _format_quantity(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


@dataclass
class DropoffTask:
    """Eingabedaten fuer genau einen Tourenstopp -> genau einen drop_off-Task."""

    customer_id: str
    external_id: str
    reference: str
    customer_name: str
    address: str
    description: str
    position: float
    phone: str = ""
    email: str = ""


@dataclass
class OrderResult:
    """Ergebnis eines einzelnen Order-/Task-Anlegeversuchs - wird pro Stopp
    lokal gespeichert (siehe db.record_gsmtasks_transfer)."""

    customer_id: str
    customer_name: str
    external_id: str
    success: bool
    order_id: str | None = None
    order_url: str | None = None
    task_url: str | None = None
    error: str | None = None


def build_order_payload(task: DropoffTask, account_url: str) -> dict[str, Any]:
    """Baut den Request-Body fuer ``POST /orders/`` gemaess OrderSerializerV2/
    TaskSerializerV2 (offizielles OpenAPI-Schema). Ein Order mit genau einem
    drop_off-Task, keine Routenoptimierung, kein auto_assign."""
    contact: dict[str, Any] = {"name": task.customer_name, "company": task.customer_name}
    if task.phone:
        contact["phones"] = [task.phone]
    if task.email:
        contact["emails"] = [task.email]

    return {
        "account": account_url,
        "external_id": task.external_id,
        "reference": task.reference,
        "auto_assign": False,
        "tasks_data": [
            {
                "external_id": task.external_id,
                "category": DROP_OFF_CATEGORY,
                "contact": contact,
                "address": {"raw_address": task.address},
                "description": task.description,
                "position": task.position,
                "auto_assign": False,
            }
        ],
    }


def _safe_error_detail(response: requests.Response) -> str:
    """Wie in zoho.py: nur HTTP-Status + vom Server zurueckgegebene
    Fehlerfelder - die stehen nie im Response-Body und enthalten daher nie
    den Authorization-Header/API-Key."""
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: keine JSON-Antwort ({response.text[:200]!r})"
    return f"HTTP {response.status_code}: {payload!r}"[:500]


class GSMtasksClient:
    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls, path: Path = ENV_FILE) -> "GSMtasksClient":
        env = load_env(path)
        api_key = env.get("GSMTASKS_API_KEY")
        if not api_key:
            raise GSMtasksError(
                "GSMTASKS_API_KEY fehlt. Bitte in "
                f"{path} eintragen (Vorlage: .env.example)."
            )
        return cls(api_key=api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.api_key}",
            "Accept": f"application/json; version={API_VERSION}",
        }

    def test_connection(self) -> bool:
        """Prueft Authentifizierung/Verbindung ueber ``GET /accounts/``.

        Rein lesend. Gibt bei Erfolg (HTTP 200) ``True`` zurueck. Wirft bei
        Fehlern ``GSMtasksError`` mit Statuscode - der API-Key erscheint
        niemals in der Fehlermeldung.
        """
        try:
            response = self.session.get(
                f"{BASE_URL}/accounts/", headers=self._headers(), timeout=30
            )
        except requests.RequestException as exc:
            raise GSMtasksError(f"GSMtasks nicht erreichbar: {type(exc).__name__}") from None

        if response.status_code == 200:
            return True

        raise GSMtasksError(
            f"GSMtasks-Verbindungstest fehlgeschlagen: HTTP {response.status_code}"
        )

    def resolve_account_url(self) -> str:
        """Ermittelt die Account-URL fuer den API-Key (``GET /accounts/``) -
        wird als ``account``-Referenz beim Anlegen von Orders benoetigt.
        Wird einmal pro Uebertragungsversuch aufgerufen, nicht pro Stopp.
        Rein lesend."""
        try:
            response = self.session.get(
                f"{BASE_URL}/accounts/", headers=self._headers(), timeout=30
            )
        except requests.RequestException as exc:
            raise GSMtasksError(f"GSMtasks nicht erreichbar: {type(exc).__name__}") from None

        if response.status_code != 200:
            raise GSMtasksError(
                f"Account konnte nicht ermittelt werden: HTTP {response.status_code}"
            )
        try:
            accounts = response.json()
        except ValueError:
            raise GSMtasksError("Account konnte nicht ermittelt werden: keine JSON-Antwort") from None
        if not accounts:
            raise GSMtasksError("Kein GSMtasks-Account fuer diesen API-Key gefunden.")
        return accounts[0]["url"]

    def create_dropoff_order(self, task: DropoffTask, account_url: str) -> OrderResult:
        """Legt genau einen Order mit genau einem drop_off-Task an
        (``POST /orders/``). Schlaegt dieser eine Stopp fehl, wird das als
        ``OrderResult(success=False, ...)`` zurueckgegeben statt eine
        Exception zu werfen - ein fehlgeschlagener Stopp darf den restlichen
        Batch nicht abbrechen (siehe app.py)."""
        payload = build_order_payload(task, account_url)
        try:
            response = self.session.post(
                f"{BASE_URL}/orders/", headers=self._headers(), json=payload, timeout=30
            )
        except requests.RequestException as exc:
            return OrderResult(
                customer_id=task.customer_id,
                customer_name=task.customer_name,
                external_id=task.external_id,
                success=False,
                error=f"GSMtasks nicht erreichbar: {type(exc).__name__}",
            )

        if response.status_code not in (200, 201):
            return OrderResult(
                customer_id=task.customer_id,
                customer_name=task.customer_name,
                external_id=task.external_id,
                success=False,
                error=_safe_error_detail(response),
            )

        try:
            body = response.json()
        except ValueError:
            return OrderResult(
                customer_id=task.customer_id,
                customer_name=task.customer_name,
                external_id=task.external_id,
                success=False,
                error=f"HTTP {response.status_code}: keine JSON-Antwort",
            )

        tasks = body.get("tasks") or []
        return OrderResult(
            customer_id=task.customer_id,
            customer_name=task.customer_name,
            external_id=task.external_id,
            success=True,
            order_id=body.get("id"),
            order_url=body.get("url"),
            task_url=tasks[0] if tasks else None,
        )


if __name__ == "__main__":
    try:
        GSMtasksClient.from_env().test_connection()
    except GSMtasksError as exc:
        print(f"GSMtasks-Verbindung fehlgeschlagen: {exc}")
        raise SystemExit(1) from None
    else:
        print("GSMtasks-Verbindung erfolgreich")
