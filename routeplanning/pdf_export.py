"""PDF-Export der aktuell sichtbaren, finalen Stoppreihenfolge - druckfertige
Lieferliste fuer Mitarbeiter.

Reine Formatierung fuer den Ausdruck (Schriftgroesse, Rahmen, Seiteneinrichtung).
Reihenfolge, geloeschte Stopps und manuelle Mengenueberschreibungen kommen
unveraendert aus dem Tourentwurf (``stops``) - dieses Modul aendert daran
nichts, es formatiert nur die Ausgabe. Keine Zoho-/GSMtasks-Zugriffe.
"""

from __future__ import annotations

import datetime as _dt
import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

COLUMNS = ["Nr", "Address", "Contact", "Kisten", "Schnittware", "Kresse"]

FONT_SIZE = 14
HEADER_FONT_SIZE = 14

#: Spaltenbreiten in mm - Adresse groesste, Contact zweitgroesste Spalte,
#: Nr/Mengenspalten schmal. Summe = nutzbare Breite bei A4 hoch mit 11 mm
#: Rand links/rechts (188 mm). "Schnittware" darf im Kopf zweizeilig stehen
#: (siehe _HEADER_LABELS), daher bleibt die Spalte trotz langem Namen schmal.
_COLUMN_WIDTHS_MM = {
    "Nr": 11,
    "Address": 65,
    "Contact": 46,
    "Kisten": 21,
    "Schnittware": 23,
    "Kresse": 22,
}

#: Kopfzeilentext je Spalte - weicht nur bei "Schnittware" vom Spaltennamen ab
#: (bewusster Zeilenumbruch an der Wortfuge, damit die Spalte schmal bleiben kann).
_HEADER_LABELS = {"Schnittware": "Schnitt<br/>ware"}

#: Spalten, deren Mengenwert bei 0 als leere Zelle statt "0" ausgegeben wird.
_QTY_COLUMNS = {"Kisten", "Schnittware", "Kresse"}

#: Spalten mit linksbuendigem Text (Rest wird zentriert).
_LEFT_ALIGNED_COLUMNS = {"Address", "Contact"}

#: 11 mm Rand - Mitte der geforderten 10-12 mm.
_MARGIN = 11 * mm

_TITLE_STYLE = ParagraphStyle(
    "title",
    fontName="Helvetica-Bold",
    fontSize=14,
    leading=17,
)
_SUBTITLE_STYLE = ParagraphStyle(
    "subtitle",
    fontName="Helvetica",
    fontSize=11,
    leading=14,
)
_SUMMARY_STYLE = ParagraphStyle(
    "summary",
    fontName="Helvetica-Bold",
    fontSize=12,
    leading=15,
    spaceBefore=1 * mm,
)
_HEADER_CELL_STYLE = ParagraphStyle(
    "header-cell",
    fontName="Helvetica-Bold",
    fontSize=HEADER_FONT_SIZE,
    leading=HEADER_FONT_SIZE + 4,
    alignment=TA_CENTER,
)
_DATA_CELL_STYLE_LEFT = ParagraphStyle(
    "data-cell-left",
    fontName="Helvetica",
    fontSize=FONT_SIZE,
    leading=FONT_SIZE + 4,
    alignment=TA_LEFT,
)
_DATA_CELL_STYLE_CENTER = ParagraphStyle(
    "data-cell-center",
    fontName="Helvetica",
    fontSize=FONT_SIZE,
    leading=FONT_SIZE + 4,
    alignment=TA_CENTER,
)


def _blank_if_zero(value: Any) -> str:
    """Leerer Text statt '0' fuer Kisten/Schnittware/Kresse - lesbarer Ausdruck."""
    if value in (0, 0.0, None):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def summarize_stops(stops: list[dict[str, Any]]) -> dict[str, float]:
    """Gesamtsummen ueber die exportierten Stopps (inkl. manueller Mengen).
    Gegenstueck im Browser: static/totals.js (RouteTotals.summarize)."""
    return {
        "stops": len(stops),
        "kisten": sum(float(s.get("kisten") or 0) for s in stops),
        "schnittware": sum(float(s.get("schnittware") or 0) for s in stops),
        "kresse": sum(float(s.get("kresse") or 0) for s in stops),
    }


def _format_number(value: float) -> str:
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:g}".replace(".", ",")


def format_summary(totals: dict[str, float]) -> str:
    """"27 Stopps | 66 Kisten | 19 Schnittware | 2 Kresse" - identisch zur
    Weboberflaeche (RouteTotals.formatSummary)."""
    return " | ".join(
        [
            f"{_format_number(totals['stops'])} {'Stopp' if totals['stops'] == 1 else 'Stopps'}",
            f"{_format_number(totals['kisten'])} {'Kiste' if totals['kisten'] == 1 else 'Kisten'}",
            f"{_format_number(totals['schnittware'])} Schnittware",
            f"{_format_number(totals['kresse'])} Kresse",
        ]
    )


def _format_delivery_date(delivery_date: str) -> str:
    return _dt.date.fromisoformat(delivery_date).strftime("%d.%m.%Y")


def build_pdf_table_data(stops: list[dict[str, Any]]) -> list[list[str]]:
    """Reine Datenzeilen (Strings) fuer die Tabelle - Header + eine Zeile pro Stopp.

    Adressen mit eingebetteten Zeilenumbruechen bleiben als "\n" erhalten
    (wird beim Rendern in ``build_pdf`` in Paragraph-Umbrueche uebersetzt).
    """
    rows = [list(COLUMNS)]
    for index, stop in enumerate(stops, start=1):
        rows.append(
            [
                str(index),
                stop.get("address", "") or "",
                stop.get("customer_name", "") or "",
                _blank_if_zero(stop.get("kisten", 0)),
                _blank_if_zero(stop.get("schnittware", 0)),
                _blank_if_zero(stop.get("kresse", 0)),
            ]
        )
    return rows


def _cell_style(column_name: str, is_header: bool) -> ParagraphStyle:
    if is_header:
        return _HEADER_CELL_STYLE
    if column_name in _LEFT_ALIGNED_COLUMNS:
        return _DATA_CELL_STYLE_LEFT
    return _DATA_CELL_STYLE_CENTER


def _as_paragraphs(rows: list[list[str]]) -> list[list[Paragraph]]:
    out = []
    for row_index, row in enumerate(rows):
        is_header = row_index == 0
        styled_row = []
        for col_index, value in enumerate(row):
            column_name = COLUMNS[col_index]
            if is_header:
                text = _HEADER_LABELS.get(value, value)
            else:
                text = str(value).replace("\n", "<br/>")
            styled_row.append(Paragraph(text, _cell_style(column_name, is_header)))
        out.append(styled_row)
    return out


def _build_table(rows: list[list[str]]) -> Table:
    col_widths = [_COLUMN_WIDTHS_MM[name] * mm for name in COLUMNS]
    table = Table(_as_paragraphs(rows), colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9D9D9")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def build_pdf(stops: list[dict[str, Any]], delivery_date: str) -> bytes:
    """Baut die druckfertige Lieferliste als PDF (DIN A4 hoch) und gibt die
    fertigen Bytes zurueck. Keine temporaeren Dateien - alles im Speicher."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=f"Lieferliste {delivery_date}",
    )

    rows = build_pdf_table_data(stops)
    flowables = [
        Paragraph("Lieferliste", _TITLE_STYLE),
        Paragraph(f"Lieferdatum: {_format_delivery_date(delivery_date)}", _SUBTITLE_STYLE),
        Paragraph(format_summary(summarize_stops(stops)), _SUMMARY_STYLE),
        Spacer(1, 4 * mm),
        _build_table(rows),
    ]
    doc.build(flowables)
    return buffer.getvalue()
