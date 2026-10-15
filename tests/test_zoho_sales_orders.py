"""Regressionstests fuer die Auftragsauswahl je Liefertag (ohne Netz).

Hintergrund: Bis 15.10.2026 liess eine Status-Positivliste alle Auftraege
fallen, deren Versand in Zoho bereits gebucht war ("shipped"/"fulfilled").
Live belegt fuer den 14.10.2026: Zoho lieferte 8 Auftraege (alle "shipped"),
die Tourenplanung zeigte 0 Stopps.
"""

import datetime as dt

from routeplanning.zoho import EXCLUDED_STATUSES, ZohoInventory


class _FakeInventory(ZohoInventory):
    """Ersetzt nur den HTTP-Aufruf; Filter- und Paginierlogik bleibt echt."""

    def __init__(self, orders, per_page=200):
        super().__init__("id", "secret", "refresh", "org")
        self._orders = orders
        self._per_page = per_page
        self.requests = []

    def _request(self, method, path, **kwargs):
        assert method == "GET"
        params = kwargs.get("params", {})
        self.requests.append((path, dict(params)))
        if path == "/salesorders":
            page = params.get("page", 1)
            start = (page - 1) * self._per_page
            chunk = self._orders[start : start + self._per_page]
            return {
                "salesorders": [
                    {k: o[k] for k in ("salesorder_id", "shipment_date", "status")}
                    for o in chunk
                ],
                "page_context": {"has_more_page": start + self._per_page < len(self._orders)},
            }
        salesorder_id = path.rsplit("/", 1)[1]
        return {"salesorder": next(o for o in self._orders if o["salesorder_id"] == salesorder_id)}


def _so(n, day, status):
    return {"salesorder_id": f"so-{n}", "shipment_date": day, "status": status}


def test_only_draft_and_void_are_excluded():
    assert EXCLUDED_STATUSES == frozenset({"draft", "void"})


def test_regression_20261014_shipped_orders_are_loaded():
    # Live-Bild 14.10.2026: 8 Auftraege, alle "shipped".
    orders = [_so(i, "2026-10-14", "shipped") for i in range(8)]
    client = _FakeInventory(orders)
    result = client.sales_orders_shipped_on(dt.date(2026, 10, 14))
    assert len(result) == 8


def test_all_relevant_statuses_included_draft_and_void_excluded():
    day = "2026-10-15"
    orders = [
        _so(1, day, "confirmed"),
        _so(2, day, "open"),
        _so(3, day, "shipped"),
        _so(4, day, "fulfilled"),
        _so(5, day, "partially_shipped"),
        _so(6, day, "invoiced"),
        _so(7, day, "draft"),
        _so(8, day, "void"),
    ]
    client = _FakeInventory(orders)
    ids = {o["salesorder_id"] for o in client.sales_orders_shipped_on(dt.date(2026, 10, 15))}
    assert ids == {"so-1", "so-2", "so-3", "so-4", "so-5", "so-6"}


def test_other_shipment_dates_filtered_locally_even_if_api_ignores_filter():
    orders = [
        _so(1, "2026-10-15", "confirmed"),
        _so(2, "2026-10-14", "shipped"),
        _so(3, "2026-10-16", "confirmed"),
        _so(4, "", "fulfilled"),
    ]
    client = _FakeInventory(orders)
    result = client.sales_orders_shipped_on(dt.date(2026, 10, 15))
    assert [o["salesorder_id"] for o in result] == ["so-1"]
    path, params = client.requests[0]
    assert params["shipment_date_start"] == params["shipment_date_end"] == "2026-10-15"


def test_pagination_reads_all_pages():
    orders = [_so(i, "2026-10-15", "confirmed") for i in range(5)]
    client = _FakeInventory(orders, per_page=2)
    result = client.sales_orders_shipped_on(dt.date(2026, 10, 15))
    assert len(result) == 5
    pages = [p["page"] for path, p in client.requests if path == "/salesorders"]
    assert pages == [1, 2, 3]
