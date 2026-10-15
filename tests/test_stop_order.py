"""Regressionstests fuer Sortierung, Speicherschutz, Exportreihenfolge und
Tourprofile (Stand 15.10.2026)."""

import io

import openpyxl
import pypdf
import pytest

from routeplanning import db
from routeplanning.app import create_app
from routeplanning.quantities import Stop

DAY = "2026-10-15"


def _stop(n):
    return Stop(
        customer_id=f"c{n}",
        customer_name=f"Kunde {n:02d}",
        address=f"Strasse {n}\n76829 Landau",
        kisten=n,
        schnittware=n % 3,
        kresse=n % 2,
        order_numbers=[f"NA-{n:05d}"],
    )


def _payload(draft, order):
    by_id = {s["customer_id"]: s for s in draft["stops"]}
    return {
        "stops": [
            {
                "customer_id": cid,
                "kisten": by_id[cid]["kisten"],
                "schnittware": by_id[cid]["schnittware"],
                "kresse": by_id[cid]["kresse"],
            }
            for cid in order
        ]
    }


def _move(ids, from_number, to_number):
    """Python-Spiegel von RouteOrdering.moveToNumber (Frontend)."""
    result = list(ids)
    moved = result.pop(from_number - 1)
    result.insert(to_number - 1, moved)
    return result


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=tmp_path / "t.sqlite3", zoho_client_factory=lambda: None)
    application.testing = True
    conn = db.get_connection(tmp_path / "t.sqlite3")
    db.init_db(conn)
    db.replace_stops_from_zoho(conn, DAY, [_stop(n) for n in range(1, 28)], [])
    conn.close()
    return application


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _ids(draft):
    return [s["customer_id"] for s in draft["stops"]]


# -- Verschieben und Speichern ---------------------------------------------


@pytest.mark.parametrize(
    "from_number,to_number",
    [(2, 16), (16, 2), (1, 27), (27, 1), (5, 6), (6, 5), (14, 14)],
)
def test_move_is_saved_and_quantities_unchanged(client, from_number, to_number):
    draft = client.get(f"/api/draft/{DAY}").get_json()
    totals_before = {f: sum(s[f] for s in draft["stops"]) for f in ("kisten", "schnittware", "kresse")}
    per_stop_before = {s["customer_id"]: (s["kisten"], s["schnittware"], s["kresse"]) for s in draft["stops"]}

    order = _move(_ids(draft), from_number, to_number)
    resp = client.put(f"/api/draft/{DAY}", json=_payload(draft, order))
    assert resp.status_code == 200

    saved = client.get(f"/api/draft/{DAY}").get_json()
    assert _ids(saved) == order
    assert _ids(saved)[to_number - 1] == f"c{from_number}"
    assert {s["customer_id"]: (s["kisten"], s["schnittware"], s["kresse"]) for s in saved["stops"]} == per_stop_before
    assert {f: sum(s[f] for s in saved["stops"]) for f in totals_before} == totals_before
    assert not any(s["kisten_manual"] or s["schnittware_manual"] or s["kresse_manual"] for s in saved["stops"])


def test_example_2_to_16_shifts_others_without_gaps(client):
    draft = client.get(f"/api/draft/{DAY}").get_json()
    order = _move(_ids(draft), 2, 16)
    client.put(f"/api/draft/{DAY}", json=_payload(draft, order))
    saved = _ids(client.get(f"/api/draft/{DAY}").get_json())
    assert saved[:16] == ["c1"] + [f"c{n}" for n in range(3, 17)] + ["c2"]
    assert saved[16:] == [f"c{n}" for n in range(17, 28)]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ids: ids[:-1],  # Stopp fehlt
        lambda ids: ids + [ids[0]],  # Stopp doppelt
        lambda ids: ids[:-1] + [ids[0]],  # doppelt statt fehlend (gleiche Laenge)
        lambda ids: ids + ["unbekannt"],  # fremder Stopp
    ],
    ids=["fehlend", "doppelt", "doppelt-gleiche-laenge", "fremd"],
)
def test_inconsistent_order_is_rejected_and_nothing_saved(client, mutate):
    draft = client.get(f"/api/draft/{DAY}").get_json()
    original = _ids(draft)
    bad = mutate(list(reversed(original)))
    payload = {
        "stops": [{"customer_id": cid, "kisten": 1, "schnittware": 0, "kresse": 0} for cid in bad]
    }
    resp = client.put(f"/api/draft/{DAY}", json=payload)
    assert resp.status_code == 400
    after = client.get(f"/api/draft/{DAY}").get_json()
    assert _ids(after) == original
    assert not any(s["kisten_manual"] for s in after["stops"])


# -- Exportreihenfolge -----------------------------------------------------


def test_xlsx_and_pdf_follow_saved_order(client):
    draft = client.get(f"/api/draft/{DAY}").get_json()
    order = _move(_move(_ids(draft), 2, 16), 27, 1)
    client.put(f"/api/draft/{DAY}", json=_payload(draft, order))
    names = [f"Kunde {int(cid[1:]):02d}" for cid in order]

    xlsx = client.get(f"/api/draft/{DAY}/xlsx")
    assert xlsx.status_code == 200
    rows = list(openpyxl.load_workbook(io.BytesIO(xlsx.data)).active.iter_rows(values_only=True))
    header = rows[0]
    contact_col = header.index("Contact")
    nr_col = header.index("Nr")
    data = [r for r in rows[1:] if r[contact_col]]
    assert [r[contact_col] for r in data] == names
    assert [r[nr_col] for r in data] == list(range(1, 28))

    pdf = client.get(f"/api/draft/{DAY}/pdf")
    assert pdf.status_code == 200
    text = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf.data)).pages)
    positions = [text.index(name) for name in names]
    assert positions == sorted(positions)


# -- Tourprofile -----------------------------------------------------------


def test_merge_profile_order_keeps_absent_customers_in_place():
    previous = ["a", "b", "c", "d", "e"]
    # Heute fehlen a und c, d wurde vor b gezogen, x ist neu.
    assert db.merge_profile_order(previous, ["d", "b", "e", "x"]) == ["a", "d", "b", "c", "e", "x"]
    assert db.merge_profile_order([], ["b", "a"]) == ["b", "a"]
    assert db.merge_profile_order(["a", "b"], ["a", "b"]) == ["a", "b"]


def test_profile_apply_with_missing_and_new_customers(tmp_path):
    path = tmp_path / "p.sqlite3"
    application = create_app(db_path=path, zoho_client_factory=lambda: None)
    conn = db.get_connection(path)
    db.init_db(conn)
    # Montag: c1..c5 in Profilreihenfolge c5, c3, c1, c4, c2.
    db.replace_stops_from_zoho(conn, "2026-10-12", [_stop(n) for n in (1, 2, 3, 4, 5)], [])
    db.save_stop_order_and_quantities(
        conn,
        "2026-10-12",
        [{"customer_id": f"c{n}", "kisten": n, "schnittware": n % 3, "kresse": n % 2} for n in (5, 3, 1, 4, 2)],
    )
    # Folgetag: c3 und c4 ohne Bestellung, c8 und c9 neu.
    db.replace_stops_from_zoho(conn, "2026-10-19", [_stop(n) for n in (9, 1, 2, 5, 8)], [])
    conn.close()

    with application.test_client() as client:
        assert client.post("/api/profiles", json={"name": "Montag", "date": "2026-10-12"}).status_code == 200
        draft = client.post("/api/profiles/Montag/apply", json={"date": "2026-10-19"}).get_json()

    assert _ids(draft) == ["c5", "c1", "c2", "c9", "c8"]
    flags = {s["customer_id"]: s["not_in_profile"] for s in draft["stops"]}
    assert flags == {"c5": False, "c1": False, "c2": False, "c9": True, "c8": True}
    assert sorted(s["kisten"] for s in draft["stops"]) == [1, 2, 5, 8, 9]


def test_tour_reload_does_not_change_profile(tmp_path):
    path = tmp_path / "p.sqlite3"
    conn = db.get_connection(path)
    db.init_db(conn)
    db.replace_stops_from_zoho(conn, DAY, [_stop(n) for n in (1, 2, 3)], [])
    db.save_profile(conn, "R", ["c3", "c1", "c2"])
    db.apply_profile_order(conn, DAY, "R")
    # Gewoehnlicher Reload mit fehlendem und neuem Kunden.
    db.replace_stops_from_zoho(conn, DAY, [_stop(n) for n in (1, 3, 7)], [])
    assert db.get_profile(conn, "R")["customer_ids"] == ["c3", "c1", "c2"]
    assert _ids(db.get_draft(conn, DAY)) == ["c3", "c1", "c7"]


def test_resaving_profile_on_day_with_missing_customers_keeps_them(tmp_path):
    path = tmp_path / "p.sqlite3"
    application = create_app(db_path=path, zoho_client_factory=lambda: None)
    conn = db.get_connection(path)
    db.init_db(conn)
    db.save_profile(conn, "R", ["c1", "c2", "c3", "c4"])
    # Heute nur c4, c2 und neu c9 - Nutzer speichert das Profil erneut.
    db.replace_stops_from_zoho(conn, DAY, [_stop(n) for n in (4, 2, 9)], [])
    conn.close()
    with application.test_client() as client:
        resp = client.post("/api/profiles", json={"name": "R", "date": DAY})
    assert resp.get_json()["customer_ids"] == ["c1", "c4", "c2", "c3", "c9"]
