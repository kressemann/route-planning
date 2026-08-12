"""Klassifizierung von Zoho-Auftragspositionen in Verpackungsklassen.

Die Regeln selbst stehen in ``article_rules.yaml``; dieses Modul wendet sie
nur an. Reihenfolge und Bedeutung der Prioritaetsstufen a-h sind in der
Aufgabenstellung festgelegt und in der YAML-Datei dokumentiert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULES_FILE = Path(__file__).resolve().parent / "article_rules.yaml"

EINZEL_1_9 = "EINZEL_1_9"
KRESSE = "KRESSE"
SCHNITTWARE = "SCHNITTWARE"
KOMPLETTE_VE = "KOMPLETTE_VE"
UNKLASSIFIZIERT = "UNKLASSIFIZIERT"

#: Klassen, deren Menge in die Spalte "Kisten" einfliesst.
KISTEN_KLASSEN = frozenset({EINZEL_1_9, KOMPLETTE_VE})

_GRAMM_PATTERN = re.compile(r"\d+\s*g\b", re.IGNORECASE)
_STK_PATTERN = re.compile(r"\bstk\.?", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    article_class: str
    reason: str  # Kurzbegruendung, z.B. "override", "produktart", "gramm"


def load_rules(path: Path = RULES_FILE) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def default_rules() -> dict[str, Any]:
    return load_rules()


def classify_article(
    name: str,
    produktart: str | None = None,
    rules: dict[str, Any] | None = None,
) -> Classification:
    """Ordnet einen Artikelnamen einer Verpackungsklasse zu.

    ``produktart`` ist der Wert des Zoho-Custom-Fields "Produktart" am
    Artikel-Stammsatz (Prioritaet b), sofern vorhanden.
    """
    rules = rules or default_rules()
    lname = (name or "").lower()

    # a) explizite Overrides
    for entry in rules.get("overrides", []):
        if entry["match"].lower() in lname:
            return Classification(entry["class"], "override")

    # b) Custom Field Produktart=Schnittware
    schnittware_value = rules.get("produktart_schnittware_value", "Schnittware")
    if produktart and produktart.strip().lower() == schnittware_value.lower():
        return Classification(SCHNITTWARE, "produktart")

    # c) Gartenkresse -> KRESSE
    if "gartenkresse" in lname:
        return Classification(KRESSE, "kresse")

    # d) "1/9" im Namen -> EINZEL_1_9
    if "1/9" in lname:
        return Classification(EINZEL_1_9, "1/9")

    # e) Grammangabe im Namen -> SCHNITTWARE
    if _GRAMM_PATTERN.search(lname):
        return Classification(SCHNITTWARE, "gramm")

    # f) "Stk." im Namen -> SCHNITTWARE
    if _STK_PATTERN.search(lname):
        return Classification(SCHNITTWARE, "stk")

    # g) "Mix" im Namen -> KOMPLETTE_VE (9 Stk. je VE, 1 bestellte Einheit =
    #    1 Kiste). Bewusst nach allen Schnittware-Regeln (b, e, f) sowie nach
    #    Kresse (c) und 1/9 (d) geprueft, damit diese spezifischeren Regeln
    #    Vorrang behalten, falls ein Artikelname zufaellig beides matcht (z.B.
    #    "Radieschen Mix 1/9" bleibt EINZEL_1_9, nicht KOMPLETTE_VE).
    if "mix" in lname:
        return Classification(KOMPLETTE_VE, "mix")

    # h) sonst unklassifiziert
    return Classification(UNKLASSIFIZIERT, "keine_regel_getroffen")
