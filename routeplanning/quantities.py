"""Baut Lieferstopps und Verpackungsmengen aus Zoho-Sales-Order-Details.

Reine Transformation, keine Netzwerkzugriffe: Aufrufer beschafft die
Auftragsdetails (inkl. line_items, shipping_address) und die Produktart je
Artikel und uebergibt sie hier fertig.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from .article_rules import (
    EINZEL_1_9,
    KOMPLETTE_VE,
    KRESSE,
    SCHNITTWARE,
    classify_article,
)

#: Zoho-Feldbezeichnungen, unter denen der Custom-Field-Name auftauchen kann -
#: je nach API-Antwort/Konto heisst das Attribut unterschiedlich.
_LABEL_KEYS = ("label", "field_name", "placeholder", "customfield_name")


@dataclass
class ArticleWarning:
    customer_id: str
    customer_name: str
    article_name: str
    quantity: float


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


def produktart_from_item(item: dict[str, Any], label: str) -> str | None:
    """Liest den Wert des Custom Fields ``label`` aus einem Zoho-Item."""
    wanted = label.strip().lower()
    for cf in item.get("custom_fields") or []:
        for key in _LABEL_KEYS:
            if (cf.get(key) or "").strip().lower() == wanted:
                return cf.get("value")
    return None


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
) -> tuple[list[Stop], list[ArticleWarning]]:
    """Gruppiert Auftraege nach Kunde und berechnet die Verpackungsmengen.

    ``get_produktart`` liefert zu einer ``item_id`` den Wert des Zoho-Custom-
    Fields "Produktart", falls vorhanden (siehe :func:`produktart_from_item`).
    """
    accumulators: dict[str, dict[str, Any]] = {}
    warnings: list[ArticleWarning] = []

    for order in orders:
        customer_id = order.get("customer_id") or ""
        customer_name = order.get("customer_name") or ""
        address = format_address(order.get("shipping_address"))

        phone = (order.get("shipping_address") or {}).get("phone") or ""
        email = _first_contact_email(order)

        acc = accumulators.get(customer_id)
        if acc is None:
            acc = {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "address": address,
                "phone": phone,
                "email": email,
                "einzel_1_9": 0.0,
                "kresse": 0.0,
                "schnittware": 0.0,
                "komplette_ve": 0.0,
                "order_numbers": [],
            }
            accumulators[customer_id] = acc
        else:
            if not acc["address"] and address:
                acc["address"] = address
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

            if classification.article_class == EINZEL_1_9:
                acc["einzel_1_9"] += quantity
            elif classification.article_class == KRESSE:
                acc["kresse"] += quantity
            elif classification.article_class == SCHNITTWARE:
                acc["schnittware"] += quantity
            elif classification.article_class == KOMPLETTE_VE:
                acc["komplette_ve"] += quantity
            else:
                warnings.append(
                    ArticleWarning(
                        customer_id=customer_id,
                        customer_name=customer_name,
                        article_name=name,
                        quantity=quantity,
                    )
                )

    stops = []
    for acc in accumulators.values():
        kisten = math.ceil(acc["einzel_1_9"] / 9) if acc["einzel_1_9"] else 0
        kisten += round(acc["komplette_ve"])
        kresse = math.ceil(acc["kresse"] / 12) if acc["kresse"] else 0
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
            )
        )
    return stops, warnings
