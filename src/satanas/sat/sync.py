import logging
import os
import re
import time
from datetime import date, datetime
import threading

from playwright.sync_api import sync_playwright

from .. import config
from .client import CfdiPortalClient, SatError
from .store import CfdiStore

logger = logging.getLogger(__name__)

POLITENESS_DELAY = 1.0  # segundos entre CFDIs para no martillar al SAT
UUID_REGEX = re.compile(r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$")


def months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """Meses inclusive entre start y end."""
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def rolling_range(today: date = None, months: int = None) -> tuple[date, date]:
    """Rango rolling: hoy - N meses → hoy (siempre dinámico)."""
    months = months if months is not None else config.SYNC_MONTHS_BACK
    today = today or date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        y -= 1
        m += 12
    start = date(y, m, 1)
    return start, today


class SyncManager:
    """Orquesta el sync incremental contra el Portal CFDI."""

    def __init__(self, rfc: str, password: str, progress=None, captcha_relay=None,
                 store: CfdiStore = None):
        self.client = CfdiPortalClient(rfc, password, progress, captcha_relay)
        self.store = store or CfdiStore(config.CFDI_DB)
        self.progress = progress

    def _p(self, text: str):
        if self.progress:
            self.progress.post(text)

    def run(self, months: list[tuple[int, int]] = None, cancel_event: threading.Event = None) -> dict:
        """Corre el sync completo (o solo los meses indicados).
        Devuelve {nuevos: int, encontrados: int, pendientes: int, meses: int}."""
        months = months or list(reversed(months_in_range(*rolling_range(months=config.SYNC_MONTHS_BACK))))
        descargados_total = 0
        encontrados_total = 0
        pendientes_total = 0
        total = len(months)
        with sync_playwright() as p:
            browser, ctx = self.client.new_browser(p)
            try:
                page = ctx.new_page()
                self.client.ensure_logged_in(page)
                for i, (anio, mes) in enumerate(months, 1):
                    if cancel_event and cancel_event.is_set():
                        raise SatError("Sincronización cancelada por el usuario.")

                    inicio = datetime.now().isoformat(timespec="seconds")
                    self._p(f"Sincronizando {mes:02d}/{anio} ({i}/{total})...")
                    try:
                        rows = self.client.search_recibidas(page, anio, mes)
                        encontrados_total += len(rows)
                        for r in rows:
                            self.store.upsert_cfdi(r["uuid"], r)
                        nuevos_rows = []
                        for r in rows:
                            c = self.store.get_by_uuid(r["uuid"])
                            if not c.get("pdf_path") or not c.get("xml_path"):
                                nuevos_rows.append(r)

                        if not nuevos_rows:
                            self.store.log_sync(anio, mes, len(rows), 0, 0, "ok",
                                                inicio, datetime.now().isoformat(timespec="seconds"))
                            continue
                        pendientes_total += len(nuevos_rows)
                        self._p(f"{mes:02d}/{anio}: descargando {len(nuevos_rows)} archivo(s)...")
                        for j, row in enumerate(nuevos_rows, 1):
                            if cancel_event and cancel_event.is_set():
                                raise SatError("Sincronización cancelada por el usuario.")

                            uuid_clean = row["uuid"].strip()
                            if not UUID_REGEX.match(uuid_clean):
                                logger.warning("UUID con formato inválido omitido: %r", uuid_clean)
                                continue

                            dest_dir = os.path.join(config.CFDI_FILES_DIR, str(anio), f"{mes:02d}")
                            os.makedirs(dest_dir, exist_ok=True)
                            xml_dest = os.path.join(dest_dir, f"{uuid_clean}.xml")
                            pdf_dest = os.path.join(dest_dir, f"{uuid_clean}.pdf")
                            self._p(f"Descargando {j}/{len(nuevos_rows)} — {mes:02d}/{anio}...")
                            got = self.client.download_files(
                                page, uuid_clean, xml_dest, pdf_dest,
                                row.get("xml_token", ""), row.get("ri_token", ""))
                            if got:
                                self.store.mark_downloaded(uuid_clean,
                                                           xml_path=got.get("xml"),
                                                           pdf_path=got.get("pdf"))
                                descargados_total += 1
                                self._p(f"Archivo {j}/{len(nuevos_rows)} OK — {mes:02d}/{anio} "
                                        f"({descargados_total} total)")
                            else:
                                self._p(f"Archivo {j}/{len(nuevos_rows)} FALLO — {uuid_clean[:8]}...")
                                logger.warning("No se pudo descargar de %s", uuid_clean)
                            if j < len(nuevos_rows):
                                time.sleep(POLITENESS_DELAY)
                        self.store.log_sync(anio, mes, len(rows), len(nuevos_rows), descargados_total,
                                            "ok", inicio, datetime.now().isoformat(timespec="seconds"))
                    except SatError as e:
                        logger.warning("Sync abortado en %02d/%d: %s", mes, anio, e)
                        raise
                    except Exception as e:
                        logger.exception("Sync falló para %02d/%d", mes, anio)
                        self.store.log_sync(anio, mes, 0, 0, 0, f"error: {e}",
                                            inicio, datetime.now().isoformat(timespec="seconds"))
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
        self._p(f"Sync completo: {descargados_total} comprobante(s) descargado(s), "
                f"{max(0, pendientes_total - descargados_total)} fallidos.")
        return {
            "nuevos": descargados_total,
            "descargados": descargados_total,
            "encontrados": encontrados_total,
            "pendientes": max(0, pendientes_total - descargados_total),
            "meses": total,
        }
