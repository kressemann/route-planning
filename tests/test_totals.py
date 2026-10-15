"""Regressionstests fuer die Gesamtmengenuebersicht (Weboberflaeche + PDF)."""

import io
import re

import pypdf
import pytest

from routeplanning import db
from routeplanning.app import create_app
from routeplanning.pdf_export import build_pdf, format_summary, summarize_stops
from routeplanning.quantities import Stop

DAY = "2026-10-15"


def _stop_dict(kisten, schnittware, kresse, name="Kunde"):
    return {"customer_name": name, "address": "Strasse 1\n76829 Landau", "kisten": kisten,
            "schnittware": schnittware, "kresse": kresse}


def _tour_20261015():
    """27 Stopps mit den am 15.10.2026 bestaetigten Summen 66 / 19 / 2."""
    stops = [_stop_dict(0, 0, 0, f"Kunde {n:02d}") for n in range(1, 28)]
    stops[0]["kisten"], stops[1]["kisten"] = 40, 26
    stops[2]["schnittware"], stops[3]["schnittware"] = 14, 5
    stops[4]["kresse"] = 2
    return stops


def _pdf_text(data, page=None):
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = reader.pages if page is None else [reader.pages[page]]
    return "\n".join(p.extract_text() for p in pages)


# -- Berechnung und Format ---------------------------------------------------


def test_summary_matches_confirmed_values_20261015():
    totals = summarize_stops(_tour_20261015())
    assert totals == {"stops": 27, "kisten": 66, "schnittware": 19, "kresse": 2}
    assert format_summary(totals) == "27 Stopps | 66 Kisten | 19 Schnittware | 2 Kresse"


def test_summary_singular_empty_and_decimal():
    assert format_summary(summarize_stops([])) == "0 Stopps | 0 Kisten | 0 Schnittware | 0 Kresse"
    assert format_summary(summarize_stops([_stop_dict(1, 1, 1)])) == "1 Stopp | 1 Kiste | 1 Schnittware | 1 Kresse"
    assert format_summary(summarize_stops([_stop_dict(2, 2.5, 0)])) == "1 Stopp | 2 Kisten | 2,5 Schnittware | 0 Kresse"


def test_summary_treats_missing_values_as_zero():
    totals = summarize_stops([{"kisten": None, "schnittware": None}, {"kresse": 3}])
    assert totals == {"stops": 2, "kisten": 0, "schnittware": 0, "kresse": 3}


# -- PDF ------------------------------------------------------------------------


def test_pdf_page_one_shows_summary_below_title_and_date():
    text = _pdf_text(build_pdf(_tour_20261015(), DAY), page=0)
    summary = "27 Stopps | 66 Kisten | 19 Schnittware | 2 Kresse"
    assert summary in text
    assert text.index("Lieferliste") < text.index("Lieferdatum: 15.10.2026") < text.index(summary)
    # Direkt unter dem Datum, vor der Tabelle (Kopfzeile "Nr").
    assert text.index(summary) < text.index("Nr")


def test_pdf_summary_only_on_first_page():
    stops = [_stop_dict(1, 0, 0, f"Kunde {n:02d}") for n in range(1, 61)]
    data = build_pdf(stops, DAY)
    assert len(pypdf.PdfReader(io.BytesIO(data)).pages) > 1
    assert "60 Stopps | 60 Kisten" in _pdf_text(data, page=0)
    assert "60 Stopps" not in _pdf_text(data, page=1)


def test_pdf_table_layout_unchanged():
    # Tabelle, Reihenfolge und Mengenzellen bleiben wie bisher.
    text = _pdf_text(build_pdf(_tour_20261015(), DAY))
    positions = [text.index(f"Kunde {n:02d}") for n in range(1, 28)]
    assert positions == sorted(positions)
    for header in ("Address", "Contact", "Kisten", "Kresse"):
        assert header in text


# -- Ueber die App: Summe folgt dem aktuellen Tourentwurf ------------------------


def _stop(n, kisten, schnittware=0, kresse=0):
    return Stop(customer_id=f"c{n}", customer_name=f"Kunde {n:02d}", address=f"Strasse {n}\n76829 Landau",
                kisten=kisten, schnittware=schnittware, kresse=kresse, order_numbers=[])


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite3", zoho_client_factory=lambda: None)
    app.testing = True
    conn = db.get_connection(tmp_path / "t.sqlite3")
    db.init_db(conn)
    db.replace_stops_from_zoho(conn, DAY, [_stop(1, 3, 1), _stop(2, 5, 0, 1), _stop(3, 2, 2)], [])
    conn.close()
    with app.test_client() as c:
        yield c


def _pdf_summary(client):
    return _pdf_text(client.get(f"/api/draft/{DAY}/pdf").data, page=0)


def test_pdf_summary_reflects_manual_quantities_deleted_stops_and_not_order(client):
    assert "3 Stopps | 10 Kisten | 3 Schnittware | 1 Kresse" in _pdf_summary(client)

    # Manuelle Menge + umgekehrte Reihenfolge.
    client.put(f"/api/draft/{DAY}", json={"stops": [
        {"customer_id": "c3", "kisten": 2, "schnittware": 2, "kresse": 0},
        {"customer_id": "c2", "kisten": 9, "schnittware": 0, "kresse": 1},
        {"customer_id": "c1", "kisten": 3, "schnittware": 1, "kresse": 0},
    ]})
    assert "3 Stopps | 14 Kisten | 3 Schnittware | 1 Kresse" in _pdf_summary(client)

    # Stopp entfernen.
    client.delete(f"/api/draft/{DAY}/stops/c2")
    assert "2 Stopps | 5 Kisten | 3 Schnittware | 0 Kresse" in _pdf_summary(client)


def test_web_page_contains_totals_section_above_profiles(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="tour-totals"' in html
    assert html.index('id="tour-totals"') < html.index('aria-label="Routenprofile"')
    assert "totals.js" in html
    # Keine fest hinterlegten Summenwerte im ausgelieferten HTML/JS.
    assert "66 Kisten" not in html
    js = client.get("/static/app.js").get_data(as_text=True) + client.get("/static/totals.js").get_data(as_text=True)
    assert not re.search(r"\b(27|66|19|2)\s+(Stopps|Kisten|Schnittware|Kresse)\b", html + js)
