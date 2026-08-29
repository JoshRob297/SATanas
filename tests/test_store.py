import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from satanas.sat.store import CfdiStore


def _store(tmp_path):
    return CfdiStore(str(tmp_path / "cfdi.db"))


def test_upsert_and_get(tmp_path):
    s = _store(tmp_path)
    s.upsert_cfdi("U1", {"emisor_rfc": "SAT060614LW1", "anio": 2025, "mes": 1})
    s.upsert_cfdi("U2", {"emisor_rfc": "X", "anio": 2025, "mes": 2})
    assert s.get_uuids_by_month(2025, 1) == {"U1"}
    assert s.get_uuids_by_month(2025, 2) == {"U2"}
    assert len(s.get_all()) == 2


def test_upsert_idempotent(tmp_path):
    s = _store(tmp_path)
    s.upsert_cfdi("U1", {"emisor_rfc": "A", "anio": 2025, "mes": 1})
    s.upsert_cfdi("U1", {"emisor_rfc": "A", "anio": 2025, "mes": 1})
    assert len(s.get_all()) == 1


def test_mark_downloaded(tmp_path):
    s = _store(tmp_path)
    s.upsert_cfdi("U1", {"anio": 2025, "mes": 1})
    assert s.get_by_uuid("U1")["xml_path"] is None
    s.mark_downloaded("U1", "/tmp/a.xml", "/tmp/a.pdf")
    row = s.get_by_uuid("U1")
    assert row["xml_path"] == "/tmp/a.xml"
    assert row["pdf_path"] == "/tmp/a.pdf"
    assert row["descargado_en"] is not None


def test_get_months_counts(tmp_path):
    s = _store(tmp_path)
    s.upsert_cfdi("U1", {"anio": 2025, "mes": 1})
    s.upsert_cfdi("U2", {"anio": 2025, "mes": 1})
    s.upsert_cfdi("U3", {"anio": 2025, "mes": 2})
    s.mark_downloaded("U1", None, "/tmp/u1.pdf")
    months = {f"{m['anio']}-{m['mes']}": m for m in s.get_months()}
    assert months["2025-1"]["total"] == 2
    assert months["2025-1"]["con_pdf"] == 1
    assert months["2025-1"]["con_xml"] == 0
    assert months["2025-1"]["completos"] == 0
    assert months["2025-2"]["total"] == 1


def test_log_sync(tmp_path):
    s = _store(tmp_path)
    s.log_sync(2025, 1, 5, 2, 2, "ok", "a", "b")
    conn = sqlite3.connect(str(tmp_path / "cfdi.db"))
    n = conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0]
    assert n == 1
