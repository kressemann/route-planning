from routeplanning.quantities import build_stops, format_address, produktart_from_item


_DEFAULT_ADDRESS = {
    "attention": None,  # per Kunde ueberschrieben
    "address": "Musterstr. 1",
    "zip": "12345",
    "city": "Musterstadt",
}


def _order(customer_id, customer_name, salesorder_number, line_items, shipping_address=None):
    if shipping_address is None:
        shipping_address = {**_DEFAULT_ADDRESS, "attention": customer_name}
    return {
        "salesorder_id": f"so-{salesorder_number}",
        "salesorder_number": salesorder_number,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "shipping_address": shipping_address,
        "line_items": line_items,
    }


def _line(item_id, name, quantity):
    return {"item_id": item_id, "name": name, "quantity": quantity}


def no_produktart(_item_id):
    return None


def test_einzel_1_9_rounds_up():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Radieschen Mix 1/9", 10)]),
    ]
    stops, warnings = build_stops(orders, no_produktart)
    assert warnings == []
    assert len(stops) == 1
    assert stops[0].kisten == 2  # ceil(10/9)
    assert stops[0].schnittware == 0
    assert stops[0].kresse == 0


def test_kresse_rounds_up():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Gartenkresse 1/12 Schale/Pad", 13)]),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert stops[0].kresse == 2  # ceil(13/12)
    assert stops[0].kisten == 0


def test_schnittware_sums_directly_no_rounding():
    orders = [
        _order(
            "c1",
            "Kunde A",
            "NA-1",
            [
                _line("i1", "Roter Rettich Microgreens 200g", 3),
                _line("i2", "Oxalis Mix 15 Stk.", 2),
            ],
        ),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert stops[0].schnittware == 5
    assert stops[0].kisten == 0


def test_komplette_ve_adds_directly_to_kisten():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Shira Mix", 2), _line("i2", "Sakina Mix", 1)]),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert stops[0].kisten == 3


def test_steige_counts_one_to_one_as_kisten():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Afiora Steige", 3)]),
    ]
    stops, warnings = build_stops(orders, no_produktart)
    assert warnings == []
    assert stops[0].kisten == 3  # 1 bestellte Steige = 1 Kiste, keine Rundung noetig


def test_mix_article_one_unit_equals_one_kiste():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Chefs-Mix", 1)]),
    ]
    stops, warnings = build_stops(orders, no_produktart)
    assert warnings == []
    assert stops[0].kisten == 1


def test_mix_article_multiple_units_equal_multiple_kisten():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Chefs-Mix", 3)]),
    ]
    stops, warnings = build_stops(orders, no_produktart)
    assert warnings == []
    assert stops[0].kisten == 3


def test_mix_articles_from_different_line_items_sum_up():
    orders = [
        _order(
            "c1",
            "Kunde A",
            "NA-1",
            [_line("i1", "Chefs-Mix", 2), _line("i2", "Bunter Salat Mix", 1)],
        ),
    ]
    stops, warnings = build_stops(orders, no_produktart)
    assert warnings == []
    assert stops[0].kisten == 3


def test_einzel_1_9_and_komplette_ve_combine_in_kisten():
    orders = [
        _order(
            "c1",
            "Kunde A",
            "NA-1",
            [_line("i1", "Radieschen Mix 1/9", 9), _line("i2", "Shira Mix", 2)],
        ),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert stops[0].kisten == 1 + 2  # ceil(9/9) + 2


def test_multiple_orders_same_customer_same_day_merge():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Radieschen Mix 1/9", 5)]),
        _order("c1", "Kunde A", "NA-2", [_line("i1", "Radieschen Mix 1/9", 4)]),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert len(stops) == 1
    assert stops[0].kisten == 1  # ceil((5+4)/9)
    assert sorted(stops[0].order_numbers) == ["NA-1", "NA-2"]


def test_different_customers_produce_separate_stops():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Radieschen Mix 1/9", 5)]),
        _order("c2", "Kunde B", "NA-2", [_line("i1", "Radieschen Mix 1/9", 5)]),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert {s.customer_id for s in stops} == {"c1", "c2"}


def test_unklassifiziert_produces_warning_and_excluded_from_sums():
    orders = [
        _order(
            "c1",
            "Kunde A",
            "NA-1",
            [_line("i1", "Radieschen Mix 1/9", 9), _line("i9", "Mystery Artikel", 4)],
        ),
    ]
    stops, warnings = build_stops(orders, no_produktart)
    assert stops[0].kisten == 1  # nur der klassifizierte Artikel zaehlt
    assert len(warnings) == 1
    assert warnings[0].article_name == "Mystery Artikel"
    assert warnings[0].quantity == 4
    assert warnings[0].customer_name == "Kunde A"


def test_produktart_custom_field_used_for_classification():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Spezialartikel", 3)]),
    ]

    def get_produktart(item_id):
        return "Schnittware" if item_id == "i1" else None

    stops, warnings = build_stops(orders, get_produktart)
    assert warnings == []
    assert stops[0].schnittware == 3


def test_address_kept_from_first_order_with_address():
    orders = [
        _order("c1", "Kunde A", "NA-1", [_line("i1", "Shira Mix", 1)], shipping_address={}),
        _order(
            "c1",
            "Kunde A",
            "NA-2",
            [_line("i1", "Shira Mix", 1)],
            shipping_address={"address": "Zweite Str. 2", "zip": "54321", "city": "Anderstadt"},
        ),
    ]
    stops, _ = build_stops(orders, no_produktart)
    assert "Zweite Str. 2" in stops[0].address


def test_format_address_empty():
    assert format_address(None) == ""
    assert format_address({}) == ""


def test_format_address_full():
    address = format_address(
        {
            "attention": "Kunde A",
            "address": "Musterstr. 1",
            "street2": "Hinterhof",
            "zip": "12345",
            "city": "Musterstadt",
            "country": "Deutschland",
        }
    )
    assert address == "Kunde A\nMusterstr. 1, Hinterhof\n12345 Musterstadt\nDeutschland"


def test_produktart_from_item_reads_custom_field():
    item = {"custom_fields": [{"label": "Produktart", "value": "Schnittware"}]}
    assert produktart_from_item(item, "Produktart") == "Schnittware"


def test_produktart_from_item_missing_field():
    item = {"custom_fields": []}
    assert produktart_from_item(item, "Produktart") is None


# -- Telefon/E-Mail (fuer GSMtasks-Uebertragung) -----------------------------


def test_phone_and_email_taken_from_existing_order_fields():
    order = _order("c1", "Kunde A", "NA-1", [_line("i1", "Shira Mix", 1)])
    order["shipping_address"]["phone"] = "0721961570"
    order["contact_person_details"] = [{"email": "info@example.com"}]

    stops, _ = build_stops([order], no_produktart)
    assert stops[0].phone == "0721961570"
    assert stops[0].email == "info@example.com"


def test_phone_and_email_empty_when_not_present_no_extra_lookup():
    orders = [_order("c1", "Kunde A", "NA-1", [_line("i1", "Shira Mix", 1)])]
    stops, _ = build_stops(orders, no_produktart)
    assert stops[0].phone == ""
    assert stops[0].email == ""


def test_email_ignores_contact_persons_without_email():
    order = _order("c1", "Kunde A", "NA-1", [_line("i1", "Shira Mix", 1)])
    order["contact_person_details"] = [{"email": ""}, {"email": "second@example.com"}]

    stops, _ = build_stops([order], no_produktart)
    assert stops[0].email == "second@example.com"


def test_phone_kept_from_first_order_with_phone():
    order1 = _order("c1", "Kunde A", "NA-1", [_line("i1", "Shira Mix", 1)])
    order1["shipping_address"]["phone"] = ""
    order2 = _order("c1", "Kunde A", "NA-2", [_line("i1", "Shira Mix", 1)])
    order2["shipping_address"]["phone"] = "0721961570"

    stops, _ = build_stops([order1, order2], no_produktart)
    assert stops[0].phone == "0721961570"
