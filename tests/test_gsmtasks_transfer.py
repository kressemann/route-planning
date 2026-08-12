"""End-to-End-Tests fuer den "An GSMtasks uebertragen"-Workflow gegen die
Flask-App mit einem Fake-GSMtasks-Client. Es findet zu keinem Zeitpunkt ein
echter Netzwerkzugriff auf GSMtasks statt. Zoho wird in diesen Tests gar
nicht erst aufgerufen - der Tourentwurf wird direkt in der SQLite-Datenbank
aufgebaut (wie es "Tour laden" ebenfalls tun wuerde)."""

import pytest

from routeplanning import db
from routeplanning.app import create_app
from routeplanning.gsmtasks import OrderResult, build_external_id
from routeplanning.quantities import Stop

ACCOUNT_URL = "https://api.gsmtasks.com/accounts/fake-account/"


class FakeGSMtasksClient:
    """Ersetzt GSMtasksClient in Tests - rein lokal, kein Netzwerk."""

    def __init__(self, fail_customer_ids=None, raise_on_resolve=None):
        self.fail_customer_ids = set(fail_customer_ids or [])
        self.raise_on_resolve = raise_on_resolve
        self.calls = []

    def resolve_account_url(self):
        if self.raise_on_resolve:
            raise self.raise_on_resolve
        return ACCOUNT_URL

    def create_dropoff_order(self, task, account_url):
        self.calls.append(task)
        assert account_url == ACCOUNT_URL
        if task.customer_id in self.fail_customer_ids:
            return OrderResult(
                customer_id=task.customer_id,
                customer_name=task.customer_name,
                external_id=task.external_id,
                success=False,
                error="HTTP 400: simulierter Fehler",
            )
        return OrderResult(
            customer_id=task.customer_id,
            customer_name=task.customer_name,
            external_id=task.external_id,
            success=True,
            order_id=f"order-{task.customer_id}",
            order_url=f"https://api.gsmtasks.com/orders/order-{task.customer_id}/",
            task_url=f"https://api.gsmtasks.com/tasks/task-{task.customer_id}/",
        )


def _stop(customer_id, name, kisten=3, schnittware=0, kresse=0, phone="", email=""):
    return Stop(
        customer_id=customer_id,
        customer_name=name,
        address=f"Adresse {name}\n12345 Musterstadt",
        kisten=kisten,
        schnittware=schnittware,
        kresse=kresse,
        order_numbers=[f"NA-{customer_id}"],
        phone=phone,
        email=email,
    )


def _fail_if_zoho_called():
    raise AssertionError("Zoho darf in diesen Tests nicht aufgerufen werden")


@pytest.fixture
def app_and_gsmtasks(tmp_path):
    fake = FakeGSMtasksClient()
    app = create_app(
        db_path=tmp_path / "test.sqlite3",
        zoho_client_factory=_fail_if_zoho_called,
        gsmtasks_client_factory=lambda: fake,
    )
    app.testing = True
    return app, fake


def _seed_draft(app, delivery_date, stops):
    conn = db.get_connection(app.config["DB_PATH"])
    db.init_db(conn)
    db.replace_stops_from_zoho(conn, delivery_date, stops, [])
    conn.close()


def test_transfer_creates_one_order_per_stop_in_order(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")])

    with app.test_client() as client:
        resp = client.post("/api/draft/2026-08-20/gsmtasks")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stop_count"] == 3
    assert body["success_count"] == 3
    assert body["failure_count"] == 0
    assert body["status"] == "success"

    assert [t.customer_id for t in fake.calls] == ["c1", "c2", "c3"]


def test_transfer_uses_deterministic_external_id(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A")])

    with app.test_client() as client:
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert fake.calls[0].external_id == build_external_id("2026-08-20", "c1")


def test_transfer_uses_position_100_200_300(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")])

    with app.test_client() as client:
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert [t.position for t in fake.calls] == [100, 200, 300]


def test_transfer_respects_manually_reordered_stops(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])

    with app.test_client() as client:
        # Reihenfolge im Automation Center manuell umgedreht.
        client.put(
            "/api/draft/2026-08-20",
            json={
                "stops": [
                    {"customer_id": "c2", "kisten": 3, "schnittware": 0, "kresse": 0},
                    {"customer_id": "c1", "kisten": 3, "schnittware": 0, "kresse": 0},
                ]
            },
        )
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert [t.customer_id for t in fake.calls] == ["c2", "c1"]
    assert [t.position for t in fake.calls] == [100, 200]


def test_transfer_uses_manual_quantity_overrides(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A", kisten=3)])

    with app.test_client() as client:
        client.put(
            "/api/draft/2026-08-20",
            json={"stops": [{"customer_id": "c1", "kisten": 7, "schnittware": 0, "kresse": 0}]},
        )
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert fake.calls[0].description == "Kisten: 7"


def test_transfer_omits_zero_quantities_in_description(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A", kisten=0, schnittware=2, kresse=0)])

    with app.test_client() as client:
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert fake.calls[0].description == "Schnittware: 2"


def test_transfer_excludes_deleted_stop(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])

    with app.test_client() as client:
        client.delete("/api/draft/2026-08-20/stops/c1")
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert [t.customer_id for t in fake.calls] == ["c2"]


def test_transfer_includes_phone_and_email_when_present(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(
        app,
        "2026-08-20",
        [_stop("c1", "Kunde A", phone="0721961570", email="info@example.com")],
    )

    with app.test_client() as client:
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert fake.calls[0].phone == "0721961570"
    assert fake.calls[0].email == "info@example.com"


def test_transfer_uses_full_current_delivery_address(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A")])

    with app.test_client() as client:
        client.post("/api/draft/2026-08-20/gsmtasks")

    assert fake.calls[0].address == "Adresse Kunde A\n12345 Musterstadt"


# -- Doppelsendeschutz --------------------------------------------------


def test_second_transfer_after_full_success_is_blocked(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A")])

    with app.test_client() as client:
        first = client.post("/api/draft/2026-08-20/gsmtasks")
        assert first.status_code == 200

        second = client.post("/api/draft/2026-08-20/gsmtasks")

    assert second.status_code == 409
    assert "bereits" in second.get_json()["error"]
    # Der zweite Versand darf GSMtasks gar nicht erst aufrufen.
    assert len(fake.calls) == 1


def test_draft_reports_already_transferred_after_success(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A")])

    with app.test_client() as client:
        before = client.get("/api/draft/2026-08-20").get_json()
        assert before["gsmtasks"]["already_transferred"] is False

        client.post("/api/draft/2026-08-20/gsmtasks")

        after = client.get("/api/draft/2026-08-20").get_json()
        assert after["gsmtasks"]["already_transferred"] is True
        assert after["gsmtasks"]["last_batch"]["status"] == "success"


def test_partial_failure_does_not_block_retry(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    fake.fail_customer_ids = {"c2"}
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])

    with app.test_client() as client:
        first = client.post("/api/draft/2026-08-20/gsmtasks")
        assert first.status_code == 200
        body = first.get_json()
        assert body["status"] == "partial"
        assert body["success_count"] == 1
        assert body["failure_count"] == 1

        # Da nicht vollstaendig erfolgreich, darf erneut versucht werden.
        second = client.post("/api/draft/2026-08-20/gsmtasks")
        assert second.status_code == 200


def test_transfer_without_draft_returns_404(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    with app.test_client() as client:
        resp = client.post("/api/draft/2026-09-01/gsmtasks")
    assert resp.status_code == 404
    assert fake.calls == []


def test_transfer_with_no_stops_returns_400(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, "2026-08-20", [])
    with app.test_client() as client:
        resp = client.post("/api/draft/2026-08-20/gsmtasks")
    assert resp.status_code == 400
    assert fake.calls == []


# -- Fehlerverhalten ------------------------------------------------------


def test_per_stop_failure_reported_with_safe_message(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    fake.fail_customer_ids = {"c1"}
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])

    with app.test_client() as client:
        resp = client.post("/api/draft/2026-08-20/gsmtasks")

    body = resp.get_json()
    failed = [s for s in body["stops"] if not s["success"]]
    assert len(failed) == 1
    assert failed[0]["customer_name"] == "Kunde A"
    assert "simulierter Fehler" in failed[0]["error"]


def test_account_resolution_failure_aborts_before_any_stop_is_sent(tmp_path):
    from routeplanning.gsmtasks import GSMtasksError

    fake = FakeGSMtasksClient(raise_on_resolve=GSMtasksError("Account konnte nicht ermittelt werden: HTTP 401"))
    app = create_app(
        db_path=tmp_path / "test.sqlite3",
        gsmtasks_client_factory=lambda: fake,
    )
    app.testing = True
    _seed_draft(app, "2026-08-20", [_stop("c1", "Kunde A")])

    with app.test_client() as client:
        resp = client.post("/api/draft/2026-08-20/gsmtasks")

    assert resp.status_code == 502
    assert fake.calls == []

    conn = db.get_connection(app.config["DB_PATH"])
    count = conn.execute("SELECT COUNT(*) FROM gsmtasks_transfer_batches").fetchone()[0]
    conn.close()
    assert count == 0
