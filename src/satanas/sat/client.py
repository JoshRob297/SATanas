import asyncio
import base64
import json
import logging
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.parse

import httpx
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, sync_playwright

from .. import config
from ..selectors import SELECTORS

logger = logging.getLogger(__name__)


class SatError(Exception):
    pass


class Progress:
    def __init__(self, loop, send):
        self.loop = loop
        self.send = send

    def post(self, text: str, kb=None):
        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.send(text, kb), self.loop)


class CaptchaRelay:
    def __init__(self, loop, send_photo, ready: threading.Event, done: threading.Event, holder: dict, timeout: int = 180):
        self.loop = loop
        self.send_photo = send_photo
        self.ready = ready
        self.done = done
        self.holder = holder
        self.timeout = timeout

    def __call__(self, b64: str) -> str:
        self.holder.pop("captcha", None)
        self.done.clear()
        self.ready.clear()
        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.send_photo(b64), self.loop)
        self.ready.set()
        if not self.done.wait(timeout=self.timeout):
            raise SatError("Tiempo agotado esperando la respuesta del captcha.")
        return self.holder.get("captcha", "")


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CfdiPortalClient:
    """Cliente del Portal Contribuyentes CFDI (facturaelectronica.sat.gob.mx)."""

    def __init__(self, rfc: str, password: str, progress: Progress = None,
                 captcha_relay: CaptchaRelay = None):
        self.rfc = rfc.upper().strip()
        self.password = password
        self.progress = progress
        self.captcha_relay = captcha_relay
        self.sel = SELECTORS["portal_cfdi"]
        self.storage_state = config.CFDI_STATE_FILE if os.path.exists(config.CFDI_STATE_FILE) else None

    def _p(self, text: str, kb=None):
        if self.progress:
            self.progress.post(text, kb)

    def new_browser(self, playwright):
        state_path = config.CFDI_STATE_FILE if os.path.exists(config.CFDI_STATE_FILE) else None
        browser = playwright.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage", "--no-sandbox"],
        )
        ctx = browser.new_context(
            storage_state=state_path,
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            ignore_https_errors=True,
            accept_downloads=True,
        )
        # Parche permanente para que el SAT no atore las descargas consecutivas al reusar el mismo target de window.open
        ctx.add_init_script("""
            window._orig_open = window.open;
            window.open = function(url, name, specs) {
                return window._orig_open(url, "_blank", specs);
            };
        """)
        return browser, ctx

    def ensure_logged_in(self, page) -> None:
        self._p("Conectando con el Portal CFDI del SAT...")
        self._goto_portal(page)
        if page.query_selector(self.sel["login_ready"]):
            self._login(page)

    def _goto_portal(self, page) -> None:
        """Navega al portal; si la sesión expiró (lofc.jsp / logout / home sat),
        limpia cookies stale y fuerza la redirección limpia al login de NIDP."""
        for attempt in range(8):
            try:
                page.goto(self.sel["url"], timeout=45000, wait_until="domcontentloaded")
            except PlaywrightError as e:
                logger.warning("Fallo al navegar a portal (intento %d/8): %s", attempt + 1, e)
                page.wait_for_timeout(2000)
                continue

            for _ in range(10):
                try:
                    if page.query_selector(self.sel["login_ready"]):
                        return
                except PlaywrightError:
                    pass  # navegación en curso, reintentar
                page.wait_for_timeout(1000)
            # Sin #rfc: ¿estamos en el logout (lofc.jsp), logoutWreply, o portal del SAT general?
            try:
                curr_url = page.url.lower()
                if "lofc" in curr_url or "logout" in curr_url or "www.sat.gob.mx" in curr_url:
                    logger.info("Sesión muerta detectada en _goto_portal (%s). Limpiando cookies...", curr_url[:80])
                    page.context.clear_cookies()
                    if os.path.exists(config.CFDI_STATE_FILE):
                        try:
                            os.remove(config.CFDI_STATE_FILE)
                        except OSError:
                            pass
                    # Sin cookies, navegar al portal fuerza la redirección limpia al login NIDP
                    page.goto(self.sel["url"], timeout=30000, wait_until="domcontentloaded")
                    for _ in range(10):
                        try:
                            if page.query_selector(self.sel["login_ready"]):
                                return
                        except PlaywrightError:
                            pass
                        page.wait_for_timeout(1000)
                else:
                    # Estamos en el portal autenticado (dashboard) o en otra página válida
                    return
            except PlaywrightError:
                continue
        raise SatError("No se pudo acceder al formulario de acceso del SAT tras varios reintentos.")

    def _login(self, page) -> None:
        self._p("Autenticando en SAT (CIEC)...")
        page.wait_for_selector(self.sel["rfc_input"], timeout=30000)

        for attempt in range(1, self.sel["max_attempts"] + 1):
            # Guard de éxito tardío: si el intento anterior navegó lentamente y ya
            # salimos de cfdiau, el login YA funcionó — guardar y continuar.
            if attempt > 1:
                try:
                    page.wait_for_url(lambda url: "cfdiau" not in url, timeout=3000)
                    page.context.storage_state(path=config.CFDI_STATE_FILE)
                    self._p("Sesion iniciada correctamente.")
                    return
                except PlaywrightTimeout:
                    pass  # seguimos en el login; proceder con el intento
                except PlaywrightError:
                    pass

            # Re-llenar RFC y password en cada intento (el postback del SAT vacía los campos)
            try:
                page.fill(self.sel["rfc_input"], self.rfc)
                page.fill(self.sel["pass_input"], self.password)
            except PlaywrightTimeout:
                # La página cambió a mitad del retry; re-evaluar antes de crashear
                try:
                    page.wait_for_url(lambda url: "cfdiau" not in url, timeout=3000)
                    page.context.storage_state(path=config.CFDI_STATE_FILE)
                    self._p("Sesion iniciada correctamente.")
                    return
                except (PlaywrightTimeout, PlaywrightError):
                    pass
                raise SatError("El formulario de acceso desapareció durante el retry.")

            img = page.evaluate(
                """() => {
                    const el = document.querySelector('#divCaptcha img');
                    return el ? el.src : '';
                }"""
            )
            if not img:
                raise SatError("No se encontró la imagen del captcha.")
            if not self.captcha_relay:
                raise SatError("Se requiere captcha pero no hay canal para resolverlo.")

            captcha = self.captcha_relay(img)
            if not captcha.strip():
                raise SatError("Respuesta de captcha vacía.")

            page.fill(self.sel["captcha_input"], captcha.strip().upper())
            try:
                # no_wait_after: el click no debe esperar la navegación (el portal
                # NO navega si el captcha es rechazado) — la detección la hace el
                # polling de abajo.
                page.click(self.sel["submit_btn"], timeout=30000, no_wait_after=True)
            except PlaywrightTimeout:
                pass  # click hecho pero sin navegación; el polling decide

            # Polling: éxito cuando la URL sale de cfdiau; fallo rápido cuando el
            # NIDP re-renderiza con error visible (no esperar el timeout completo).
            outcome = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    if "cfdiau" not in page.url:
                        outcome = "ok"
                        break
                    body = page.inner_text("body") or ""
                    if "no válido" in body.lower() or "incorrect" in body.lower():
                        outcome = "bad"
                        break
                except PlaywrightError:
                    pass  # navegación en curso
                page.wait_for_timeout(2000)

            # Check final: si el deadline venció mientras la cadena SAML seguía
            # navegando lentamente, no declarar fallo sin revisar la URL una vez más.
            if outcome is None:
                try:
                    if "cfdiau" not in page.url:
                        outcome = "ok"
                except PlaywrightError:
                    pass

            if outcome == "ok":
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
                page.context.storage_state(path=config.CFDI_STATE_FILE)
                self._p("Sesion iniciada correctamente.")
                return

            body = ""
            try:
                body = page.inner_text("body")
            except Exception:
                pass
            body_clean = " ".join(body.split())[:200]
            logger.warning("Intento %d/%d fallido en NIDP. Texto página: %s", attempt, self.sel["max_attempts"], body_clean)
            if attempt < self.sel["max_attempts"]:
                self._p(f"Captcha rechazado (intento {attempt}/{self.sel['max_attempts']}). Generando nuevo captcha...")
                page.wait_for_timeout(1000)
                continue
            raise SatError("Credenciales o captcha rechazados por el SAT.")

        raise SatError("Número máximo de intentos de captcha agotado.")

    def search_recibidas(self, page, anio: int, mes: int) -> list[dict]:
        """Busca facturas recibidas de un año/mes. Devuelve metadatos + tokens de descarga."""
        self._p(f"Consultando {mes:02d}/{anio} en el portal...")
        page.goto(f"{self.sel['url']}{self.sel['consulta_receptor']}",
                  timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        try:
            page.click(self.sel["radio_fecha"], timeout=15000)
        except PlaywrightTimeout:
            # Si el radio de búsqueda no aparece, la sesión murió (el visor o el
            # portal nos redirigieron fuera) — abortar el sync completo.
            try:
                curr = page.url.lower()
            except Exception:
                curr = ""
            logger.warning("Radio de búsqueda no disponible (url=%s) — sesión perdida", curr[:80])
            raise SatError("Sesión del SAT perdida. Vuelve a iniciar sesión (/sync).")
        page.select_option(self.sel["sel_anio"], str(anio))
        page.select_option(self.sel["sel_mes"], f"{mes:02d}")
        page.select_option(self.sel["sel_estado"], self.sel["estado_vigente"])
        page.click(self.sel["btn_buscar"])
        # La tabla se llena vía AJAX (UpdatePanel); esperar a que aparezcan resultados o aviso de vacío
        try:
            page.wait_for_selector("input.ListaFolios, #ctl00_MainContent_LblError, #ctl00_MainContent_divSinResultados", timeout=25000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(1000)

        rows = []
        seen: set[str] = set()
        for tr in page.query_selector_all("table tr"):
            cb = tr.query_selector("input.ListaFolios")
            if not cb:
                continue
            tds = [td.inner_text().strip() for td in tr.query_selector_all("td")]
            if len(tds) < 20:
                continue  # fila de tabla secundaria/duplicada
            uuid = tds[8]
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)
            tok_el = tr.query_selector('input[name="ListaFoliosUrl"]')
            token = (tok_el.get_attribute("value") or "") if tok_el else ""
            # Tokens de descarga por fila: XML (RecuperaCfdi) y Representación
            # Impresa (PageMethod) — extraídos del onclick de los botones de fila.
            xml_token = ""
            ri_token = ""
            try:
                desc = tr.query_selector("#BtnDescarga")
                oc = desc.get_attribute("onclick") or "" if desc else ""
                m = re.search(r"Datos=([^'\"\\s]+)", oc)
                if m:
                    xml_token = m.group(1)
            except Exception:
                pass
            try:
                ri = tr.query_selector("#BtnRI")
                oc = ri.get_attribute("onclick") or "" if ri else ""
                m = re.search(r"recuperaRepresentacionImpresa\('([^']+)'\)", oc)
                if m:
                    ri_token = m.group(1)
            except Exception:
                pass
            rows.append({
                "uuid": uuid,
                "cb_val": (cb.get_attribute("value") or "").strip(),
                "token": token,
                "xml_token": xml_token,
                "ri_token": ri_token,
                "emisor_rfc": tds[9],
                "emisor_nombre": tds[10],
                "fecha_emision": tds[13],
                "fecha_cert": tds[14],
                "total": tds[16],
                "efecto": tds[17],
                "anio": anio,
                "mes": mes,
            })
        return rows

    def download_files(self, page, uuid: str, xml_dest: str, pdf_dest: str,
                       xml_token: str = "", ri_token: str = "") -> dict:
        """Descarga XML y PDF de un CFDI.
        - XML: click físico en el botón #BtnDescarga de la fila activa en la tabla.
        - PDF: PageMethod RecuperaRepresentacionImpresa → URL → httpx.
        Devuelve {xml: path, pdf: path}. Registra archivos ya en disco sin re-descargar.
        """
        out: dict[str, str] = {}

        # Registrar archivos ya existentes en disco para no re-descargarlos
        if os.path.exists(xml_dest) and os.path.getsize(xml_dest) > 0:
            try:
                with open(xml_dest, "rb") as fh:
                    head = fh.read(100)
                if head[:5] == b"<?xml" or b"<cfdi:Comprobante" in head:
                    out["xml"] = xml_dest
                    logger.info("XML %s ya existe en disco (%d bytes), omitiendo", uuid, os.path.getsize(xml_dest))
            except Exception:
                pass

        if os.path.exists(pdf_dest) and os.path.getsize(pdf_dest) > 0:
            try:
                with open(pdf_dest, "rb") as fh:
                    head = fh.read(4)
                if head == b"%PDF":
                    out["pdf"] = pdf_dest
                    logger.info("PDF %s ya existe en disco (%d bytes), omitiendo", uuid, os.path.getsize(pdf_dest))
            except Exception:
                pass

        if "xml" in out and "pdf" in out:
            return out

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        }
        base = f"{self.sel['url']}{self.sel['consulta_receptor']}"
        json_h = dict(headers)
        json_h.update({
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": base,
        })
        referer_h = dict(headers)
        referer_h["Referer"] = base

        try:
            cookies = {c["name"]: c["value"] for c in page.context.cookies()}
        except Exception:
            cookies = {}

        # ── XML: click físico en el botón #BtnDescarga de la fila ──────────
        # Es el único flujo que el SAT acepta para descarga individual.
        # Playwright atrapa el evento de descarga nativo que genera el portal.
        # Con el init_script (_blank), cada descarga ocurre en su propio contexto limpio sin colisión de popups.
        if "xml" not in out:
            try:
                page.wait_for_timeout(1000)  # Pausa breve de cortesía
                btn = page.query_selector(f'tr:has(input.ListaFolios[value="{uuid}"]) #BtnDescarga')
                if btn:
                    logger.info("XML %s: click físico en #BtnDescarga", uuid)
                    with page.expect_download(timeout=25000) as dl_info:
                        btn.click()
                    dl = dl_info.value
                    dl.save_as(xml_dest)
                    with open(xml_dest, "rb") as fh:
                        head = fh.read(100)
                    if head[:5] == b"<?xml" or b"<cfdi:Comprobante" in head:
                        out["xml"] = xml_dest
                        logger.info("XML %s OK via click físico (%d bytes)", uuid, os.path.getsize(xml_dest))
                    else:
                        logger.warning("XML %s: click físico no produjo XML: %s", uuid, head[:60])
                else:
                    logger.warning("XML %s: #BtnDescarga no encontrado en tabla activa", uuid)
            except Exception as e:
                logger.warning("XML %s: click físico fallo: %s", uuid, str(e)[:300])

        # 2) PDF: PageMethod RecuperaRepresentacionImpresa → URL del PDF
        if ri_token and "pdf" not in out:
            try:
                with httpx.Client(verify=ctx, follow_redirects=True, timeout=60) as client:
                    resp = client.post(f"{base}/RecuperaRepresentacionImpresa",
                                       content=json.dumps({"datos": ri_token}),
                                       cookies=cookies, headers=json_h)
                body = resp.content
                ctype = (resp.headers.get("content-type") or "").lower()
                if body[:4] == b"%PDF" or "pdf" in ctype:
                    with open(pdf_dest, "wb") as fh:
                        fh.write(body)
                    out["pdf"] = pdf_dest
                    logger.info("PDF %s descargado directo (%d bytes)", uuid, len(body))
                elif body:
                    msg = ""
                    data = None
                    try:
                        data = json.loads(body)
                        if isinstance(data, dict) and data.get("d"):
                            data = data["d"]
                        msg = str(data)
                    except Exception:
                        msg = body.decode("utf-8", "replace")[:300]
                    if msg and msg[:4] == "%PDF" or (msg and "JVBERi" in msg[:20]):
                        try:
                            pdf = base64.b64decode(msg)
                        except Exception:
                            pdf = msg.encode()
                        if pdf[:4] == b"%PDF":
                            with open(pdf_dest, "wb") as fh:
                                fh.write(pdf)
                            out["pdf"] = pdf_dest
                            logger.info("PDF %s via base64 (%d bytes)", uuid, len(pdf))
                            return out
                    # La URL viene en data["d"] completa — usarla DIRECTA (sin
                    # regex que la trunque). Puede ser absoluta, /relativa o
                    # página relativa (ej. "RepresentacionImpresa.aspx?Datos=").
                    url = ""
                    if isinstance(data, str) and (data.startswith("http") or data.startswith("/")):
                        url = data.strip().strip('"')
                    elif isinstance(data, str) and ".aspx" in data[:60]:
                        url = data.strip().strip('"')
                    else:
                        m = re.search(r'https?://\S+', msg)
                        if m:
                            url = m.group(0).strip('"')
                    if url:
                        url = urllib.parse.quote(url, safe=":/?&=%+")
                        if not url.startswith("http"):
                            base_url = self.sel["url"].rstrip("/")
                            url = f"{base_url}/{url.lstrip('/')}"
                        logger.info("PDF %s: PageMethod devolvió URL %s", uuid, url[:150])
                        # La URL generada expira en segundos: GET httpx INMEDIATO
                        # con Referer (anti-hotlinking del portal).
                        with httpx.Client(verify=ctx, follow_redirects=True, timeout=60) as client:
                            r2 = client.get(url, cookies=cookies, headers=referer_h)
                        if r2.content[:4] == b"%PDF":
                            with open(pdf_dest, "wb") as fh:
                                fh.write(r2.content)
                            out["pdf"] = pdf_dest
                            logger.info("PDF %s via URL (%d bytes)", uuid, len(r2.content))
                        else:
                            # Fallback: tab (popup del portal) con espera corta
                            captured = self._open_tab_capture(page, url, 10000, referer=base)
                            if captured and captured[:4] == b"%PDF":
                                with open(pdf_dest, "wb") as fh:
                                    fh.write(captured)
                                out["pdf"] = pdf_dest
                                logger.info("PDF %s via URL+tab (%d bytes)", uuid, len(captured))
                            else:
                                logger.warning("PDF %s: URL no devolvió PDF (http=%s %s, tab=%s)",
                                               uuid, r2.status_code,
                                               r2.headers.get("content-type", ""),
                                               captured[:30])
                        return out
                    logger.warning("PDF %s: respuesta PageMethod inesperada: %s",
                                   uuid, msg[:200])
            except Exception as e:
                logger.warning("PDF %s: fallo: %s", uuid, str(e)[:200])

        return out

    def _open_tab_capture(self, page, url: str, wait_ms: int = 45000, referer: str = "") -> bytes:
        """Abre una URL en pestaña aislada y captura el archivo descargado por
        el JS del portal (loader → download real). Loguea cada respuesta y
        download observado. Devuelve los bytes del archivo o b''."""
        captured: dict = {}
        dl_event = threading.Event()
        tab = page.context.new_page()
        if referer:
            try:
                tab.set_extra_http_headers({"Referer": referer})
            except Exception:
                pass

        def on_response(resp):
            try:
                ctype = (resp.headers.get("content-type") or "").lower()
                captured.setdefault("responses", []).append(
                    {"url": resp.url[:120], "ctype": ctype,
                     "status": resp.status})
                if "xml" in ctype or "pdf" in ctype or "octet-stream" in ctype:
                    body = resp.body()
                    captured["body"] = body
                    captured["body_ctype"] = ctype
                    dl_event.set()
                elif "error" in resp.url.lower() or resp.status >= 400:
                    captured["err_page"] = resp.body()[:2000]
                    captured["err_url"] = str(resp.url)[:150]
            except Exception:
                pass

        def on_download(download):
            captured["download"] = download
            dl_event.set()

        tab.on("response", on_response)
        tab.on("download", on_download)
        try:
            try:
                tab.goto(url, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            for _ in range(wait_ms // 1000):
                if dl_event.is_set():
                    break
                tab.wait_for_timeout(1000)
            logger.info("Tab capture %s: %d resp(s) %s", url[:90],
                        len(captured.get("responses", [])),
                        [f"{r['status']} {r['ctype']} {r['url'][:60]}"
                         for r in captured.get("responses", [])])
            body = captured.get("body")
            err = captured.get("err_page")
            if err:
                txt = re.sub(rb"<[^>]+>", b" ", err)
                txt = b" ".join(txt.split())[:500]
                logger.info("Tab capture %s: página de error (%s): %s | crudo=%s",
                            url[:60], captured.get("err_url", ""), txt,
                            err[:120])
            # Si el body capturado es un loader HTML, buscar la URL real del archivo
            if body and not (body[:5] == b"<?xml" or body[:4] == b"%PDF" or
                             b"<cfdi:Comprobante" in body[:3000]):
                m = re.search(rb'https?://[^\s"\'<>]+\.(?:xml|pdf)', body) or \
                    re.search(rb'/[A-Za-z0-9/_.-]+\.(?:xml|pdf)', body)
                if m:
                    u = m.group(0).decode("utf-8", "replace").strip('"\'')
                    if u.startswith("/"):
                        u = f"{self.sel['url'].rstrip('/')}{u}"
                    try:
                        tab.goto(u, timeout=30000, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    for _ in range(15):
                        if dl_event.is_set():
                            break
                        tab.wait_for_timeout(1000)
                    body = captured.get("body")
            if body and (body[:5] == b"<?xml" or body[:4] == b"%PDF" or
                         b"<cfdi:Comprobante" in body[:3000]):
                return body
            if "download" in captured:
                with tempfile.NamedTemporaryFile(suffix=".bin", delete=True) as tmp:
                    try:
                        captured["download"].save_as(tmp.name)
                        tmp.seek(0)
                        return tmp.read()
                    except Exception as e:
                        logger.warning("Tab capture: download save falló: %s", e)
                        return b""
            return b""
        finally:
            try:
                tab.remove_listener("response", on_response)
                tab.remove_listener("download", on_download)
                tab.close()
            except Exception:
                pass
