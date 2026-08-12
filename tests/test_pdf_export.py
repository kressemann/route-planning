"""Tests fuer die Datenaufbereitung des PDF-Exports.

Reine Formatierungslogik - Reihenfolge/Ausschluss/manuelle Mengen kommen
unveraendert aus ``stops`` und werden hier nicht erneut getestet (siehe
test_app.py::test_pdf_download_reflects_manual_values).
"""

import io

from pypdf import PdfReader

from routeplanning.pdf_export import COLUMNS, FONT_SIZE, HEADER_FONT_SIZE, build_pdf, build_pdf_table_data


def _stop(customer_id, name, address="Musterstr. 1\n12345 Musterstadt", kisten=1, schnittware=0, kresse=0):
    return {
        "customer_id": customer_id,
        "customer_name": name,
        "address": address,
        "kisten": kisten,
        "schnittware": schnittware,
        "kresse": kresse,
    }


STOPS = [
    _stop("c1", "Kunde A", kisten=3, schnittware=2.5, kresse=0),
    _stop("c2", "Kunde B", kisten=0, schnittware=0, kresse=4),
]


# -- Tabellendaten -------------------------------------------------------


def test_header_row_matches_columns():
    rows = build_pdf_table_data(STOPS)
    assert rows[0] == COLUMNS


def test_row_order_matches_stop_order():
    rows = build_pdf_table_data(STOPS)
    assert rows[1][2] == "Kunde A"
    assert rows[2][2] == "Kunde B"


def test_nr_column_is_sequential_starting_at_one():
    rows = build_pdf_table_data(STOPS)
    assert rows[1][0] == "1"
    assert rows[2][0] == "2"


def test_zero_quantities_rendered_as_empty_string():
    rows = build_pdf_table_data(STOPS)
    # Zeile 1 = c1: kisten=3, schnittware=2.5, kresse=0 -> nur kresse leer
    assert rows[1][3] == "3"
    assert rows[1][4] == "2.5"
    assert rows[1][5] == ""

    # Zeile 2 = c2: kisten=0, schnittware=0, kresse=4 -> kisten/schnittware leer
    assert rows[2][3] == ""
    assert rows[2][4] == ""
    assert rows[2][5] == "4"


def test_positive_quantities_shown_normally():
    rows = build_pdf_table_data([_stop("c1", "Kunde A", kisten=7, schnittware=1.5, kresse=2)])
    assert rows[1][3] == "7"
    assert rows[1][4] == "1.5"
    assert rows[1][5] == "2"


def test_integer_valued_float_rendered_without_decimal():
    rows = build_pdf_table_data([_stop("c1", "Kunde A", kisten=1, schnittware=3.0, kresse=0)])
    assert rows[1][4] == "3"


def test_no_stops_still_produces_header_only_rows():
    rows = build_pdf_table_data([])
    assert rows == [COLUMNS]


# -- Schriftgroesse: Daten moeglichst bei 14pt belassen ----------------------


def test_data_font_size_kept_at_14pt():
    assert FONT_SIZE == 14


def test_header_font_size_reasonably_readable():
    assert 12 <= HEADER_FONT_SIZE <= 16


# -- Spaltenbreiten (Hochformat) ----------------------------------------


def test_column_widths_fit_within_printable_a4_portrait_width():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    from routeplanning.pdf_export import _COLUMN_WIDTHS_MM, _MARGIN

    usable_width = A4[0] - 2 * _MARGIN
    total_columns_width = sum(_COLUMN_WIDTHS_MM[name] for name in COLUMNS) * mm
    assert total_columns_width <= usable_width


def test_address_is_widest_column_contact_second():
    from routeplanning.pdf_export import _COLUMN_WIDTHS_MM

    widths = _COLUMN_WIDTHS_MM
    assert widths["Address"] > widths["Contact"]
    for narrow in ("Nr", "Kisten", "Schnittware", "Kresse"):
        assert widths["Contact"] > widths[narrow]


def test_margins_within_requested_10_to_12mm():
    from reportlab.lib.units import mm

    from routeplanning.pdf_export import _MARGIN

    assert 10 * mm <= _MARGIN <= 12 * mm


# -- Gerendertes PDF -----------------------------------------------------


def test_build_pdf_produces_valid_single_page_pdf_for_small_tour():
    data = build_pdf(STOPS, "2026-08-20")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1


def test_build_pdf_is_portrait_a4():
    data = build_pdf(STOPS, "2026-08-20")
    reader = PdfReader(io.BytesIO(data))
    page = reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    assert height > width  # Hochformat
    # A4 = 595 x 842 pt (72 dpi) - mit Toleranz fuer Rundung
    assert abs(width - 595) < 2
    assert abs(height - 842) < 2


def test_build_pdf_header_and_date_present():
    data = build_pdf(STOPS, "2026-08-20")
    reader = PdfReader(io.BytesIO(data))
    text = reader.pages[0].extract_text()
    assert "Lieferliste" in text
    assert "20.08.2026" in text


def test_build_pdf_no_stops_still_valid():
    data = build_pdf([], "2026-08-20")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    # "Schnittware" darf im Kopf zweizeilig ("Schnitt"/"ware") stehen.
    for column in COLUMNS:
        if column == "Schnittware":
            assert "Schnitt" in text and "ware" in text
        else:
            assert column in text


def test_build_pdf_many_stops_spans_multiple_pages():
    many_stops = [_stop(f"c{i}", f"Kunde {i}", kisten=i % 5) for i in range(80)]
    data = build_pdf(many_stops, "2026-08-20")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) > 1
    # Kopfzeile wiederholt sich auf jeder Seite
    for page in reader.pages:
        text = page.extract_text()
        assert "Address" in text
        assert "Contact" in text
