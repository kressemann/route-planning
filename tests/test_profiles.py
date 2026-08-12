import pytest

from routeplanning import db
from routeplanning.quantities import Stop


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "test.sqlite3")
    db.init_db(connection)
    yield connection
    connection.close()


def _stop(customer_id, name, kisten=1, schnittware=0, kresse=0):
    return Stop(
        customer_id=customer_id,
        customer_name=name,
        address=f"Adresse {name}",
        kisten=kisten,
        schnittware=schnittware,
        kresse=kresse,
        order_numbers=[f"NA-{customer_id}"],
    )


def test_replace_stops_persists_order(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    draft = db.get_draft(conn, "2026-08-20")
    assert [s["customer_id"] for s in draft["stops"]] == ["c1", "c2", "c3"]
    assert draft["status"] == "entwurf"


def test_save_and_get_profile(conn):
    db.save_profile(conn, "Montagsroute", ["c2", "c1", "c3"])
    profile = db.get_profile(conn, "Montagsroute")
    assert profile["customer_ids"] == ["c2", "c1", "c3"]
    assert db.list_profiles(conn) == ["Montagsroute"]


def test_save_profile_overwrites_existing(conn):
    db.save_profile(conn, "Route", ["c1"])
    db.save_profile(conn, "Route", ["c2", "c3"])
    assert db.get_profile(conn, "Route")["customer_ids"] == ["c2", "c3"]
    assert db.list_profiles(conn) == ["Route"]


def test_apply_profile_orders_known_customers_first(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    db.save_profile(conn, "R1", ["c3", "c1", "c2"])

    draft = db.apply_profile_order(conn, "2026-08-20", "R1")
    assert [s["customer_id"] for s in draft["stops"]] == ["c3", "c1", "c2"]
    assert all(not s["not_in_profile"] for s in draft["stops"])


def test_apply_profile_ignores_customers_without_order_today(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    # c99 hat heute keinen Auftrag - muss ignoriert werden, kein Fehler.
    db.save_profile(conn, "R1", ["c99", "c2", "c1"])

    draft = db.apply_profile_order(conn, "2026-08-20", "R1")
    assert [s["customer_id"] for s in draft["stops"]] == ["c2", "c1"]


def test_apply_profile_appends_and_flags_new_customers(conn):
    stops = [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    # Profil kennt nur c1 - c2 und c3 sind neu und sollen ans Ende, markiert.
    db.save_profile(conn, "R1", ["c1"])

    draft = db.apply_profile_order(conn, "2026-08-20", "R1")
    ids = [s["customer_id"] for s in draft["stops"]]
    assert ids[0] == "c1"
    assert set(ids[1:]) == {"c2", "c3"}
    flags = {s["customer_id"]: s["not_in_profile"] for s in draft["stops"]}
    assert flags["c1"] is False
    assert flags["c2"] is True
    assert flags["c3"] is True


def test_apply_profile_unknown_profile_raises(conn):
    stops = [_stop("c1", "Kunde A")]
    db.replace_stops_from_zoho(conn, "2026-08-20", stops, [])
    with pytest.raises(ValueError):
        db.apply_profile_order(conn, "2026-08-20", "does-not-exist")


def test_apply_profile_without_draft_returns_none(conn):
    db.save_profile(conn, "R1", ["c1"])
    assert db.apply_profile_order(conn, "2026-09-01", "R1") is None
