import pytest

from routeplanning import db
from routeplanning.quantities import Stop


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "test.sqlite3")
    db.init_db(connection)
    yield connection
    connection.close()


def _stop(customer_id, name, kisten=3, schnittware=0, kresse=0):
    return Stop(
        customer_id=customer_id,
        customer_name=name,
        address=f"Adresse {name}",
        kisten=kisten,
        schnittware=schnittware,
        kresse=kresse,
        order_numbers=[],
    )


def test_manual_override_is_stored_and_flagged(conn):
    stops = [_stop("c1", "Kunde A", kisten=3)]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    # Nutzer aendert Kisten von 3 auf 4, weil noch ein Geschenk mitkommt.
    db.save_stop_order_and_quantities(
        conn, "2026-08-20", [{"customer_id": "c1", "kisten": 4, "schnittware": 0, "kresse": 0}]
    )

    draft = db.get_draft(conn, "2026-08-20")
    stop = draft["stops"][0]
    assert stop["kisten"] == 4
    assert stop["kisten_auto"] == 3
    assert stop["kisten_manual"] is True


def test_setting_value_back_to_auto_clears_override(conn):
    stops = [_stop("c1", "Kunde A", kisten=3)]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    db.save_stop_order_and_quantities(
        conn, "2026-08-20", [{"customer_id": "c1", "kisten": 4, "schnittware": 0, "kresse": 0}]
    )
    db.save_stop_order_and_quantities(
        conn, "2026-08-20", [{"customer_id": "c1", "kisten": 3, "schnittware": 0, "kresse": 0}]
    )

    stop = db.get_draft(conn, "2026-08-20")["stops"][0]
    assert stop["kisten"] == 3
    assert stop["kisten_manual"] is False


def test_manual_override_does_not_touch_article_rules_or_zoho_data(conn):
    # Die manuelle Aenderung wirkt nur auf den Tourentwurf: der automatisch
    # berechnete Wert (kisten_auto) bleibt unveraendert erhalten.
    stops = [_stop("c1", "Kunde A", kisten=3)]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    db.save_stop_order_and_quantities(
        conn, "2026-08-20", [{"customer_id": "c1", "kisten": 4, "schnittware": 0, "kresse": 0}]
    )
    stop = db.get_draft(conn, "2026-08-20")["stops"][0]
    assert stop["kisten_auto"] == 3


def test_reload_preserves_manual_override_for_existing_customer(conn):
    stops = [_stop("c1", "Kunde A", kisten=3)]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    db.save_stop_order_and_quantities(
        conn, "2026-08-20", [{"customer_id": "c1", "kisten": 4, "schnittware": 0, "kresse": 0}]
    )

    # Erneutes "Tour laden": neuer Zoho-Abruf liefert denselben Kunden mit
    # unveraendertem Auto-Wert - die manuelle Ueberschreibung bleibt erhalten.
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    stop = db.get_draft(conn, "2026-08-20")["stops"][0]
    assert stop["kisten"] == 4
    assert stop["kisten_manual"] is True


def test_reload_drops_stops_for_customers_no_longer_present(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    db.replace_stops_from_zoho(conn, "2026-08-20", [_stop("c1", "Kunde A")], [])

    ids = [s["customer_id"] for s in db.get_draft(conn, "2026-08-20")["stops"]]
    assert ids == ["c1"]


def test_reload_appends_new_customers_after_existing_order(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    db.replace_stops_from_zoho(
        conn, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")], []
    )

    ids = [s["customer_id"] for s in db.get_draft(conn, "2026-08-20")["stops"]]
    assert ids == ["c1", "c2", "c3"]


def test_manual_override_of_zero_is_distinguishable_from_auto(conn):
    # Nutzer setzt Kisten explizit auf 0 (z.B. weniger geliefert als bestellt) -
    # das muss sich von "kein Override" (= automatisch berechneter Wert)
    # unterscheiden lassen, sonst geht die manuelle Aenderung beim naechsten
    # "Tour laden" verloren.
    stops = [_stop("c1", "Kunde A", kisten=3)]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    db.save_stop_order_and_quantities(
        conn, "2026-08-20", [{"customer_id": "c1", "kisten": 0, "schnittware": 0, "kresse": 0}]
    )
    stop = db.get_draft(conn, "2026-08-20")["stops"][0]
    assert stop["kisten"] == 0
    assert stop["kisten_auto"] == 3
    assert stop["kisten_manual"] is True


def test_delete_stop_removes_only_that_stop(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    deleted = db.delete_stop(conn, "2026-08-20", "c1")

    assert deleted is True
    ids = [s["customer_id"] for s in db.get_draft(conn, "2026-08-20")["stops"]]
    assert ids == ["c2"]


def test_delete_stop_unknown_customer_returns_false(conn):
    stops = [_stop("c1", "Kunde A")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    assert db.delete_stop(conn, "2026-08-20", "does-not-exist") is False
    ids = [s["customer_id"] for s in db.get_draft(conn, "2026-08-20")["stops"]]
    assert ids == ["c1"]


def test_delete_stop_unknown_draft_returns_false(conn):
    assert db.delete_stop(conn, "2026-09-01", "c1") is False


def test_save_updates_position(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])

    db.save_stop_order_and_quantities(
        conn,
        "2026-08-20",
        [
            {"customer_id": "c2", "kisten": 3, "schnittware": 0, "kresse": 0},
            {"customer_id": "c1", "kisten": 3, "schnittware": 0, "kresse": 0},
        ],
    )

    ids = [s["customer_id"] for s in db.get_draft(conn, "2026-08-20")["stops"]]
    assert ids == ["c2", "c1"]
