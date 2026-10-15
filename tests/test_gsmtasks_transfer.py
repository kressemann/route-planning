"""End-to-End-Tests fuer den "An GSMtasks uebertragen"-Workflow gegen die
Flask-App mit einem Fake-GSMtasks-Client. Es findet zu keinem Zeitpunkt ein
echter Netzwerkzugriff auf GSMtasks statt. Zoho wird in diesen Tests gar
nicht erst aufgerufen - der Tourentwurf wird direkt in der SQLite-Datenbank
aufgebaut (wie es "Tour laden" ebenfalls tun wuerde).

Der Fake merkt sich angelegte Orders samt external_id wie GSMtasks selbst,
damit Duplikat-Szenarien (Teilerfolg, unklarer Ausgang, manuell vorhandene
Orders) realistisch nachgestellt werden koennen."""

import pytest

from routeplanning import db
from routeplanning.app import create_app
from routeplanning.gsmtasks import GSMtasksError, OrderResult, build_external_id
from routeplanning.quantities import Stop

ACCOUNT_URL = "https://api.gsmtasks.com/accounts/fake-account/"
DAY = "2026-08-20"
CONFIRM = {"confirm": True}


class FakeGSMtasksClient:
    """Ersetzt GSMtasksClient in Tests - rein lokal, kein Netzwerk."""

    def __init__(self, fail_customer_ids=None, raise_on_resolve=None):
        self.fail_customer_ids = set(fail_customer_ids or [])
        self.unclear_customer_ids = set()
        # unclear, aber serverseitig trotzdem angelegt (Timeout nach Verarbeitung)
        self.unclear_but_created_ids = set()
        self.raise_on_resolve = raise_on_resolve
        self.raise_on_lookup = None
        self.calls = []  # create_dropoff_order-Aufrufe (= Schreibzugriffe)
        self.lookups = []
        self.remote_orders = {}  # external_id -> order-URL (wie in GSMtasks)

    def resolve_account_url(self):
        if self.raise_on_resolve:
            raise self.raise_on_resolve
        return ACCOUNT_URL

    def find_orders_by_external_id(self, external_id):
        self.lookups.append(external_id)
        if self.raise_on_lookup:
            raise self.raise_on_lookup
        url = self.remote_orders.get(external_id)
        return [{"url": url, "external_id": external_id}] if url else []

    def create_dropoff_order(self, task, account_url):
        self.calls.append(task)
        assert account_url == ACCOUNT_URL
        base = dict(customer_id=task.customer_id, customer_name=task.customer_name, external_id=task.external_id)
        if task.customer_id in self.fail_customer_ids:
            return OrderResult(**base, outcome="failed", error="Nicht angelegt: HTTP 400: simulierter Fehler")
        if task.customer_id in self.unclear_but_created_ids:
            self.remote_orders[task.external_id] = f"https://api.gsmtasks.com/orders/order-{task.customer_id}/"
            return OrderResult(**base, outcome="unclear", error="Ausgang unklar (ReadTimeout)")
        if task.customer_id in self.unclear_customer_ids:
            return OrderResult(**base, outcome="unclear", error="Ausgang unklar (ReadTimeout)")
        url = f"https://api.gsmtasks.com/orders/order-{task.customer_id}/"
        self.remote_orders[task.external_id] = url
        return OrderResult(
            **base,
            outcome="created",
            order_id=f"order-{task.customer_id}",
            order_url=url,
            task_url=f"https://api.gsmtasks.com/tasks/task-{task.customer_id}/",
        )


def _parts(name, street="Hauptstraße 7"):
    return {"street": street, "street2": "", "zip": "76829", "city": "Landau", "country": "Deutschland"}


def _stop(customer_id, name, kisten=3, schnittware=0, kresse=0, phone="", email="", address_parts=None):
    return Stop(
        customer_id=customer_id,
        customer_name=name,
        address=f"{name}\nHauptstraße 7\n76829 Landau\nDeutschland",
        kisten=kisten,
        schnittware=schnittware,
        kresse=kresse,
        order_numbers=[f"NA-{customer_id}"],
        phone=phone,
        email=email,
        address_parts=_parts(name) if address_parts is None else address_parts,
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


def _three(app):
    _seed_draft(app, DAY, [_stop("c1", "Kunde A"), _stop("c2", "Kunde B"), _stop("c3", "Kunde C")])


def _batch_count(app):
    conn = db.get_connection(app.config["DB_PATH"])
    count = conn.execute("SELECT COUNT(*) FROM gsmtasks_transfer_batches").fetchone()[0]
    conn.close()
    return count


# -- Grundfunktion -----------------------------------------------------------


def test_transfer_creates_one_order_per_stop_in_order(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)

    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)

    assert resp.status_code == 200
    body = resp.get_json()
    assert (body["stop_count"], body["success_count"], body["failure_count"], body["status"]) == (3, 3, 0, "success")
    assert [s["outcome"] for s in body["stops"]] == ["created"] * 3
    assert [t.customer_id for t in fake.calls] == ["c1", "c2", "c3"]


def test_transfer_requires_explicit_confirmation(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    with app.test_client() as client:
        for body in (None, {}, {"confirm": "yes"}, {"confirm": 1}):
            resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=body)
            assert resp.status_code == 400
    assert fake.calls == [] and fake.lookups == []


def test_transfer_uses_deterministic_external_id(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])
    with app.test_client() as client:
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert fake.calls[0].external_id == build_external_id(DAY, "c1")


def test_transfer_uses_position_100_200_300(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    with app.test_client() as client:
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert [t.position for t in fake.calls] == [100, 200, 300]


def test_transfer_respects_manually_reordered_stops(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])

    with app.test_client() as client:
        client.put(
            f"/api/draft/{DAY}",
            json={
                "stops": [
                    {"customer_id": "c2", "kisten": 3, "schnittware": 0, "kresse": 0},
                    {"customer_id": "c1", "kisten": 3, "schnittware": 0, "kresse": 0},
                ]
            },
        )
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)

    assert [t.customer_id for t in fake.calls] == ["c2", "c1"]
    assert [t.position for t in fake.calls] == [100, 200]


def test_transfer_excludes_deleted_stop(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])
    with app.test_client() as client:
        client.delete(f"/api/draft/{DAY}/stops/c1")
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert [t.customer_id for t in fake.calls] == ["c2"]


def test_transfer_sends_customer_name_and_structured_address_only(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A", phone="0721961570", email="info@example.com")])
    with app.test_client() as client:
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    task = fake.calls[0]
    assert task.customer_name == "Kunde A"
    assert task.address_parts == _parts("Kunde A")
    # Telefon/E-Mail/Mengen werden nicht mehr uebertragen (nicht erforderlich).
    assert not hasattr(task, "phone") and not hasattr(task, "description")


def test_incomplete_address_blocks_whole_transfer(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [
        _stop("c1", "Kunde A"),
        _stop("c2", "Casella Landau", address_parts={"street": "", "zip": "", "city": "", "country": "Deutschland"}),
    ])
    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert resp.status_code == 400
    assert "Casella Landau" in resp.get_json()["error"]
    assert fake.calls == []
    assert _batch_count(app) == 0


def test_draft_loaded_before_update_without_structured_address_is_blocked(app_and_gsmtasks):
    # Entwuerfe, die vor dieser Version geladen wurden, haben noch keine
    # strukturierte Adresse -> erst "Tour laden", dann uebertragen.
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A", address_parts={})])
    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert resp.status_code == 400
    assert "Tour laden" in resp.get_json()["error"]
    assert fake.calls == []


# -- Duplikatschutz -----------------------------------------------------------


def test_second_transfer_after_full_success_is_blocked(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])
    with app.test_client() as client:
        assert client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).status_code == 200
        second = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert second.status_code == 409
    assert "bereits" in second.get_json()["error"]
    assert len(fake.calls) == 1


def test_draft_reports_transfer_state(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])
    fake.fail_customer_ids = {"c2"}
    with app.test_client() as client:
        before = client.get(f"/api/draft/{DAY}").get_json()["gsmtasks"]
        assert before["already_transferred"] is False and before["transferred_count"] == 0

        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
        mid = client.get(f"/api/draft/{DAY}").get_json()["gsmtasks"]
        assert (mid["already_transferred"], mid["transferred_count"], mid["stop_count"]) == (False, 1, 2)
        assert mid["last_batch"]["status"] == "partial"

        fake.fail_customer_ids = set()
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
        after = client.get(f"/api/draft/{DAY}").get_json()["gsmtasks"]
        assert after["already_transferred"] is True and after["transferred_count"] == 2


def test_retry_after_partial_failure_creates_only_missing_stops(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    fake.fail_customer_ids = {"c2"}
    with app.test_client() as client:
        first = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()
        assert first["status"] == "partial"

        fake.fail_customer_ids = set()
        second = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()

    assert [t.customer_id for t in fake.calls] == ["c1", "c2", "c3", "c2"]
    assert [s["customer_id"] for s in second["stops"]] == ["c2"]
    # Position bleibt die aus der Tour (c2 = 2. Stopp).
    assert fake.calls[-1].position == 200
    assert len(fake.remote_orders) == 3


def test_stop_already_existing_in_gsmtasks_is_not_created_again(app_and_gsmtasks):
    # z.B. lokale Historie verloren oder Order frueher angelegt: Vorabsuche findet sie.
    app, fake = app_and_gsmtasks
    _three(app)
    fake.remote_orders[build_external_id(DAY, "c2")] = "https://api.gsmtasks.com/orders/vorhanden/"
    with app.test_client() as client:
        body = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()
    assert [t.customer_id for t in fake.calls] == ["c1", "c3"]
    outcomes = {s["customer_id"]: s["outcome"] for s in body["stops"]}
    assert outcomes == {"c1": "created", "c2": "existing", "c3": "created"}
    assert body["status"] == "success"


def test_unclear_outcome_stops_sending_remaining_stops(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    fake.unclear_customer_ids = {"c2"}
    with app.test_client() as client:
        body = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()
    assert [t.customer_id for t in fake.calls] == ["c1", "c2"]  # c3 nicht mehr gesendet
    assert {s["customer_id"]: s["outcome"] for s in body["stops"]} == {
        "c1": "created", "c2": "unclear", "c3": "not_sent"}
    assert body["status"] == "unclear"


def test_unclear_stop_that_was_created_is_detected_on_retry_no_duplicate(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    fake.unclear_but_created_ids = {"c2"}
    with app.test_client() as client:
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
        fake.unclear_but_created_ids = set()
        state = client.get(f"/api/draft/{DAY}").get_json()["gsmtasks"]
        assert state["unclear_count"] == 1
        body = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()

    assert [t.customer_id for t in fake.calls] == ["c1", "c2", "c3"]  # c2 nur einmal gesendet
    assert {s["customer_id"]: s["outcome"] for s in body["stops"]} == {"c2": "existing", "c3": "created"}


def test_unclear_stop_not_created_is_sent_again_only_after_lookup(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])
    fake.unclear_customer_ids = {"c1"}
    with app.test_client() as client:
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
        fake.unclear_customer_ids = set()
        lookups_before = len(fake.lookups)
        body = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()
    assert len(fake.lookups) > lookups_before
    assert [s["outcome"] for s in body["stops"]] == ["created"]
    assert len(fake.calls) == 2


def test_lookup_failure_sends_nothing(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    fake.raise_on_lookup = GSMtasksError("Vorabpruefung fehlgeschlagen: HTTP 500")
    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert resp.status_code == 502
    assert fake.calls == []
    assert _batch_count(app) == 0


def test_transfer_without_draft_returns_404(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    with app.test_client() as client:
        resp = client.post("/api/draft/2026-09-01/gsmtasks", json=CONFIRM)
    assert resp.status_code == 404
    assert fake.calls == []


def test_transfer_with_no_stops_returns_400(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [])
    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert resp.status_code == 400
    assert fake.calls == []


# -- Fehlerverhalten ------------------------------------------------------


def test_per_stop_failure_reported_with_safe_message(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    fake.fail_customer_ids = {"c1"}
    _seed_draft(app, DAY, [_stop("c1", "Kunde A"), _stop("c2", "Kunde B")])
    with app.test_client() as client:
        body = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).get_json()
    failed = [s for s in body["stops"] if not s["success"]]
    assert len(failed) == 1
    assert failed[0]["customer_name"] == "Kunde A"
    assert failed[0]["outcome"] == "failed"
    assert "simulierter Fehler" in failed[0]["error"]


def test_account_resolution_failure_aborts_before_any_stop_is_sent(tmp_path):
    fake = FakeGSMtasksClient(raise_on_resolve=GSMtasksError("Account konnte nicht ermittelt werden: HTTP 401"))
    app = create_app(db_path=tmp_path / "test.sqlite3", gsmtasks_client_factory=lambda: fake)
    app.testing = True
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])

    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)

    assert resp.status_code == 502
    assert "401" in resp.get_json()["error"]
    assert fake.calls == [] and fake.lookups == []
    assert _batch_count(app) == 0


# -- Vorabpruefung (rein lesend) ----------------------------------------------


def test_precheck_is_read_only_and_reports_plan(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    fake.remote_orders[build_external_id(DAY, "c3")] = "https://api.gsmtasks.com/orders/x/"
    with app.test_client() as client:
        body = client.get(f"/api/draft/{DAY}/gsmtasks/precheck").get_json()
    assert fake.calls == []
    assert (body["new_count"], body["existing_count"], body["transferred_count"]) == (2, 1, 0)
    assert [s["status"] for s in body["stops"]] == ["new", "new", "existing"]
    assert body["ready"] is True
    assert _batch_count(app) == 0


def test_precheck_reports_incomplete_address_and_not_ready(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _seed_draft(app, DAY, [_stop("c1", "Casella", address_parts={"street": "", "zip": "", "city": "", "country": "DE"})])
    with app.test_client() as client:
        body = client.get(f"/api/draft/{DAY}/gsmtasks/precheck").get_json()
    assert body["ready"] is False
    assert body["address_incomplete"] == [{"customer_name": "Casella", "missing": ["street", "zip", "city"]}]


def test_precheck_shows_invalid_key_error(tmp_path):
    fake = FakeGSMtasksClient(raise_on_resolve=GSMtasksError("Account konnte nicht ermittelt werden: HTTP 401"))
    app = create_app(db_path=tmp_path / "t.sqlite3", gsmtasks_client_factory=lambda: fake)
    app.testing = True
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])
    with app.test_client() as client:
        resp = client.get(f"/api/draft/{DAY}/gsmtasks/precheck")
    assert resp.status_code == 502
    assert "401" in resp.get_json()["error"]
    assert fake.calls == []


# -- Dauerhafte Transfersperre je Liefertag -------------------------------------


def _lock(app, day=DAY, reason="Tour manuell in GSMtasks angelegt"):
    conn = db.get_connection(app.config["DB_PATH"])
    db.init_db(conn)
    db.lock_gsmtasks_transfer(conn, day, reason)
    conn.close()


def test_lock_blocks_transfer_before_any_gsmtasks_access(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    _lock(app)
    with app.test_client() as client:
        resp = client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert resp.status_code == 423
    assert "dauerhaft gesperrt" in resp.get_json()["error"]
    assert "manuell" in resp.get_json()["error"]
    assert fake.calls == [] and fake.lookups == []
    assert _batch_count(app) == 0


def test_lock_blocks_precheck_without_gsmtasks_access(tmp_path):
    fake = FakeGSMtasksClient(raise_on_resolve=AssertionError("GSMtasks darf nicht aufgerufen werden"))
    app = create_app(db_path=tmp_path / "t.sqlite3", gsmtasks_client_factory=lambda: fake)
    app.testing = True
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])
    _lock(app)
    with app.test_client() as client:
        resp = client.get(f"/api/draft/{DAY}/gsmtasks/precheck")
    assert resp.status_code == 423
    assert resp.get_json()["locked"]["reason"] == "Tour manuell in GSMtasks angelegt"
    assert fake.lookups == []


def test_lock_survives_tour_reload_and_restart(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    _lock(app)
    # "Tour laden" (replace_stops_from_zoho) mit neuen Adressdaten ...
    _seed_draft(app, DAY, [_stop("c1", "Kunde A"), _stop("c4", "Kunde D")])
    # ... und "Neustart": neue App-Instanz auf derselben DB.
    app2 = create_app(db_path=app.config["DB_PATH"], gsmtasks_client_factory=lambda: fake)
    app2.testing = True
    with app2.test_client() as client:
        draft = client.get(f"/api/draft/{DAY}").get_json()
        assert draft["gsmtasks"]["locked"]["reason"] == "Tour manuell in GSMtasks angelegt"
        assert client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM).status_code == 423
    assert fake.calls == []


def test_lock_does_not_change_tour_quantities_order_or_exports(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    with app.test_client() as client:
        before = client.get(f"/api/draft/{DAY}").get_json()
        pdf_before = client.get(f"/api/draft/{DAY}/pdf").status_code
        _lock(app)
        after = client.get(f"/api/draft/{DAY}").get_json()
        assert after["stops"] == before["stops"]
        assert client.get(f"/api/draft/{DAY}/pdf").status_code == pdf_before == 200
        assert client.get(f"/api/draft/{DAY}/xlsx").status_code == 200


def test_lock_only_affects_its_own_date(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    _seed_draft(app, "2026-08-21", [_stop("c9", "Kunde Z")])
    _lock(app)
    with app.test_client() as client:
        assert client.post("/api/draft/2026-08-21/gsmtasks", json=CONFIRM).status_code == 200
        assert client.get(f"/api/draft/{DAY}").get_json()["gsmtasks"]["locked"] is not None
        assert client.get("/api/draft/2026-08-21").get_json()["gsmtasks"]["locked"] is None
    assert [t.customer_id for t in fake.calls] == ["c9"]


def test_no_web_route_can_remove_a_lock(app_and_gsmtasks):
    app, _ = app_and_gsmtasks
    rules = [str(r) for r in app.url_map.iter_rules()]
    assert not any("lock" in r for r in rules)


def test_lock_is_idempotent_and_requires_reason(tmp_path):
    import pytest as _pytest

    conn = db.get_connection(tmp_path / "l.sqlite3")
    db.init_db(conn)
    first = db.lock_gsmtasks_transfer(conn, DAY, "Grund A")
    second = db.lock_gsmtasks_transfer(conn, DAY, "Grund B")
    assert first == second and second["reason"] == "Grund A"
    with _pytest.raises(ValueError):
        db.lock_gsmtasks_transfer(conn, "2026-08-22", "  ")
    with _pytest.raises(ValueError):
        db.lock_gsmtasks_transfer(conn, "kein-datum", "x")


def test_admin_cli_lock_show_and_unlock_requires_confirm(tmp_path, monkeypatch, capsys):
    from routeplanning import admin

    path = tmp_path / "cli.sqlite3"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", path)
    monkeypatch.setattr(db.get_connection, "__defaults__", (path,))
    assert admin.main(["lock-gsmtasks", DAY, "Tour manuell in GSMtasks angelegt"]) == 0
    assert admin.main(["show-gsmtasks-locks"]) == 0
    assert DAY in capsys.readouterr().out
    assert admin.main(["unlock-gsmtasks", DAY]) == 2  # ohne --confirm
    conn = db.get_connection(path)
    assert db.get_gsmtasks_lock(conn, DAY) is not None
    assert admin.main(["unlock-gsmtasks", DAY, "--confirm"]) == 0
    assert db.get_gsmtasks_lock(conn, DAY) is None



# -- Liefertag als Zeitfenster, Test-Praefix ------------------------------------


def test_transfer_passes_draft_delivery_date_to_every_task(app_and_gsmtasks):
    app, fake = app_and_gsmtasks
    _three(app)
    with app.test_client() as client:
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert [t.delivery_date for t in fake.calls] == [DAY, DAY, DAY]
    assert [t.position for t in fake.calls] == [100, 200, 300]


def test_custom_external_id_prefix_isolates_test_runs(tmp_path):
    fake = FakeGSMtasksClient()
    # Ein frueherer Test-Order mit Standard-Praefix existiert bereits in GSMtasks.
    fake.remote_orders[build_external_id(DAY, "c1")] = "https://api.gsmtasks.com/orders/erster-test/"
    app = create_app(db_path=tmp_path / "t.sqlite3", gsmtasks_client_factory=lambda: fake,
                     external_id_prefix="routeplanning-test2")
    app.testing = True
    _seed_draft(app, DAY, [_stop("c1", "Kunde A")])
    with app.test_client() as client:
        pre = client.get(f"/api/draft/{DAY}/gsmtasks/precheck").get_json()
        assert pre["new_count"] == 1 and pre["existing_count"] == 0
        client.post(f"/api/draft/{DAY}/gsmtasks", json=CONFIRM)
    assert fake.calls[0].external_id == f"routeplanning-test2::{DAY}::c1"
    assert fake.lookups == [f"routeplanning-test2::{DAY}::c1"] * 2
