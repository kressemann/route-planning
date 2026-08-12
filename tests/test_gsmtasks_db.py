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
