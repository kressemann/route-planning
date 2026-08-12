"""Tests fuer den GSMtasks-Client (Verbindungstest + Schreibintegration) -
komplett mit Mock-Session, kein echter Netzwerkzugriff."""

from unittest.mock import Mock

import pytest

from routeplanning.gsmtasks import (
    BASE_URL,
    DropoffTask,
    GSMtasksClient,
    GSMtasksError,
    build_description,
    build_external_id,
    build_order_payload,
    load_env,
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


def _task(**overrides):
    defaults = dict(
        customer_id="603026000008172199",
        external_id=build_external_id("2026-08-20", "603026000008172199"),
        reference="2026-08-20 Kunde A",
        customer_name="Kunde A",
        address="Musterstr. 1\n12345 Musterstadt",
        description="Kisten: 3",
        position=100,
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

    assert payload["account"] == ACCOUNT_URL
    assert payload["external_id"] == task.external_id
    assert payload["auto_assign"] is False
    assert len(payload["tasks_data"]) == 1

    task_data = payload["tasks_data"][0]
    assert task_data["external_id"] == task.external_id
    assert task_data["category"] == "drop_off"
    assert task_data["address"] == {"raw_address": task.address}
    assert task_data["description"] == "Kisten: 3"
    assert task_data["position"] == 100
    assert task_data["auto_assign"] is False
    assert task_data["contact"]["name"] == "Kunde A"
    assert task_data["contact"]["company"] == "Kunde A"


def test_order_payload_omits_phone_and_email_when_absent():
    task = _task(phone="", email="")
    payload = build_order_payload(task, ACCOUNT_URL)
    contact = payload["tasks_data"][0]["contact"]
    assert "phones" not in contact
    assert "emails" not in contact


def test_order_payload_includes_phone_and_email_when_present():
    task = _task(phone="0721961570", email="info@example.com")
    payload = build_order_payload(task, ACCOUNT_URL)
    contact = payload["tasks_data"][0]["contact"]
    assert contact["phones"] == ["0721961570"]
    assert contact["emails"] == ["info@example.com"]


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


def test_create_dropoff_order_uses_given_position_and_reference():
    session = Mock()
    session.post.return_value = Mock(
        status_code=201, json=lambda: {"id": "o1", "url": "u1", "tasks": ["t1"]}
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    client.create_dropoff_order(_task(position=300, reference="2026-08-20 Kunde X"), ACCOUNT_URL)

    _, kwargs = session.post.call_args
    task_data = kwargs["json"]["tasks_data"][0]
    assert task_data["position"] == 300
    assert kwargs["json"]["reference"] == "2026-08-20 Kunde X"


def test_create_dropoff_order_api_error_returns_failed_result_not_exception():
    session = Mock()
    session.post.return_value = Mock(
        status_code=400, json=lambda: {"external_id": ["Order mit dieser external_id existiert bereits."]}
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is False
    assert result.order_id is None
    assert "400" in result.error


def test_create_dropoff_order_error_does_not_leak_api_key():
    session = Mock()
    session.post.return_value = Mock(status_code=401, json=lambda: {"detail": "Invalid token."})
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is False
    assert API_KEY not in result.error


def test_create_dropoff_order_network_error_returns_failed_result():
    import requests

    session = Mock()
    session.post.side_effect = requests.ConnectionError(f"boom {API_KEY}")
    client = GSMtasksClient(api_key=API_KEY, session=session)

    result = client.create_dropoff_order(_task(), ACCOUNT_URL)

    assert result.success is False
    assert API_KEY not in result.error


def test_create_dropoff_order_does_not_call_optimization_endpoint():
    session = Mock()
    session.post.return_value = Mock(
        status_code=201, json=lambda: {"id": "o1", "url": "u1", "tasks": ["t1"]}
    )
    client = GSMtasksClient(api_key=API_KEY, session=session)

    client.create_dropoff_order(_task(), ACCOUNT_URL)

    for call in session.post.call_args_list:
        assert "optim" not in call.args[0].lower()
