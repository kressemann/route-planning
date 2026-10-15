"""Tests fuer die lokale Persistenz von GSMtasks-Uebertragungen (Batches) -
insbesondere den Doppelsendeschutz."""

import pytest

from routeplanning import db
from routeplanning.gsmtasks import OrderResult


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "test.sqlite3")
    db.init_db(connection)
    yield connection
    connection.close()


def _success(customer_id, customer_name="Kunde"):
    return OrderResult(
        customer_id=customer_id,
        customer_name=customer_name,
        external_id=f"routeplanning::2026-08-20::{customer_id}",
        success=True,
        order_id=f"order-{customer_id}",
        order_url=f"https://api.gsmtasks.com/orders/order-{customer_id}/",
        task_url=f"https://api.gsmtasks.com/tasks/task-{customer_id}/",
    )


def _failure(customer_id, error="HTTP 400: ungueltige Adresse"):
    return OrderResult(
        customer_id=customer_id,
        customer_name="Kunde",
        external_id=f"routeplanning::2026-08-20::{customer_id}",
        success=False,
        error=error,
    )


def test_no_transfer_yet_is_not_blocked(conn):
    assert db.has_successful_gsmtasks_transfer(conn, "2026-08-20") is False


def test_fully_successful_batch_blocks_second_send(conn):
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1"), _success("c2")])
    assert db.has_successful_gsmtasks_transfer(conn, "2026-08-20") is True


def test_partial_failure_does_not_count_as_successful_transfer(conn):
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1"), _failure("c2")])
    assert db.has_successful_gsmtasks_transfer(conn, "2026-08-20") is False


def test_fully_failed_batch_does_not_count_as_successful(conn):
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_failure("c1"), _failure("c2")])
    assert db.has_successful_gsmtasks_transfer(conn, "2026-08-20") is False


def test_successful_transfer_only_blocks_same_delivery_date(conn):
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1")])
    assert db.has_successful_gsmtasks_transfer(conn, "2026-08-21") is False


def test_batch_records_success_and_failure_counts(conn):
    batch = db.record_gsmtasks_transfer(
        conn, "2026-08-20", [_success("c1"), _success("c2"), _failure("c3")]
    )
    assert batch["stop_count"] == 3
    assert batch["success_count"] == 2
    assert batch["failure_count"] == 1
    assert batch["status"] == "partial"


def test_batch_status_success_when_all_succeed(conn):
    batch = db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1")])
    assert batch["status"] == "success"


def test_batch_status_failed_when_all_fail(conn):
    batch = db.record_gsmtasks_transfer(conn, "2026-08-20", [_failure("c1")])
    assert batch["status"] == "failed"


def test_batch_stops_include_order_and_task_urls_for_success(conn):
    batch = db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1", "Kunde A")])
    stop = batch["stops"][0]
    assert stop["customer_id"] == "c1"
    assert stop["customer_name"] == "Kunde A"
    assert stop["success"] is True
    assert stop["error"] is None


def test_batch_stops_include_safe_error_for_failure(conn):
    batch = db.record_gsmtasks_transfer(conn, "2026-08-20", [_failure("c1", error="HTTP 400: kaputt")])
    stop = batch["stops"][0]
    assert stop["success"] is False
    assert stop["error"] == "HTTP 400: kaputt"


def test_get_latest_gsmtasks_batch_returns_none_when_never_transferred(conn):
    assert db.get_latest_gsmtasks_batch(conn, "2026-08-20") is None


def test_get_latest_gsmtasks_batch_returns_most_recent(conn):
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_failure("c1")])
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1")])

    latest = db.get_latest_gsmtasks_batch(conn, "2026-08-20")
    assert latest["status"] == "success"


# -- outcome je Stopp und Uebertragungsstand --------------------------------


def test_old_rows_without_outcome_are_migrated(tmp_path):
    # Bestand vor Einfuehrung von "outcome" (wie die echte Uebertragung vom 12.08.2026).
    import sqlite3

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE gsmtasks_transfer_batches (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " delivery_date TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,"
        " stop_count INTEGER NOT NULL, success_count INTEGER NOT NULL,"
        " failure_count INTEGER NOT NULL, status TEXT NOT NULL);"
        "CREATE TABLE gsmtasks_transfer_stops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " batch_id INTEGER NOT NULL, customer_id TEXT NOT NULL, customer_name TEXT NOT NULL,"
        " external_id TEXT NOT NULL, success INTEGER NOT NULL, gsmtasks_order_id TEXT,"
        " gsmtasks_order_url TEXT, gsmtasks_task_url TEXT, error_message TEXT);"
        "INSERT INTO gsmtasks_transfer_batches VALUES (1,'2026-08-12','t','t',2,1,1,'partial');"
        "INSERT INTO gsmtasks_transfer_stops VALUES (1,1,'c1','A','x1',1,'o1','u1','t1',NULL);"
        "INSERT INTO gsmtasks_transfer_stops VALUES (2,1,'c2','B','x2',0,NULL,NULL,NULL,'HTTP 400');"
    )
    old.commit()
    old.close()

    conn = db.get_connection(path)
    db.init_db(conn)
    states = db.gsmtasks_stop_states(conn, "2026-08-12")
    assert states["c1"] == {"transferred": True, "outcome": "created", "order_url": "u1"}
    assert states["c2"]["transferred"] is False and states["c2"]["outcome"] == "failed"


def test_stop_states_success_in_any_batch_wins(conn):
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_failure("c1")])
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_success("c1")])
    db.record_gsmtasks_transfer(conn, "2026-08-20", [_failure("c1")])  # spaeterer Fehlversuch aendert nichts
    assert db.gsmtasks_stop_states(conn, "2026-08-20")["c1"]["transferred"] is True


def test_unclear_and_existing_outcomes_are_recorded(conn):
    unclear = OrderResult(customer_id="c1", customer_name="A", external_id="x1", outcome="unclear", error="Timeout")
    existing = OrderResult(customer_id="c2", customer_name="B", external_id="x2", outcome="existing", order_url="u2")
    batch = db.record_gsmtasks_transfer(conn, "2026-08-20", [unclear, existing])
    assert batch["status"] == "unclear"
    assert [(s["outcome"], s["success"]) for s in batch["stops"]] == [("unclear", False), ("existing", True)]
    summary = db.gsmtasks_summary(conn, "2026-08-20", ["c1", "c2", "c3"])
    assert (summary["transferred_count"], summary["unclear_count"], summary["already_transferred"]) == (1, 1, False)


def test_concurrent_worker_migration_does_not_crash(tmp_path):
    # Zwei gunicorn-Worker starten gleichzeitig: beide sehen die Spalte noch
    # nicht, einer legt sie an - der andere darf daran nicht scheitern
    # (Vorfall beim Deployment am 15.10.2026: "duplicate column name").
    path = tmp_path / "race.sqlite3"
    first = db.get_connection(path)
    db.init_db(first)
    second = db.get_connection(path)
    second.execute("CREATE TABLE t (id INTEGER)")
    real_execute = second.execute

    class StaleView:
        """Liefert fuer PRAGMA table_info den Stand VOR der Migration des anderen Workers."""

        def execute(self, sql, *args):
            if sql.startswith("PRAGMA table_info"):
                return []
            return real_execute(sql, *args)

    first.execute("ALTER TABLE t ADD COLUMN extra TEXT")
    first.commit()
    db._ensure_column(StaleView(), "t", "extra", "extra TEXT")  # darf nicht werfen

    with pytest.raises(Exception):
        db._ensure_column(StaleView(), "gibt_es_nicht", "x", "x TEXT")
