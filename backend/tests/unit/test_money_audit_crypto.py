import sqlite3

import pytest

from app.core import crypto
from app.core.money import leg_pnl, sum_money
from app.db import audit
from app.db.sqlite import get_conn


def test_pnl_is_exact_to_paise():
    # float math gives 0.30000000000000004-style noise; Decimal books exact paise
    assert leg_pnl("SELL", 10.1, 9.8, 3) == 0.9
    assert leg_pnl("BUY", 10.1, 9.8, 3) == -0.9
    assert sum_money([0.1, 0.2]) == 0.3
    assert sum_money([]) == 0.0


def test_pnl_sell_buy_symmetry():
    assert leg_pnl("SELL", 50, 40, 75) == -leg_pnl("BUY", 50, 40, 75) == 750.0


def test_crypto_roundtrip_and_wrong_key():
    enc = crypto.encrypt("session-key-123")
    assert enc.startswith("enc:") and "session-key-123" not in enc
    assert crypto.decrypt(enc) == "session-key-123"
    assert crypto.decrypt("plaintext-legacy-value") is None
    assert crypto.decrypt("enc:not-a-valid-token") is None


def test_audit_chain_detects_tampering():
    with get_conn() as conn:
        audit.record(conn, "a", {"x": 1})
        audit.record(conn, "b", {"x": 2})
    with get_conn() as conn:
        assert audit.verify_chain(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_log SET payload_json = '{}' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log WHERE id = 1")


def test_audit_verify_fails_if_chain_forged():
    with get_conn() as conn:
        audit.record(conn, "a", {"x": 1})
        conn.execute("DROP TRIGGER audit_log_no_update")  # attacker with raw DB access
        conn.execute("UPDATE audit_log SET payload_json = '{\"x\": 999}'")
        assert not audit.verify_chain(conn)


def test_db_constraints():
    with get_conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO position_logs (strategy_type, symbol, expiry, legs_json, entry_time) VALUES ('yolo','NIFTY','2026-10-08','[]','t')"
            )
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):  # FK to a missing position
            conn.execute(
                "INSERT INTO position_legs (position_id, strike, option_type, action, qty, entry_price, ltp) VALUES (999,100,'CE','BUY',1,1,1)"
            )
