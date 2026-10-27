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

import datetime as _dt
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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

#: Ergebnis je Stopp eines Uebertragungsversuchs.
OUTCOME_CREATED = "created"  # in diesem Versuch angelegt (HTTP 200/201)
OUTCOME_EXISTING = "existing"  # Order mit unserer external_id existierte bereits
OUTCOME_FAILED = "failed"  # von GSMtasks eindeutig abgelehnt (4xx) - nicht angelegt
OUTCOME_UNCLEAR = "unclear"  # Ausgang unbekannt (Timeout, Verbindungsabbruch, 5xx)
OUTCOME_NOT_SENT = "not_sent"  # nach einem unklaren Ergebnis bewusst nicht gesendet
SUCCESS_OUTCOMES = frozenset({OUTCOME_CREATED, OUTCOME_EXISTING})

#: Zeitzone der Liefertage - Zeitfenster je Aufgabe = ganzer Liefertag hier.
DELIVERY_TIMEZONE = ZoneInfo("Europe/Berlin")

#: Praefix der external_id. Produktiv immer "routeplanning"; isolierte Tests
#: verwenden einen eigenen Praefix, damit sie nie mit echten oder frueheren
#: Test-Orders kollidieren (siehe create_app(external_id_prefix=...)).
EXTERNAL_ID_PREFIX = "routeplanning"

#: Pflichtbestandteile der Zieladresse (Schluessel aus Stop.address_parts).
ADDRESS_REQUIRED_PARTS = ("street", "zip", "city", "country")

#: Fester Fahrer fuer alle neu angelegten Aufgaben: GSMtasks-User-UUID plus
#: erwarteter Anzeigename seiner Account-Rolle (z. B. "Kressemann"). Bewusst
#: konfiguriert statt per Namenssuche - mehrere Integrations-Benutzer des
#: Accounts haben ebenfalls den Vornamen "Kressemann".
ASSIGNEE_USER_ID_KEY = "GSMTASKS_ASSIGNEE_USER_ID"
ASSIGNEE_NAME_KEY = "GSMTASKS_ASSIGNEE_NAME"


class GSMtasksError(RuntimeError):
    pass


class AssigneeConfigError(ValueError):
    """Fahrerzuordnung fehlt, ist ungueltig oder in GSMtasks nicht eindeutig.
    Wird vor dem ersten Schreibzugriff geworfen - es wird dann nichts
    uebertragen (kein Fallback auf "Nicht zugewiesen")."""


def describe_http_error(status_code: int, action: str) -> str:
    """Verstaendliche Fehlermeldung fuer die Oberflaeche - ohne Secrets."""
    if status_code == 401:
        return (
            f"{action}: GSMtasks lehnt den API-Schluessel ab (HTTP 401 \"Invalid token\"). "
            "Der Schluessel ist ungueltig, widerrufen oder neu erzeugt worden - bitte in "
            "GSMtasks einen neuen API-Schluessel erstellen und GSMTASKS_API_KEY in der .env "
            "ersetzen."
        )
    if status_code == 403:
        return (
            f"{action}: keine Berechtigung (HTTP 403). Der Benutzer des API-Schluessels "
            "darf diese Aktion in GSMtasks nicht ausfuehren."
        )
    if status_code == 429:
        return f"{action}: zu viele Anfragen an GSMtasks (HTTP 429) - bitte spaeter erneut versuchen."
    if status_code >= 500:
        return f"{action}: GSMtasks-Serverfehler (HTTP {status_code})."
    return f"{action}: HTTP {status_code}"


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
    for key in ("GSMTASKS_API_KEY", ASSIGNEE_USER_ID_KEY, ASSIGNEE_NAME_KEY):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


@dataclass(frozen=True)
class Assignee:
    """Konfigurierter Fahrer. ``assignee`` eines Tasks ist laut API die
    User-URL (nicht die Account-Rollen-URL)."""

    user_id: str
    name: str

    @property
    def user_url(self) -> str:
        return f"{BASE_URL}/users/{self.user_id}/"


def load_assignee(env: dict[str, str]) -> Assignee:
    """Fahrer aus der Konfiguration - nur Formatpruefung, kein Netzwerk.
    Ob der Fahrer in GSMtasks existiert, prueft ``verify_assignee``."""
    user_id = (env.get(ASSIGNEE_USER_ID_KEY) or "").strip()
    name = (env.get(ASSIGNEE_NAME_KEY) or "").strip()
    if not user_id or not name:
        missing = [k for k, v in ((ASSIGNEE_USER_ID_KEY, user_id), (ASSIGNEE_NAME_KEY, name)) if not v]
        raise AssigneeConfigError(
            "Fahrerzuordnung nicht konfiguriert (" + ", ".join(missing) + " fehlt) - "
            "es wird nichts an GSMtasks uebertragen."
        )
    try:
        parsed = uuid.UUID(user_id)
    except ValueError:
        raise AssigneeConfigError(
            f"{ASSIGNEE_USER_ID_KEY} ist keine gueltige GSMtasks-User-ID (UUID erwartet) - "
            "es wird nichts an GSMtasks uebertragen."
        ) from None
    return Assignee(user_id=str(parsed), name=name)


def build_external_id(
    delivery_date: str, customer_id: str, prefix: str = EXTERNAL_ID_PREFIX
) -> str:
    """Deterministische external_id je Stopp (Praefix + Lieferdatum + Zoho-customer_id).

    Wird als Order- und Task-external_id verwendet. Duplikatschutz: vor dem
    Senden wird lokal (db.gsmtasks_stop_states) und per
    ``GET /orders/?external_id=`` geprueft, ob der Stopp schon existiert. Ob
    GSMtasks selbst doppelte external_ids abweist, ist im Schema nicht
    dokumentiert - darauf wird sich bewusst nicht verlassen.
    """
    return f"{prefix}::{delivery_date}::{customer_id}"


def delivery_window(delivery_date: str) -> tuple[str, str]:
    """Zeitfenster der Aufgabe: Liefertag 00:00:00 bis 23:59:59 Europe/Berlin,
    als ISO-8601 mit dem an diesem Tag gueltigen UTC-Offset (+01:00/+02:00).
    Massgeblich ist ausschliesslich das Lieferdatum des Tourentwurfs."""
    day = _dt.date.fromisoformat(delivery_date)
    start = _dt.datetime.combine(day, _dt.time(0, 0, 0), tzinfo=DELIVERY_TIMEZONE)
    end = _dt.datetime.combine(day, _dt.time(23, 59, 59), tzinfo=DELIVERY_TIMEZONE)
    return start.isoformat(), end.isoformat()


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
    customer_name: str
    #: Strukturierte Zoho-Lieferadresse (street, street2, zip, city, country).
    address_parts: dict[str, str]
    position: float
    #: Liefertag des Tourentwurfs (JJJJ-MM-TT) -> complete_after/complete_before.
    delivery_date: str
    #: User-URL des zugewiesenen Fahrers (Assignee.user_url) - Pflicht.
    assignee_url: str = ""


@dataclass
class OrderResult:
    """Ergebnis je Stopp - wird lokal gespeichert (db.record_gsmtasks_transfer).

    ``outcome`` siehe OUTCOME_*; fehlt es, wird es aus ``success`` abgeleitet
    (created/failed). ``success`` ist True genau fuer created/existing."""

    customer_id: str
    customer_name: str
    external_id: str
    success: bool = False
    order_id: str | None = None
    order_url: str | None = None
    task_url: str | None = None
    error: str | None = None
    outcome: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.outcome is None:
            self.outcome = OUTCOME_CREATED if self.success else OUTCOME_FAILED
        self.success = self.outcome in SUCCESS_OUTCOMES


def missing_address_parts(parts: dict[str, str] | None) -> list[str]:
    """Pflichtbestandteile der Zieladresse, die fehlen (leer = vollstaendig)."""
    parts = parts or {}
    return [key for key in ADDRESS_REQUIRED_PARTS if not str(parts.get(key) or "").strip()]


def build_address(parts: dict[str, str]) -> dict[str, str]:
    """Zieladresse fuer GSMtasks (NestedAddress): einzeilige ``raw_address``
    ohne Ansprechpartner-/Firmenzeile plus strukturierte Felder, damit
    GSMtasks eindeutig geokodieren kann. Die Hausnummer bleibt Teil von
    ``street`` - Zoho liefert sie nicht getrennt, und eine Zerlegung waere
    geraten."""
    street = str(parts.get("street") or "").strip()
    street2 = str(parts.get("street2") or "").strip()
    postal_code = str(parts.get("zip") or "").strip()
    city = str(parts.get("city") or "").strip()
    country = str(parts.get("country") or "").strip()
    raw = ", ".join(
        part for part in (street, street2, f"{postal_code} {city}".strip(), country) if part
    )
    return {
        "raw_address": raw,
        "street": street,
        "postal_code": postal_code,
        "city": city,
        "country": country,
    }


def build_order_payload(task: DropoffTask, account_url: str) -> dict[str, Any]:
    """Request-Body fuer ``POST /orders/`` (OrderSerializerV2/TaskSerializerV2,
    offizielles OpenAPI-Schema 2.11.x). Bewusst minimal:

    * ``account`` - einziges beschreibbares Pflichtfeld laut Schema
    * ``external_id`` - Duplikatschutz (Vorabpruefung per GET /orders/?external_id=)
    * ``contact.name`` = Kundenname, ``address`` = Lieferadresse
    * ``position`` = Reihenfolge der Tourenplanung
    * ``complete_after``/``complete_before`` = Liefertag 00:00-23:59:59
      Europe/Berlin (ohne diese Felder setzt GSMtasks den Zeitpunkt der
      Uebertragung - im Test am 15.10.2026 beobachtet)
    * ``assignee`` = User-URL des konfigurierten Fahrers (nur am Task - das
      Order-Feld ``assignee`` gibt es seit API 2.4.6 nicht mehr)
    * ``auto_assign: False`` - keine automatische Fahrerwahl durch GSMtasks
    * ``category: drop_off`` - Auslieferung (wie bisher)
    Keine Routenoptimierung, keine Beschreibung/Telefon/E-Mail/Referenz.
    """
    if not task.assignee_url:
        raise AssigneeConfigError("Kein Fahrer fuer die Aufgabe gesetzt - es wird nichts uebertragen.")
    complete_after, complete_before = delivery_window(task.delivery_date)
    return {
        "account": account_url,
        "external_id": task.external_id,
        "auto_assign": False,
        "tasks_data": [
            {
                "external_id": task.external_id,
                "category": DROP_OFF_CATEGORY,
                "contact": {"name": task.customer_name},
                "address": build_address(task.address_parts),
                "position": task.position,
                "complete_after": complete_after,
                "complete_before": complete_before,
                "assignee": task.assignee_url,
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
                describe_http_error(response.status_code, "Account konnte nicht ermittelt werden")
            )
        try:
            accounts = response.json()
        except ValueError:
            raise GSMtasksError("Account konnte nicht ermittelt werden: keine JSON-Antwort") from None
        if not accounts:
            raise GSMtasksError("Kein GSMtasks-Account fuer diesen API-Key gefunden.")
        return accounts[0]["url"]

    def verify_assignee(self, assignee: Assignee) -> Assignee:
        """Rein lesend (``GET /account_roles/?user=<uuid>``): Der konfigurierte
        Fahrer muss genau eine aktive Fahrer-Rolle mit dem erwarteten
        Anzeigenamen haben. Sonst ``AssigneeConfigError`` - der Aufrufer
        sendet dann nichts."""
        try:
            response = self.session.get(
                f"{BASE_URL}/account_roles/",
                headers=self._headers(),
                params={"user": assignee.user_id},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GSMtasksError(f"GSMtasks nicht erreichbar: {type(exc).__name__}") from None
        if response.status_code == 400:
            # GSMtasks lehnt unbekannte User-UUIDs im Filter mit 400 ab.
            raise AssigneeConfigError(
                f"Fahrer-ID {assignee.user_id} ist in GSMtasks unbekannt - es wird nichts uebertragen."
            )
        if response.status_code != 200:
            raise GSMtasksError(
                describe_http_error(response.status_code, "Fahrer konnte nicht geprueft werden")
            )
        try:
            roles = response.json()
        except ValueError:
            raise GSMtasksError("Fahrer konnte nicht geprueft werden: keine JSON-Antwort") from None
        if isinstance(roles, dict):  # falls paginiert mit "results"
            roles = roles.get("results") or []
        roles = [r for r in roles if r.get("user") == assignee.user_url and not r.get("deleted_at")]
        if len(roles) != 1:
            raise AssigneeConfigError(
                f"Fahrer-ID {assignee.user_id} nicht eindeutig: {len(roles)} Account-Rollen in "
                "GSMtasks gefunden - es wird nichts uebertragen."
            )
        role = roles[0]
        if not (role.get("is_worker") and role.get("is_active") and role.get("state") == "active"):
            raise AssigneeConfigError(
                f"Fahrer-ID {assignee.user_id} ist in GSMtasks kein aktiver Fahrer - "
                "es wird nichts uebertragen."
            )
        display_name = (role.get("display_name") or "").strip()
        if display_name != assignee.name:
            raise AssigneeConfigError(
                f"Fahrer-ID {assignee.user_id} heisst in GSMtasks {display_name!r}, konfiguriert ist "
                f"{assignee.name!r} - es wird nichts uebertragen."
            )
        return assignee

    def find_orders_by_external_id(self, external_id: str) -> list[dict[str, Any]]:
        """Rein lesend: vorhandene Orders mit genau dieser external_id
        (``GET /orders/?external_id=...``). Wirft GSMtasksError, wenn das
        nicht eindeutig beantwortet werden kann - der Aufrufer sendet dann
        nichts."""
        try:
            response = self.session.get(
                f"{BASE_URL}/orders/",
                headers=self._headers(),
                params={"external_id": external_id},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GSMtasksError(f"GSMtasks nicht erreichbar: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise GSMtasksError(
                describe_http_error(response.status_code, "Vorabpruefung auf vorhandene Auftraege fehlgeschlagen")
            )
        try:
            orders = response.json()
        except ValueError:
            raise GSMtasksError("Vorabpruefung fehlgeschlagen: keine JSON-Antwort") from None
        if isinstance(orders, dict):  # falls paginiert mit "results"
            orders = orders.get("results") or []
        return [o for o in orders if o.get("external_id") == external_id]

    def create_dropoff_order(self, task: DropoffTask, account_url: str) -> OrderResult:
        """Legt genau einen Order mit genau einem drop_off-Task an
        (``POST /orders/``). Schlaegt dieser eine Stopp fehl, wird das als
        ``OrderResult(success=False, ...)`` zurueckgegeben statt eine
        Exception zu werfen - ein fehlgeschlagener Stopp darf den restlichen
        Batch nicht abbrechen (siehe app.py)."""
        payload = build_order_payload(task, account_url)

        def result(outcome: str, **kwargs: Any) -> OrderResult:
            return OrderResult(
                customer_id=task.customer_id,
                customer_name=task.customer_name,
                external_id=task.external_id,
                outcome=outcome,
                **kwargs,
            )

        try:
            response = self.session.post(
                f"{BASE_URL}/orders/", headers=self._headers(), json=payload, timeout=30
            )
        except requests.ConnectTimeout:
            # Verbindung kam nie zustande -> Anfrage wurde sicher nicht verarbeitet.
            return result(OUTCOME_FAILED, error="GSMtasks nicht erreichbar (Verbindungsaufbau)")
        except requests.RequestException as exc:
            # Anfrage evtl. schon angekommen (Timeout beim Lesen, Abbruch) ->
            # Ausgang unbekannt, keine automatische Wiederholung.
            return result(
                OUTCOME_UNCLEAR,
                error=f"Ausgang unklar ({type(exc).__name__}) - vor erneutem Versuch wird "
                "in GSMtasks nach dem Stopp gesucht",
            )

        if response.status_code >= 500:
            return result(
                OUTCOME_UNCLEAR,
                error=describe_http_error(response.status_code, "Ausgang unklar")
                + " - vor erneutem Versuch wird in GSMtasks nach dem Stopp gesucht",
            )
        if response.status_code not in (200, 201):
            if response.status_code in (401, 403, 429):
                error = describe_http_error(response.status_code, "Nicht angelegt")
            else:
                error = "Nicht angelegt: " + _safe_error_detail(response)
            return result(OUTCOME_FAILED, error=error)

        try:
            body = response.json()
        except ValueError:
            body = {}
        tasks = body.get("tasks") or []
        return result(
            OUTCOME_CREATED,
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
