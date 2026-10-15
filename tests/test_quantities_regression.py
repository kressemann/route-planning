"""Regressionstests fuer die abgesicherte Mengenberechnung (Stand 15.10.2026).

Die Artikelnamen und Stammdatenwerte entsprechen den am 15.10.2026 live aus
Zoho gelesenen Artikeln (Custom Fields "Produktart", "VE Stück").
"""

import sqlite3

from routeplanning import db
from routeplanning.quantities import (
    REASON_ADRESSE_UNVOLLSTAENDIG,
    REASON_UNKLASSIFIZIERT,
    REASON_VE_GROESSE_WIDERSPRUCH,
    REASON_VE_NICHT_GANZZAHLIG,
    build_stops,
    missing_address_parts,
    ve_stueck_from_item,
)

_ADDRESS = {"address": "Hauptstraße 7", "zip": "76777", "city": "Neupotz"}


def _order(customer_id, line_items, shipping_address=_ADDRESS, number="NA-1"):
    return {
        "salesorder_number": number,
        "customer_id": customer_id,
        "customer_name": f"Kunde {customer_id}",
        "shipping_address": shipping_address,
        "line_items": line_items,
    }


def _line(item_id, name, quantity):
    return {"item_id": item_id, "name": name, "quantity": quantity}


def _no_produktart(_item_id):
    return None


def _item(**custom):
    return {"custom_fields": [{"label": k, "value": v} for k, v in custom.items()]}


# -- VE-Groesse aus dem Artikelstamm ---------------------------------------


def test_ve_stueck_parsed_from_zoho_custom_field():
    assert ve_stueck_from_item(_item(**{"VE Stück": "9 Stk."}), "VE Stück") == 9
    assert ve_stueck_from_item(_item(**{"VE Stück": "12 Stk."}), "VE Stück") == 12
    assert ve_stueck_from_item(_item(**{"VE Stück": "12"}), "VE Stück") == 12


def test_ve_stueck_unreadable_or_missing_is_none():
    assert ve_stueck_from_item(_item(**{"VE Stück": "ca. 10"}), "VE Stück") is None
    assert ve_stueck_from_item(_item(**{"VE Stück": "0 Stk."}), "VE Stück") is None
    assert ve_stueck_from_item(_item(), "VE Stück") is None


# -- Kisten ----------------------------------------------------------------


def test_different_1_9_varieties_share_crates_per_stop():
    # Event Taste 15.10.: 3 + 2 + 1 Schalen verschiedener Sorten = 1 Kiste.
    orders = [
        _order(
            "c1",
            [
                _line("a", "Blutampfer Microgreens 1/9 Schale", 3),
                _line("b", "Shiso Rot Microgreens 1/9 Schale", 2),
                _line("c", "Erbsenranken Microgreens 1/9 Schale", 1),
            ],
        )
    ]
    stops, warnings = build_stops(orders, _no_produktart, lambda _i: 9)
    assert warnings == []
    assert stops[0].kisten == 1


def test_partial_crate_counts_as_full_crate():
    orders = [_order("c1", [_line("a", "Erbsenranken Microgreens 1/9 Schale", 19)])]
    stops, _ = build_stops(orders, _no_produktart, lambda _i: 9)
    assert stops[0].kisten == 3  # ceil(19/9)


def test_komplette_ve_non_integer_is_warned_not_rounded():
    orders = [
        _order("c1", [_line("a", "Sakina Mix", 2.5), _line("b", "Afiora Steige", 1)]),
    ]
    stops, warnings = build_stops(orders, _no_produktart)
    assert stops[0].kisten == 1  # nur die ganzzahlige Steige, 2.5 nicht gerundet
    assert [(w.article_name, w.quantity, w.reason) for w in warnings] == [
        ("Sakina Mix", 2.5, REASON_VE_NICHT_GANZZAHLIG)
    ]


def test_1_9_article_with_conflicting_master_ve_is_warned_not_counted():
    orders = [
        _order(
            "c1",
            [
                _line("ok", "Erbsenranken Microgreens 1/9 Schale", 9),
                _line("bad", "Neue Sorte Microgreens 1/9 Schale", 6),
            ],
        )
    ]
    stops, warnings = build_stops(orders, _no_produktart, {"ok": 9, "bad": 6}.get)
    assert stops[0].kisten == 1
    assert len(warnings) == 1
    assert warnings[0].reason == REASON_VE_GROESSE_WIDERSPRUCH
    assert warnings[0].quantity == 6


def test_1_9_article_without_master_ve_keeps_name_rule():
    orders = [_order("c1", [_line("a", "Erbsenranken Microgreens 1/9 Schale", 10)])]
    stops, warnings = build_stops(orders, _no_produktart, lambda _i: None)
    assert warnings == []
    assert stops[0].kisten == 2


# -- Gartenkresse ----------------------------------------------------------


def test_kresse_uses_master_ve_size():
    orders = [_order("c1", [_line("k", "Gartenkresse 1/12 Schale/Pad", 10)])]
    stops, _ = build_stops(orders, _no_produktart, lambda _i: 5)
    assert stops[0].kresse == 2  # ceil(10/5) laut Artikelstamm, nicht ceil(10/12)


def test_kresse_falls_back_to_rule_default_without_master_ve():
    orders = [_order("c1", [_line("k", "Gartenkresse 1/12 Schale/Pad", 24)])]
    stops, _ = build_stops(orders, _no_produktart, lambda _i: None)
    assert stops[0].kresse == 2


def test_kresse_with_different_ve_sizes_rounded_per_size():
    orders = [
        _order(
            "c1",
            [_line("k12", "Gartenkresse 1/12 Schale/Pad", 13), _line("k6", "Gartenkresse 6er", 6)],
        )
    ]
    stops, _ = build_stops(orders, _no_produktart, {"k12": 12, "k6": 6}.get)
    assert stops[0].kresse == 2 + 1


# -- Schnittware -----------------------------------------------------------


def test_schnittware_counts_ordered_sales_units():
    # Joul's Food 15.10.: 5x "Rosa Rettich Microgreens 1000g" = 5 Einheiten.
    orders = [_order("c1", [_line("s", "Rosa Rettich Microgreens 1000g", 5)])]
    stops, _ = build_stops(orders, lambda _i: "Schnittware", lambda _i: 1)
    assert stops[0].schnittware == 5
    assert stops[0].kisten == 0


# -- Warnungen -------------------------------------------------------------


def test_unclassified_article_still_warned():
    orders = [_order("c1", [_line("x", "Lieferpauschale", 1)])]
    stops, warnings = build_stops(orders, _no_produktart)
    assert (stops[0].kisten, stops[0].schnittware, stops[0].kresse) == (0, 0, 0)
    assert [w.reason for w in warnings] == [REASON_UNKLASSIFIZIERT]


def test_missing_address_parts():
    assert missing_address_parts(_ADDRESS) == []
    # Casella Landau 15.10.: nur Name + Land, keine Strasse/PLZ/Ort.
    assert missing_address_parts({"attention": "Casella Landau", "country": "Deutschland"}) == [
        "Straße",
        "PLZ",
        "Ort",
    ]
    assert missing_address_parts(None) == ["Straße", "PLZ", "Ort"]


def test_incomplete_address_produces_one_warning_per_stop():
    casella = {"attention": "Casella Landau", "country": "Deutschland"}
    orders = [
        _order("c1", [_line("v", "Afiora Steige", 3)], shipping_address=casella),
        _order("c1", [_line("v", "Afiora Steige", 1)], shipping_address=casella, number="NA-2"),
        _order("c2", [_line("v", "Afiora Steige", 1)]),
    ]
    stops, warnings = build_stops(orders, _no_produktart)
    assert {s.customer_id: s.kisten for s in stops} == {"c1": 4, "c2": 1}
    assert len(warnings) == 1
    assert warnings[0].customer_id == "c1"
    assert warnings[0].reason == REASON_ADRESSE_UNVOLLSTAENDIG
    assert "Straße, PLZ, Ort" in warnings[0].article_name


def test_live_like_stop_deidesheimer_hof_20261015():
    # Echte Positionen NA-05759 vom 15.10.2026 (Stammdaten: 9 bzw. 12 Stk./VE).
    lines = [
        _line("kor", "Koriander Microgreens 1/9 Schale", 3),
        _line("bor", "Borretsch Microgreens 1/9 Schale", 3),
        _line("erb", "Erbsenranken Microgreens 1/9 Schale", 4),
        _line("kap", "Kapuzinerkresse Rot Microgreens 1/9 Schale/Pad", 4),
        _line("gk", "Gartenkresse 1/12 Schale/Pad", 24),
    ]
    ve = {"kor": 9, "bor": 9, "erb": 9, "kap": 9, "gk": 12}
    stops, warnings = build_stops([_order("c1", lines)], _no_produktart, ve.get)
    assert warnings == []
    assert (stops[0].kisten, stops[0].schnittware, stops[0].kresse) == (2, 0, 2)


# -- Persistenz ------------------------------------------------------------


def test_warning_reason_persisted_and_old_db_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    # Datenbank im alten Schema (route_warnings ohne reason-Spalte).
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE route_warnings (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " draft_id INTEGER NOT NULL, customer_name TEXT NOT NULL,"
        " article_name TEXT NOT NULL, quantity REAL NOT NULL);"
    )
    old.close()

    conn = db.get_connection(path)
    db.init_db(conn)
    casella = {"attention": "Casella Landau", "country": "Deutschland"}
    stops, warnings = build_stops(
        [_order("c1", [_line("v", "Afiora Steige", 3)], shipping_address=casella)],
        _no_produktart,
    )
    db.replace_stops_from_zoho(conn, "2026-10-15", stops, warnings)
    draft = db.get_draft(conn, "2026-10-15")
    assert [w["reason"] for w in draft["warnings"]] == [REASON_ADRESSE_UNVOLLSTAENDIG]
