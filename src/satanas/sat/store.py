import logging
import os
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cfdis (
    uuid           TEXT PRIMARY KEY,
    emisor_rfc     TEXT NOT NULL,
    emisor_nombre  TEXT,
    fecha_emision  TEXT,
    fecha_cert     TEXT,
    total          TEXT,
    efecto         TEXT,
    estado         TEXT,
    anio           INTEGER NOT NULL,
    mes            INTEGER NOT NULL,
    xml_path       TEXT,
    pdf_path       TEXT,
    token          TEXT,
    descubierto_en TEXT,
    descargado_en  TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio      TEXT,
    fin         TEXT,
    anio        INTEGER,
    mes         INTEGER,
    encontrados INTEGER,
    nuevos      INTEGER,
    descargados INTEGER,
    estado      TEXT
);
CREATE INDEX IF NOT EXISTS idx_cfdis_anio_mes ON cfdis(anio, mes);
CREATE INDEX IF NOT EXISTS idx_cfdis_fecha ON cfdis(fecha_emision DESC);
CREATE INDEX IF NOT EXISTS idx_cfdis_pendientes ON cfdis(pdf_path, xml_path);
"""


class CfdiStore:
    def __init__(self, db_path: str):
        dir_path = os.path.dirname(db_path)
        os.makedirs(dir_path, mode=0o700, exist_ok=True)
        try:
            os.chmod(dir_path, 0o700)
        except OSError:
            pass
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()
        if os.path.exists(db_path):
            try:
                os.chmod(db_path, 0o600)
            except OSError:
                pass

    def _migrate(self) -> None:
        """Agrega columnas faltantes a bases existentes."""
        with self._lock:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(cfdis)").fetchall()}
            if "token" not in cols:
                self._conn.execute("ALTER TABLE cfdis ADD COLUMN token TEXT")

    def upsert_cfdi(self, uuid: str, meta: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO cfdis (uuid, emisor_rfc, emisor_nombre, fecha_emision,
                                      fecha_cert, total, efecto, estado, anio, mes,
                                      token, descubierto_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(uuid) DO UPDATE SET
                       token=excluded.token,
                       estado=excluded.estado""",
                (
                    uuid,
                    meta.get("emisor_rfc", ""),
                    meta.get("emisor_nombre", ""),
                    meta.get("fecha_emision", ""),
                    meta.get("fecha_cert", ""),
                    meta.get("total", ""),
                    meta.get("efecto", ""),
                    meta.get("estado", ""),
                    meta.get("anio", 0),
                    meta.get("mes", 0),
                    meta.get("token", ""),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    def mark_downloaded(self, uuid: str, xml_path: str = None, pdf_path: str = None) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT xml_path, pdf_path FROM cfdis WHERE uuid=?", (uuid,)
            ).fetchone()
            xml_path = xml_path or (row["xml_path"] if row else None)
            pdf_path = pdf_path or (row["pdf_path"] if row else None)
            self._conn.execute(
                "UPDATE cfdis SET xml_path=?, pdf_path=?, descargado_en=? WHERE uuid=?",
                (xml_path, pdf_path, datetime.now().isoformat(timespec="seconds"), uuid),
            )
            self._conn.commit()

    def count_pendientes(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM cfdis WHERE pdf_path IS NULL OR xml_path IS NULL"
            ).fetchone()
            return row["n"] if row else 0

    def get_uuids_by_month(self, anio: int, mes: int) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT uuid FROM cfdis WHERE anio=? AND mes=?", (anio, mes)
            ).fetchall()
            return {r["uuid"] for r in rows}

    def get_all(self, anio: int = None, mes: int = None, solo_nomina: bool = False) -> list[dict]:
        with self._lock:
            q = "SELECT * FROM cfdis"
            clauses = []
            params = []
            if anio is not None and mes is not None:
                clauses.append("anio=? AND mes=?")
                params.extend([anio, mes])
            if solo_nomina:
                clauses.append("(LOWER(efecto) LIKE '%nómina%' OR LOWER(efecto) LIKE '%nomina%')")
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY fecha_emision DESC"
            rows = self._conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]

    def get_months(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT anio, mes, COUNT(*) AS total,
                          SUM(CASE WHEN pdf_path IS NOT NULL THEN 1 ELSE 0 END) AS con_pdf,
                          SUM(CASE WHEN xml_path IS NOT NULL THEN 1 ELSE 0 END) AS con_xml,
                          SUM(CASE WHEN pdf_path IS NOT NULL AND xml_path IS NOT NULL THEN 1 ELSE 0 END) AS completos,
                          SUM(CASE WHEN LOWER(efecto) LIKE '%nomina%' OR LOWER(efecto) LIKE '%nómina%' THEN 1 ELSE 0 END) AS nomina_total,
                          SUM(CASE WHEN (LOWER(efecto) LIKE '%nomina%' OR LOWER(efecto) LIKE '%nómina%') AND pdf_path IS NOT NULL THEN 1 ELSE 0 END) AS nomina_desc
                   FROM cfdis GROUP BY anio, mes ORDER BY anio DESC, mes DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_uuid(self, uuid: str) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cfdis WHERE uuid=?", (uuid,)).fetchone()
            return dict(row) if row else {}

    def log_sync(self, anio: int, mes: int, encontrados: int, nuevos: int,
                 descargados: int, estado: str, inicio: str, fin: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sync_log (inicio, fin, anio, mes, encontrados, nuevos, descargados, estado) VALUES (?,?,?,?,?,?,?,?)",
                (inicio, fin, anio, mes, encontrados, nuevos, descargados, estado),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
