"""Schmaler, rein lesender REST-Client fuer die Zoho-Inventory-API (EU).

Bewusst nur die Aufrufe, die die Tourenplanung braucht. Es gibt hier keine
Methode, die Daten nach Zoho schreibt - das ist Absicht (siehe Aufgabenstellung
V1: nur lesender Zugriff).
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from pathlib import Path
from typing import Any, Iterator

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_REGION = "eu"
MAX_RETRIES = 4

#: Status, die NICHT in eine Tour gehoeren: "draft" sind Planungsentwuerfe
#: (siehe production-planning), "void" ist storniert. Alle anderen Status
#: zaehlen - insbesondere "shipped"/"fulfilled": Zoho setzt sie, sobald der
#: Versand gebucht ist, die Lieferung steht dann aber trotzdem auf der Tour.
#: Eine Positivliste hat genau diese Auftraege frueher stillschweigend
#: verloren (live belegt am 14.10.2026: 8 Auftraege "shipped" -> 0 Stopps).
EXCLUDED_STATUSES = frozenset({"draft", "void"})


class ZohoError(RuntimeError):
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
    for key in list(values) + [
        "ZOHO_CLIENT_ID",
        "ZOHO_CLIENT_SECRET",
        "ZOHO_REFRESH_TOKEN",
        "ZOHO_ORGANIZATION_ID",
        "ZOHO_REGION",
    ]:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class ZohoInventory:
    """Rein lesender Client. Enthaelt bewusst keine POST/PUT/DELETE-Aufrufe."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        organization_id: str,
        region: str = DEFAULT_REGION,
        session: requests.Session | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.organization_id = organization_id
        self.region = region
        self.session = session or requests.Session()
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._item_cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls, path: Path = ENV_FILE) -> "ZohoInventory":
        env = load_env(path)
        missing = [
            key
            for key in (
                "ZOHO_CLIENT_ID",
                "ZOHO_CLIENT_SECRET",
                "ZOHO_REFRESH_TOKEN",
                "ZOHO_ORGANIZATION_ID",
            )
            if not env.get(key)
        ]
        if missing:
            raise ZohoError(
                "Zugangsdaten fehlen: "
                + ", ".join(missing)
                + f"\nBitte {ENV_FILE} anlegen (Vorlage: .env.example)."
            )
        return cls(
            client_id=env["ZOHO_CLIENT_ID"],
            client_secret=env["ZOHO_CLIENT_SECRET"],
            refresh_token=env["ZOHO_REFRESH_TOKEN"],
            organization_id=env["ZOHO_ORGANIZATION_ID"],
            region=env.get("ZOHO_REGION", DEFAULT_REGION),
        )

    # -- Auth -----------------------------------------------------------

    @property
    def accounts_url(self) -> str:
        return f"https://accounts.zoho.{self.region}/oauth/v2/token"

    @property
    def base_url(self) -> str:
        return f"https://www.zohoapis.{self.region}/inventory/v1"

    def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at:
            return self._access_token
        response = self.session.post(
            self.accounts_url,
            data={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        payload = response.json()
        if "access_token" not in payload:
            raise ZohoError(
                "Access Token konnte nicht erneuert werden. Antwort: "
                f"{payload.get('error', payload)}"
            )
        self._access_token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - 120
        return self._access_token

    # -- HTTP -----------------------------------------------------------

    @staticmethod
    def _safe_error_detail(response: requests.Response) -> str:
        """Extrahiert Diagnoseinfos aus einer Fehlerantwort - ausschliesslich
        die von Zoho im JSON-Body zurueckgegebenen Felder ``code``/``message``.
        Enthaelt nie Header, Tokens oder sonstige Secrets, da diese nie im
        Response-Body stehen (nur im ausgehenden Request)."""
        try:
            payload = response.json()
        except ValueError:
            return f"keine JSON-Antwort ({response.text[:200]!r})"
        return f"code={payload.get('code')} message={payload.get('message')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if method != "GET":
            raise ZohoError("Dieser Client ist rein lesend - nur GET ist erlaubt.")
        params = {"organization_id": self.organization_id, **kwargs.pop("params", {})}
        url = f"{self.base_url}{path}"

        last_status: int | None = None
        last_detail = ""

        for attempt in range(MAX_RETRIES):
            response = self.session.request(
                method,
                url,
                headers={"Authorization": f"Zoho-oauthtoken {self._token()}"},
                params=params,
                timeout=60,
                **kwargs,
            )
            last_status = response.status_code
            last_detail = self._safe_error_detail(response)
            if response.status_code == 401:
                self._access_token = None
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
            try:
                payload = response.json()
            except ValueError:
                raise ZohoError(
                    f"{method} {path}: HTTP {response.status_code}, "
                    f"keine JSON-Antwort ({response.text[:200]})"
                ) from None
            if response.status_code >= 400 or payload.get("code", 0) != 0:
                raise ZohoError(
                    f"{method} {path}: HTTP {response.status_code} "
                    f"code={payload.get('code')} {payload.get('message')}"
                )
            return payload

        raise ZohoError(
            f"{method} {path}: nach {MAX_RETRIES} Versuchen aufgegeben "
            f"(zuletzt HTTP {last_status}, {last_detail})."
        )

    # -- Endpunkte --------------------------------------------------------

    def iter_sales_orders(self, **filters: Any) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            payload = self._request(
                "GET",
                "/salesorders",
                params={"page": page, "per_page": 200, **filters},
            )
            yield from payload.get("salesorders", [])
            if not payload.get("page_context", {}).get("has_more_page"):
                return
            page += 1

    def sales_orders_shipped_on(self, day: _dt.date) -> list[dict[str, Any]]:
        """Alle Auftraege mit Versand-/Liefertermin ``day`` ausser Entwuerfen und
        stornierten (siehe :data:`EXCLUDED_STATUSES`).

        Der Versandtermin (``shipment_date``) ist in diesem Datenmodell der
        Liefertag. Die API filtert Datumsangaben unzuverlaessig (siehe
        production-planning), deshalb wird lokal nachgefiltert.
        """
        wanted = day.isoformat()
        summaries = [
            order
            for order in self.iter_sales_orders(
                shipment_date_start=wanted, shipment_date_end=wanted
            )
            if order.get("shipment_date") == wanted
            and order.get("status") not in EXCLUDED_STATUSES
        ]
        return [self.get_sales_order(o["salesorder_id"]) for o in summaries]

    def get_sales_order(self, salesorder_id: str) -> dict[str, Any]:
        return self._request("GET", f"/salesorders/{salesorder_id}")["salesorder"]

    def get_item(self, item_id: str) -> dict[str, Any]:
        """Artikel-Stammdaten inkl. Custom Fields (z.B. Produktart). Gecacht."""
        if item_id not in self._item_cache:
            self._item_cache[item_id] = self._request("GET", f"/items/{item_id}")["item"]
        return self._item_cache[item_id]
