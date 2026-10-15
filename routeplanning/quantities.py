"""Baut Lieferstopps und Verpackungsmengen aus Zoho-Sales-Order-Details.

Reine Transformation, keine Netzwerkzugriffe: Aufrufer beschafft die
Auftragsdetails (inkl. line_items, shipping_address) und die Produktart je
Artikel und uebergibt sie hier fertig.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .article_rules import (
    EINZEL_1_9,
    KOMPLETTE_VE,
    KRESSE,
    SCHNITTWARE,
    classify_article,
    default_rules,
)

#: Zoho-Feldbezeichnungen, unter denen der Custom-Field-Name auftauchen kann -
#: je nach API-Antwort/Konto heisst das Attribut unterschiedlich.
_LABEL_KEYS = ("label", "field_name", "placeholder", "customfield_name")

#: Warnungsgruende (``ArticleWarning.reason``).
REASON_UNKLASSIFIZIERT = "unklassifiziert"
REASON_VE_NICHT_GANZZAHLIG = "ve_nicht_ganzzahlig"
REASON_VE_GROESSE_WIDERSPRUCH = "ve_groesse_widerspruch"
REASON_ADRESSE_UNVOLLSTAENDIG = "adresse_unvollstaendig"

#: Wert des Custom Fields "VE Stück", z.B. "9 Stk." oder "12".
_VE_STUECK_PATTERN = re.compile(r"^\s*(\d+)\s*(stk\.?)?\s*$", re.IGNORECASE)

#: Pflichtbestandteile einer anfahrbaren Lieferadresse (Zoho-Feld -> Anzeige).
_ADDRESS_REQUIRED = (("address", "Straße"), ("zip", "PLZ"), ("city", "Ort"))


@dataclass
class ArticleWarning:
    """Sichtbarer Hinweis zu einem Stopp. Positionen mit Warnung fliessen nie
    in die Summen ein - es wird keine Menge angenommen."""

    customer_id: str
    customer_name: str
    article_name: str
    quantity: float
    reason: str = REASON_UNKLASSIFIZIERT


@dataclass
class Stop:
    customer_id: str
    customer_name: str
    address: str
    kisten: int
    schnittware: float
    kresse: int
    order_numbers: list[str] = field(default_factory=list)
    phone: str = ""
    email: str = ""
    #: Strukturierte Lieferadresse fuer GSMtasks (street, street2, zip, city,
    #: country) - aus derselben Zoho-Adresse wie ``address``.
    address_parts: dict[str, str] = field(default_factory=dict)


def produktart_from_item(item: dict[str, Any], label: str) -> str | None:
    """Liest den Wert des Custom Fields ``label`` aus einem Zoho-Item."""
    wanted = label.strip().lower()
    for cf in item.get("custom_fields") or []:
        for key in _LABEL_KEYS:
            if (cf.get(key) or "").strip().lower() == wanted:
                return cf.get("value")
    return None


def ve_stueck_from_item(item: dict[str, Any], label: str) -> int | None:
    """Liest die VE-Groesse (Stueck je Verpackungseinheit) aus dem Custom
    Field ``label`` eines Zoho-Items. ``None``, wenn nicht vorhanden oder
    nicht eindeutig als ganze Stueckzahl lesbar."""
    match = _VE_STUECK_PATTERN.match(str(produktart_from_item(item, label) or ""))
    if not match or int(match.group(1)) <= 0:
        return None
    return int(match.group(1))


def missing_address_parts(shipping_address: dict[str, Any] | None) -> list[str]:
    """Pflichtbestandteile (Straße/PLZ/Ort), die in der Lieferadresse fehlen."""
    shipping_address = shipping_address or {}
    return [
        label
        for key, label in _ADDRESS_REQUIRED
        if not str(shipping_address.get(key) or "").strip()
    ]


def address_parts_from_zoho(shipping_address: dict[str, Any] | None) -> dict[str, str]:
    """Strukturierte Lieferadresse (ohne Ansprechpartner-Zeile)."""
    shipping_address = shipping_address or {}
    return {
        key: str(shipping_address.get(source) or "").strip()
        for key, source in (
            ("street", "address"),
            ("street2", "street2"),
            ("zip", "zip"),
            ("city", "city"),
            ("country", "country"),
        )
    }


def format_address(shipping_address: dict[str, Any] | None) -> str:
    """Formatiert eine Zoho-Lieferadresse als mehrzeiligen String."""
    if not shipping_address:
        return ""
    lines = []
    attention = shipping_address.get("attention")
    if attention:
        lines.append(attention)
    street = ", ".join(
        part
        for part in (shipping_address.get("address"), shipping_address.get("street2"))
        if part
    )
    if street:
        lines.append(street)
    city_line = " ".join(
        part
        for part in (shipping_address.get("zip"), shipping_address.get("city"))
        if part
    )
    if city_line:
        lines.append(city_line)
    country = shipping_address.get("country")
    if country:
        lines.append(country)
    return "\n".join(lines)


def _first_contact_email(order: dict[str, Any]) -> str:
    """Liest die E-Mail des (ersten) Ansprechpartners aus bereits geladenen
    Zoho-Auftragsdaten - keine zusaetzliche API-Abfrage noetig."""
    for person in order.get("contact_person_details") or []:
        email = (person.get("email") or "").strip()
        if email:
            return email
    return ""


def build_stops(
    orders: list[dict[str, Any]],
    get_produktart: Callable[[str], str | None],
    get_ve_stueck: Callable[[str], int | None] | None = None,
) -> tuple[list[Stop], list[ArticleWarning]]:
    """Gruppiert Auftraege nach Kunde und berechnet die Verpackungsmengen.

    ``get_produktart`` liefert zu einer ``item_id`` den Wert des Zoho-Custom-
    Fields "Produktart", falls vorhanden (siehe :func:`produktart_from_item`).
    ``get_ve_stueck`` liefert die VE-Groesse aus dem Artikelstamm (Custom
    Field "VE Stück", siehe :func:`ve_stueck_from_item`); fehlt sie, gilt die
    Vorgabe aus ``article_rules.yaml`` (``ve_groesse``).

    Mengenregeln:

    * EINZEL_1_9: alle Sorten eines Stopps teilen sich die Kisten,
      angebrochene Kisten zaehlen voll (ceil). Widerspricht die VE-Groesse
      im Artikelstamm der 9er-Kiste, wird die Position nicht gezaehlt,
      sondern als Warnung ausgewiesen.
    * KRESSE: je VE-Groesse (Artikelstamm, sonst Vorgabe) aufgerundet.
    * KOMPLETTE_VE: 1 bestellte Einheit = 1 Kiste. Nicht ganzzahlige Mengen
      werden nicht gerundet, sondern als Warnung ausgewiesen.
    * SCHNITTWARE: Anzahl bestellter Verkaufseinheiten, ungerundet.
    """
    ve_defaults = default_rules().get("ve_groesse") or {}
    einzel_ve = int(ve_defaults.get(EINZEL_1_9, 9))
    kresse_default_ve = int(ve_defaults.get(KRESSE, 12))

    accumulators: dict[str, dict[str, Any]] = {}
    warnings: list[ArticleWarning] = []

    for order in orders:
        customer_id = order.get("customer_id") or ""
        customer_name = order.get("customer_name") or ""
        address = format_address(order.get("shipping_address"))
        address_missing = missing_address_parts(order.get("shipping_address"))
        address_parts = address_parts_from_zoho(order.get("shipping_address"))

        phone = (order.get("shipping_address") or {}).get("phone") or ""
        email = _first_contact_email(order)

        acc = accumulators.get(customer_id)
        if acc is None:
            acc = {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "address": address,
                "address_parts": address_parts,
                "address_missing": address_missing,
                "phone": phone,
                "email": email,
                "einzel_1_9": 0.0,
                "kresse": {},  # VE-Groesse -> Stueck
                "schnittware": 0.0,
                "komplette_ve": 0.0,
                "order_numbers": [],
            }
            accumulators[customer_id] = acc
        else:
            if not acc["address"] and address:
                acc["address"] = address
                acc["address_parts"] = address_parts
                acc["address_missing"] = address_missing
            if not acc["phone"] and phone:
                acc["phone"] = phone
            if not acc["email"] and email:
                acc["email"] = email

        order_number = order.get("salesorder_number")
        if order_number:
            acc["order_numbers"].append(order_number)

        for line in order.get("line_items") or []:
            name = line.get("name") or ""
            quantity = float(line.get("quantity") or 0)
            item_id = line.get("item_id")
            produktart = get_produktart(item_id) if item_id else None
            classification = classify_article(name, produktart)
            article_class = classification.article_class
            ve_stueck = (
                get_ve_stueck(item_id)
                if get_ve_stueck and item_id and article_class in (EINZEL_1_9, KRESSE)
                else None
            )

            def warn(reason: str, label: str = name) -> None:
                warnings.append(
                    ArticleWarning(
                        customer_id=customer_id,
                        customer_name=customer_name,
                        article_name=label,
                        quantity=quantity,
                        reason=reason,
                    )
                )

            if article_class == EINZEL_1_9:
                if ve_stueck is not None and ve_stueck != einzel_ve:
                    warn(
                        REASON_VE_GROESSE_WIDERSPRUCH,
                        f"{name} (Artikelstamm VE {ve_stueck} Stk., Kiste {einzel_ve} Stk.)",
                    )
                else:
                    acc["einzel_1_9"] += quantity
            elif article_class == KRESSE:
                size = ve_stueck or kresse_default_ve
                acc["kresse"][size] = acc["kresse"].get(size, 0.0) + quantity
            elif article_class == SCHNITTWARE:
                acc["schnittware"] += quantity
            elif article_class == KOMPLETTE_VE:
                if quantity != int(quantity):
                    warn(REASON_VE_NICHT_GANZZAHLIG)
                else:
                    acc["komplette_ve"] += quantity
            else:
                warn(REASON_UNKLASSIFIZIERT)

    stops = []
    for acc in accumulators.values():
        kisten = math.ceil(acc["einzel_1_9"] / einzel_ve) + int(acc["komplette_ve"])
        kresse = sum(math.ceil(pieces / size) for size, pieces in acc["kresse"].items())
        if acc["address_missing"]:
            warnings.append(
                ArticleWarning(
                    customer_id=acc["customer_id"],
                    customer_name=acc["customer_name"],
                    article_name="Lieferadresse unvollständig, fehlt: "
                    + ", ".join(acc["address_missing"]),
                    quantity=0,
                    reason=REASON_ADRESSE_UNVOLLSTAENDIG,
                )
            )
        stops.append(
            Stop(
                customer_id=acc["customer_id"],
                customer_name=acc["customer_name"],
                address=acc["address"],
                kisten=kisten,
                schnittware=acc["schnittware"],
                kresse=kresse,
                order_numbers=acc["order_numbers"],
                phone=acc["phone"],
                email=acc["email"],
                address_parts=acc["address_parts"],
            )
        )
    return stops, warnings
