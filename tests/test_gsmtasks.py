"""Tests fuer den GSMtasks-Client (Verbindungstest + Schreibintegration) -
komplett mit Mock-Session, kein echter Netzwerkzugriff."""

from unittest.mock import Mock

import pytest
import requests

from routeplanning.gsmtasks import (
    BASE_URL,
    DropoffTask,
    GSMtasksClient,
    GSMtasksError,
    DELIVERY_TIMEZONE,
    build_address,
    delivery_window,
    build_description,
    build_external_id,
    build_order_payload,
    load_env,
    missing_address_parts,
)

API_KEY = "test-key-123"
ACCOUNT_URL = "https://api.gsmtasks.com/accounts/57deb9eb-fake/"


def _client(status_code=200, side_effect=None):
    session = Mock()
    if side_effect is not None:
        session.get.side_effect = side_effect
    else:
        session.get.return_value = Mock(status_code=status_code)
    return GSMtasksClient(api_key=API_KEY, session=session), session


ADDRESS_PARTS = {
    "street": "Kleiner Platz 9-13",
    "street2": "",
    "zip": "76829",
    "city": "Landau in der Pfalz",
    "country": "Deutschland",
}


def _task(**overrides):
    defaults = dict(
        customer_id="603026000008172199",
        external_id=build_external_id("2026-08-20", "603026000008172199"),
        customer_name="Kunde A",
        address_parts=dict(ADDRESS_PARTS),
        position=100,
        delivery_date="2026-08-20",
    )
    defaults.update(overrides)
    return DropoffTask(**defaults)


def test_test_connection_success_on_http_200():
    client, session = _client(status_code=200)

    assert client.test_connection() is True
    session.get.assert_called_once()
    _, kwargs = session.get.call_args
    assert kwargs["headers"]["Authorization"] == "Token test-key-123"


def test_test_connection_uses_accounts_endpoint():
    client, session = _client(status_code=200)

    client.test_connection()

    args, _ = session.get.call_args
    assert args[0] == "https://api.gsmtasks.com/accounts/"


def test_test_connection_raises_on_401_without_leaking_key():
    client, _ = _client(status_code=401)

    with pytest.raises(GSMtasksError) as excinfo:
        client.test_connection()

    assert "401" in str(excinfo.value)
    assert "test-key-123" not in str(excinfo.value)


def test_test_connection_raises_on_server_error():
    client, _ = _client(status_code=500)

    with pytest.raises(GSMtasksError, match="500"):
        client.test_connection()


def test_test_connection_raises_on_network_error_without_leaking_key():
    import requests

    client, _ = _client(side_effect=requests.ConnectionError("boom test-key-123"))

    with pytest.raises(GSMtasksError) as excinfo:
        client.test_connection()

    assert "test-key-123" not in str(excinfo.value)


def test_from_env_raises_when_api_key_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_OTHER_KEY=value\n", encoding="utf-8")

    with pytest.raises(GSMtasksError, match="GSMTASKS_API_KEY"):
        GSMtasksClient.from_env(path=env_file)


def test_from_env_reads_api_key_from_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GSMTASKS_API_KEY=from-file-key\n", encoding="utf-8")

    client = GSMtasksClient.from_env(path=env_file)

    assert client.api_key == "from-file-key"


def test_load_env_prefers_environment_variable(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GSMTASKS_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("GSMTASKS_API_KEY", "env-key")

    values = load_env(path=env_file)

    assert values["GSMTASKS_API_KEY"] == "env-key"


# -- build_description: Nullmengen weglassen ---------------------------------


def test_description_all_quantities_present():
    assert build_description(4, 2, 1) == "Kisten: 4\nSchnittware: 2\nKresse: 1"


def test_description_only_kisten():
    assert build_description(4, 0, 0) == "Kisten: 4"


def test_description_only_schnittware():
    assert build_description(0, 2.5, 0) == "Schnittware: 2.5"


def test_description_only_kresse():
    assert build_description(0, 0, 3) == "Kresse: 3"


def test_description_all_zero_is_empty_string():
    assert build_description(0, 0, 0) == ""


def test_description_formats_whole_number_floats_without_decimal():
    # schnittware kommt aus der DB als REAL/float - 2.0 soll wie "2" aussehen.
    assert build_description(0, 2.0, 0) == "Schnittware: 2"


def test_description_keeps_fractional_values():
    assert build_description(0, 1.5, 0) == "Schnittware: 1.5"


# -- build_external_id: deterministisch ---------------------------------


def test_external_id_deterministic_for_same_input():
    a = build_external_id("2026-08-20", "603026000008172199")
    b = build_external_id("2026-08-20", "603026000008172199")
    assert a == b


def test_external_id_differs_per_date():
    a = build_external_id("2026-08-20", "c1")
    b = build_external_id("2026-08-21", "c1")
    assert a != b


def test_external_id_differs_per_customer():
    a = build_external_id("2026-08-20", "c1")
    b = build_external_id("2026-08-20", "c2")
    assert a != b


def test_external_id_contains_date_and_customer_id():
    result = build_external_id("2026-08-20", "603026000008172199")
    assert "2026-08-20" in result
    assert "603026000008172199" in result


def test_external_id_uses_double_colon_separator_format():
    # Exaktes Format wie vorgegeben: routeplanning::<lieferdatum>::<customer_id>
    result = build_external_id("2026-08-12", "603026000001234567")
    assert result == "routeplanning::2026-08-12::603026000001234567"


def test_external_id_same_customer_different_date_is_a_new_id():
    # Derselbe Kunde an einem anderen Liefertag muss als neuer, legitimer
    # Auftrag uebertragen werden koennen - andere external_id.
    first = build_external_id("2026-08-12", "603026000001234567")
    second = build_external_id("2026-08-13", "603026000001234567")
    assert first != second


def test_external_id_same_customer_same_date_is_identical_id():
    # Derselbe Kunde am selben Liefertag bleibt gegen Doppeluebertragung
    # geschuetzt - identische external_id bei erneutem Aufruf.
    first = build_external_id("2026-08-12", "603026000001234567")
    second = build_external_id("2026-08-12", "603026000001234567")
    assert first == second


# -- build_order_payload: Struktur ---------------------------------------


def test_order_payload_structure_for_one_stop():
    task = _task()
    payload = build_order_payload(task, ACCOUNT_URL)

    assert set(payload) == {"account", "external_id", "auto_assign", "tasks_data"}
    assert payload["account"] == ACCOUNT_URL
    assert payload["external_id"] == task.external_id
    assert payload["auto_assign"] is False
    assert len(payload["tasks_data"]) == 1

    task_data = payload["tasks_data"][0]
    assert set(task_data) == {
        "external_id", "category", "contact", "address", "position",
        "complete_after", "complete_before", "auto_assign",
    }
    assert task_data["complete_after"] == "2026-08-20T00:00:00+02:00"
    assert task_data["complete_before"] == "2026-08-20T23:59:59+02:00"
    assert task_data["external_id"] == task.external_id
    assert task_data["category"] == "drop_off"
    assert task_data["position"] == 100
    assert task_data["auto_assign"] is False
    # Kontaktname = Kundenname, sonst nichts (kein company/phones/emails).
    assert task_data["contact"] == {"name": "Kunde A"}


def test_order_payload_never_assigns_a_driver():
    payload = build_order_payload(_task(), ACCOUNT_URL)
    task_data = payload["tasks_data"][0]
    assert "assignee" not in task_data and "assignee" not in payload
    assert payload["auto_assign"] is False and task_data["auto_assign"] is False


def test_order_payload_structured_address_without_attention_line():
    address = build_order_payload(_task(), ACCOUNT_URL)["tasks_data"][0]["address"]
    assert address == {
        "raw_address": "Kleiner Platz 9-13, 76829 Landau in der Pfalz, Deutschland",
        "street": "Kleiner Platz 9-13",
        "postal_code": "76829",
        "city": "Landau in der Pfalz",
        "country": "Deutschland",
    }


def test_build_address_includes_street2_only_in_raw_address():
    parts = dict(ADDRESS_PARTS, street2="Hintereingang")
    assert build_address(parts)["raw_address"] == (
        "Kleiner Platz 9-13, Hintereingang, 76829 Landau in der Pfalz, Deutschland"
    )
    assert build_address(parts)["street"] == "Kleiner Platz 9-13"


def test_missing_address_parts():
    assert missing_address_parts(ADDRESS_PARTS) == []
    assert missing_address_parts({"street": "", "zip": "76829", "city": " ", "country": "DE"}) == ["street", "city"]
    assert missing_address_parts({}) == ["street", "zip", "city", "country"]


def test_order_payload_no_route_optimization_fields():
    # Wichtig laut Aufgabenstellung: keine Routenoptimierungs-Parameter.
    payload = build_order_payload(_task(), ACCOUNT_URL)
    assert "route_optimization" not in payload
    assert "optimize" not in payload
    assert "optimize" not in payload["tasks_data"][0]


# -- resolve_account_url --------------------------------------------------


def test_resolve_account_url_returns_first_account_url():
    session = Mock()
    session.get.return_value = Mock(status_code=200, json=lambda: [{"url": ACCOUNT_URL, "name": "Kressemann"}])
    client = GSMtasksClient(api_key=API_KEY, session=session)

    assert client.resolve_account_url() == ACCOUNT_URL


def test_resolve_account_url_raises_on_empty_account_list():
    session = Mock()
    session.get.return_value = Mock(status_code=200, json=lambda: [])
    client = GSMtasksClient(api_key=API_KEY, session=session)

    with pytest.raises(GSMtasksError, match="Kein GSMtasks-Account"):
        client.resolve_account_url()


def test_resolve_account_url_raises_on_error_status_without_leaking_key():
    session = Mock()
    session.get.return_value = Mock(status_code=403)
    client = GSMtasksClient(api_key=API_KEY, session=session)

    with pytest.raises(GSMtasksError) as excinfo:
        client.resolve_account_url()
    assert "403" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


# -- create_dropoff_order --------------------------------------------------


def test_create_dropoff_order_success():
    session = Mock()
    session.post.return_value = Mock(
        status_code=201,
        json=lambda: {
            "id": "order-uuid-1",
            "url": "https://api.gsmtasks.com/orders/order-uuid-1/",
            "tasks": ["https://api.gsmtasks.com/tasks/task-uuid-1/"],
        },
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is True
    assert result.order_id == "order-uuid-1"
    assert result.order_url == "https://api.gsmtasks.com/orders/order-uuid-1/"
    assert result.task_url == "https://api.gsmtasks.com/tasks/task-uuid-1/"
    assert result.customer_id == "603026000008172199"

    args, kwargs = session.post.call_args
    assert args[0] == f"{BASE_URL}/orders/"
    assert kwargs["headers"]["Authorization"] == f"Token {API_KEY}"


def test_create_dropoff_order_uses_given_position():
    session = Mock()
    session.post.return_value = Mock(
        status_code=201, json=lambda: {"id": "o1", "url": "u1", "tasks": ["t1"]}
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(position=300), ACCOUNT_URL)

    _, kwargs = session.post.call_args
    assert kwargs["json"]["tasks_data"][0]["position"] == 300
    assert result.outcome == "created"


def test_create_dropoff_order_api_error_returns_failed_result_not_exception():
    session = Mock()
    session.post.return_value = Mock(
        status_code=400, json=lambda: {"external_id": ["Order mit dieser external_id existiert bereits."]}
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is False
    assert result.outcome == "failed"
    assert result.order_id is None
    assert "400" in result.error


def test_create_dropoff_order_error_does_not_leak_api_key():
    session = Mock()
    session.post.return_value = Mock(status_code=401, json=lambda: {"detail": "Invalid token."})
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is False
    assert API_KEY not in result.error


@pytest.mark.parametrize(
    "exc", [requests.ConnectionError, requests.ReadTimeout, requests.exceptions.ChunkedEncodingError]
)
def test_create_dropoff_order_ambiguous_network_error_is_unclear(exc):
    # Anfrage kann bereits angekommen sein -> Ausgang unklar, nicht "failed".
    session = Mock()
    session.post.side_effect = exc(f"boom {API_KEY}")
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is False
    assert result.outcome == "unclear"
    assert API_KEY not in result.error


def test_create_dropoff_order_connect_timeout_is_definitely_not_created():
    session = Mock()
    session.post.side_effect = requests.ConnectTimeout("no route")
    client = GSMtasksClient(api_key=API_KEY, session=session)

    assert client.create_dropoff_order(_task(), ACCOUNT_URL).outcome == "failed"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_create_dropoff_order_server_error_is_unclear(status):
    session = Mock()
    session.post.return_value = Mock(status_code=status, json=lambda: {})
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)
    assert result.outcome == "unclear"
    assert str(status) in result.error


def test_create_dropoff_order_201_without_json_still_counts_as_created():
    session = Mock()
    session.post.return_value = Mock(status_code=201, json=Mock(side_effect=ValueError))
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)
    assert result.outcome == "created" and result.success is True


def test_create_dropoff_order_401_has_actionable_message():
    session = Mock()
    session.post.return_value = Mock(status_code=401, json=lambda: {"detail": "Invalid token."})
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)
    assert result.outcome == "failed"
    assert "API-Schluessel" in result.error and "401" in result.error


# -- find_orders_by_external_id (rein lesend) -------------------------------


def test_find_orders_by_external_id_queries_exact_filter():
    ext = build_external_id("2026-10-15", "c1")
    session = Mock()
    session.get.return_value = Mock(status_code=200, json=lambda: [
        {"url": "https://api.gsmtasks.com/orders/o1/", "external_id": ext},
        {"url": "https://api.gsmtasks.com/orders/o2/", "external_id": "fremd"},
    ])
    client = GSMtasksClient(api_key=API_KEY, session=session)

    found = client.find_orders_by_external_id(ext)

    assert [o["url"] for o in found] == ["https://api.gsmtasks.com/orders/o1/"]
    args, kwargs = session.get.call_args
    assert args[0] == f"{BASE_URL}/orders/"
    assert kwargs["params"] == {"external_id": ext}
    session.post.assert_not_called()


def test_find_orders_by_external_id_accepts_paginated_results():
    session = Mock()
    session.get.return_value = Mock(status_code=200, json=lambda: {"results": [{"external_id": "x", "url": "u"}]})
    client = GSMtasksClient(api_key=API_KEY, session=session)
    assert len(client.find_orders_by_external_id("x")) == 1


@pytest.mark.parametrize("side_effect,status", [(None, 401), (None, 500), (requests.ReadTimeout("t"), None)])
def test_find_orders_by_external_id_raises_when_not_answerable(side_effect, status):
    session = Mock()
    if side_effect is not None:
        session.get.side_effect = side_effect
    else:
        session.get.return_value = Mock(status_code=status)
    client = GSMtasksClient(api_key=API_KEY, session=session)
    with pytest.raises(GSMtasksError):
        client.find_orders_by_external_id("x")


def test_resolve_account_url_401_explains_invalid_key():
    session = Mock()
    session.get.return_value = Mock(status_code=401)
    client = GSMtasksClient(api_key=API_KEY, session=session)
    with pytest.raises(GSMtasksError) as excinfo:
        client.resolve_account_url()
    message = str(excinfo.value)
    assert "401" in message and "neuen API-Schluessel" in message and API_KEY not in message


def test_create_dropoff_order_does_not_call_optimization_endpoint():
    session = Mock()
    session.post.return_value = Mock(
        status_code=201, json=lambda: {"id": "o1", "url": "u1", "tasks": ["t1"]}
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    client.create_dropoff_order(_task(), ACCOUNT_URL)

    for call in session.post.call_args_list:
        assert "optim" not in call.args[0].lower()


# -- Zeitfenster = Liefertag (Europe/Berlin) ---------------------------------

import datetime as _dt


@pytest.mark.parametrize(
    "day,expected",
    [
        # Sommerzeit (MESZ, +02:00)
        ("2026-07-16", ("2026-07-16T00:00:00+02:00", "2026-07-16T23:59:59+02:00")),
        # Winterzeit (MEZ, +01:00)
        ("2026-12-17", ("2026-12-17T00:00:00+01:00", "2026-12-17T23:59:59+01:00")),
        # Umstellung auf Sommerzeit: 29.03.2026, 02:00 -> 03:00
        ("2026-03-29", ("2026-03-29T00:00:00+01:00", "2026-03-29T23:59:59+02:00")),
        # Umstellung auf Winterzeit: 25.10.2026, 03:00 -> 02:00 (Testtag!)
        ("2026-10-25", ("2026-10-25T00:00:00+02:00", "2026-10-25T23:59:59+01:00")),
        # Jahreswechsel und Schaltjahr
        ("2026-12-31", ("2026-12-31T00:00:00+01:00", "2026-12-31T23:59:59+01:00")),
        ("2028-02-29", ("2028-02-29T00:00:00+01:00", "2028-02-29T23:59:59+01:00")),
    ],
)
def test_delivery_window_berlin_with_correct_utc_offset(day, expected):
    assert delivery_window(day) == expected


@pytest.mark.parametrize("day", ["2026-03-29", "2026-10-25", "2026-07-16", "2026-12-17"])
def test_delivery_window_covers_exactly_the_local_calendar_day(day):
    start, end = (_dt.datetime.fromisoformat(v) for v in delivery_window(day))
    assert start.astimezone(DELIVERY_TIMEZONE).date().isoformat() == day
    assert end.astimezone(DELIVERY_TIMEZONE).date().isoformat() == day
    assert (start.astimezone(DELIVERY_TIMEZONE).hour, end.astimezone(DELIVERY_TIMEZONE).hour) == (0, 23)
    # 23 h / 25 h an den Umstellungstagen, sonst 24 h (jeweils minus 1 s)
    hours = {"2026-03-29": 23, "2026-10-25": 25}.get(day, 24)
    assert (end - start).total_seconds() == hours * 3600 - 1


def test_delivery_window_uses_only_delivery_date_not_today(monkeypatch):
    class FrozenDate(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    class FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 11, 45, tzinfo=tz)

    import routeplanning.gsmtasks as g

    monkeypatch.setattr(g._dt, "date", FrozenDate)
    monkeypatch.setattr(g._dt, "datetime", FrozenDatetime)
    start, end = delivery_window("2026-10-25")
    assert start.startswith("2026-10-25T00:00:00") and end.startswith("2026-10-25T23:59:59")


def test_delivery_window_rejects_missing_or_invalid_date():
    for bad in ("", "15.10.2026", "2026-13-01", None):
        with pytest.raises((ValueError, TypeError)):
            delivery_window(bad)


def test_task_requires_delivery_date():
    with pytest.raises(TypeError):
        DropoffTask(customer_id="c", external_id="x", customer_name="K", address_parts={}, position=100)


def test_payload_window_follows_task_delivery_date():
    payload = build_order_payload(_task(delivery_date="2026-10-25"), ACCOUNT_URL)
    task_data = payload["tasks_data"][0]
    assert (task_data["complete_after"], task_data["complete_before"]) == (
        "2026-10-25T00:00:00+02:00", "2026-10-25T23:59:59+01:00")
    # Unveraendert: keine Fahrerzuweisung, auto_assign aus, strukturierte Adresse.
    assert "assignee" not in task_data and task_data["auto_assign"] is False and payload["auto_assign"] is False
    assert task_data["address"]["street"] == "Kleiner Platz 9-13"


def test_external_id_prefix_for_isolated_tests():
    assert build_external_id("2026-10-25", "c1") == "routeplanning::2026-10-25::c1"
    assert build_external_id("2026-10-25", "c1", "routeplanning-test2") == "routeplanning-test2::2026-10-25::c1"
