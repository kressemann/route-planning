"""Tests fuer die Druckformatierung des XLSX-Exports.

Reine Formatierungslogik - Reihenfolge/Ausschluss/manuelle Mengen kommen
unveraendert aus ``stops`` und werden hier nicht erneut getestet (siehe
test_app.py::test_xlsx_download_reflects_manual_values).
"""

from routeplanning.xlsx_export import COLUMNS, FONT_SIZE, build_workbook


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


def _ws(stops=STOPS):
    return build_workbook(stops).active


# -- Schriftgroesse ----------------------------------------------------------


def test_header_font_size_and_bold():
    ws = _ws()
    for cell in ws[1]:
        assert cell.font.size == FONT_SIZE == 16
        assert cell.font.bold is True


def test_data_rows_font_size():
    ws = _ws()
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            assert cell.font.size == 16


# -- Rahmen --------------------------------------------------------------


def test_all_used_cells_have_thin_border():
    ws = _ws()
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=len(COLUMNS)):
        for cell in row:
            for side in (cell.border.left, cell.border.right, cell.border.top, cell.border.bottom):
                assert side.style == "thin"


def test_header_has_fill_to_stand_out():
    ws = _ws()
    for cell in ws[1]:
        assert cell.fill.fgColor.rgb not in (None, "00000000")


# -- Nullwerte -------------------------------------------------------------


def test_zero_quantities_exported_as_blank_cells():
    ws = _ws()
    # Zeile 2 = c1: kisten=3, schnittware=2.5, kresse=0 -> nur kresse leer
    row2 = ws[2]
    assert row2[3].value == 3
    assert row2[4].value == 2.5
    assert row2[5].value is None

    # Zeile 3 = c2: kisten=0, schnittware=0, kresse=4 -> kisten/schnittware leer
    row3 = ws[3]
    assert row3[3].value is None
    assert row3[4].value is None
    assert row3[5].value == 4


def test_positive_quantities_shown_normally():
    ws = _ws([_stop("c1", "Kunde A", kisten=7, schnittware=1.5, kresse=2)])
    row = ws[2]
    assert row[3].value == 7
    assert row[4].value == 1.5
    assert row[5].value == 2


def test_no_stops_still_produces_valid_header_only_sheet():
    ws = _ws([])
    assert ws.max_row == 1
    assert [c.value for c in ws[1]] == COLUMNS


# -- Ausrichtung -------------------------------------------------------------


def test_qty_and_nr_columns_centered():
    ws = _ws()
    row = ws[2]
    for col_index in (0, 3, 4, 5):  # Nr, Kisten, Schnittware, Kresse
        cell = row[col_index]
        assert cell.alignment.horizontal == "center"
        assert cell.alignment.vertical == "center"


def test_address_and_contact_left_aligned_with_wrap():
    ws = _ws()
    row = ws[2]
    for col_index in (1, 2):  # Address, Contact
        cell = row[col_index]
        assert cell.alignment.horizontal == "left"
        assert cell.alignment.vertical == "center"
        assert cell.alignment.wrap_text is True


# -- Spaltenbreiten ----------------------------------------------------------


def test_address_and_contact_wider_than_qty_columns():
    ws = _ws()
    widths = {name: ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width
              for i, name in enumerate(COLUMNS)}
    assert widths["Address"] > widths["Kisten"]
    assert widths["Address"] > widths["Schnittware"]
    assert widths["Address"] > widths["Kresse"]
    assert widths["Contact"] > widths["Kisten"]
    assert widths["Contact"] > widths["Schnittware"]
    assert widths["Contact"] > widths["Kresse"]


# -- Drucken -----------------------------------------------------------------


def test_print_area_covers_used_range_only():
    ws = _ws()
    assert ws.print_area == "'Tour'!$A$1:$F$3"


def test_print_area_with_no_stops_is_header_only():
    ws = _ws([])
    assert ws.print_area == "'Tour'!$A$1:$F$1"


def test_header_row_repeats_on_each_print_page():
    ws = _ws()
    assert ws.print_title_rows == "$1:$1"


def test_landscape_orientation():
    ws = _ws()
    assert ws.page_setup.orientation == "landscape"


def test_fit_to_one_page_wide_unlimited_height():
    ws = _ws()
    assert ws.sheet_properties.pageSetUpPr.fitToPage is True
    assert ws.page_setup.fitToWidth == 1
    assert ws.page_setup.fitToHeight == 0


def test_page_margins_are_set_reasonably():
    ws = _ws()
    margins = ws.page_margins
    assert 0 < margins.left <= 1
    assert 0 < margins.right <= 1
    assert 0 < margins.top <= 1.5
    assert 0 < margins.bottom <= 1.5
