import asyncio
import base64
import datetime as dt
import logging
import os
import tempfile
import threading
import zipfile

import httpx
import telegram.error
from telegram import InputFile, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import config, state, ui
from .auth import is_allowed
from .sat.client import CaptchaRelay, Progress, SatError
from .sat.store import CfdiStore
from .sat.sync import SyncManager, rolling_range
from .state import PHASE_IDLE, PHASE_SYNC

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("satanas")

store = CfdiStore(config.CFDI_DB)

SYNC_LOCK = threading.Lock()
_LAST_SYNC_FINISH = ""


async def post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(ui.get_bot_commands())
    except Exception as e:
        logger.warning("Fallo al setear bot commands: %s", e)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    state.reset(update.effective_user.id)
    try:
        tmp = await update.message.reply_text(".", reply_markup=ui.remove_reply_keyboard())
        await tmp.delete()
    except Exception:
        pass
    
    total_local = len(store.get_all())
    text = (
        f"{ui.render_header('Inicio')}\n\n"
        f"Bienvenido. Tu almacén local está disponible.\n"
        f"Tienes <b>{total_local}</b> comprobantes guardados (últimos {config.SYNC_MONTHS_BACK} meses).\n\n"
        f"Selecciona una opción para comenzar:"
    )
    await ui.ensure_canvas(update, state.get(update.effective_user.id), text, ui.menu_main(total_local))


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    text = (
        f"{ui.render_header('Ayuda y Guía')}\n\n"
        f"<b>Funcionalidades:</b>\n"
        f"• <b>Ver mis recibos:</b> Explora comprobantes locales agrupados por año y mes (acceso instantáneo offline).\n"
        f"• <b>Filtro de Nómina:</b> En cualquier mes, alterna entre todos los comprobantes o solo recibos de nómina.\n"
        f"• <b>Sincronizar:</b> Descarga nuevos comprobantes desde el SAT resolviendo el CAPTCHA directamente aquí.\n\n"
        f"<i>Nota: Los archivos se conservan localmente para consulta permanente.</i>"
    )
    await ui.ensure_canvas(update, state.get(update.effective_user.id), text, ui.menu_help())


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    st = state.get(update.effective_user.id)
    state.reset(update.effective_user.id)
    await ui.ensure_canvas(
        update,
        st,
        f"{ui.render_header('Operación Cancelada')}\n\nOperación cancelada. Dashboard listo.",
        ui.menu_main(len(store.get_all())),
    )


async def cmd_recibos(update: Update, ctx: ContextTypes.DEFAULT_TYPE, anio_req: int = None) -> None:
    if not is_allowed(update):
        return
    st = state.get(update.effective_user.id)
    await ui.set_working(update, st)
    meses = store.get_months()
    
    # Obtener años disponibles
    anios = sorted({m["anio"] for m in meses}, reverse=True)
    if not anios:
        current_year = dt.date.today().year
        anios = [current_year, current_year - 1]
        
    anio_activo = anio_req if anio_req and anio_req in anios else anios[0]
    
    total_local = len(store.get_all())
    pendientes = store.count_pendientes()
    extra_pend = f" (⚠️ {pendientes} pendientes de descarga)" if pendientes > 0 else ""
    
    text = (
        f"{ui.render_header('Explorador')}\n\n"
        f"Almacén local: <b>{total_local}</b> comprobantes{extra_pend}.\n"
        f"Pestaña activa: <b>{anio_activo}</b>\n\n"
        f"Selecciona un mes para ver los comprobantes:"
    )
    kb = ui.menu_explorador(anios, anio_activo, meses)
    await ui.ensure_canvas(update, st, text, kb)


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        f"Tu Telegram ID: <code>{update.effective_user.id}</code>\n"
        f"Chat ID: <code>{update.effective_chat.id}</code>",
        parse_mode=ui.HTML,
    )


async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE, months: list = None) -> None:
    if not is_allowed(update):
        return
    q = update.callback_query
    uid = update.effective_user.id
    st = state.get(uid)
    if st.phase == PHASE_SYNC:
        if q:
            await ui.alert(q, "Ya hay una sincronización en curso.")
        return
    if not config.SAT_RFC or not config.SAT_PASSWORD:
        await ui.ensure_canvas(
            update,
            st,
            f"{ui.render_header('Configuración')}\n\nFaltan credenciales en la configuración.",
            ui.menu_main(len(store.get_all())),
        )
        return
    st.phase = PHASE_SYNC
    loop = asyncio.get_running_loop()
    captcha_ready = threading.Event()
    captcha_done = threading.Event()
    holder = {}
    st.holder = holder
    st.captcha_done = captcha_done
    await ui.ensure_canvas(update, st, f"{ui.render_header('Sincronización')}\n\nIniciando conexión con el SAT...", ui.menu_cancel_only())

    async def send_status(text, kb=None):
        try:
            await ui.ensure_canvas(update, st, f"{ui.render_header('Sincronización')}\n\n{text}", kb)
        except Exception as e:
            logger.warning("Status update fail: %s", e)

    async def send_captcha(b64: str):
        raw = b64.split(",", 1)[1] if "," in b64 else b64
        try:
            img = base64.b64decode(raw)
        except Exception as e:
            logger.warning("b64 captcha fail: %s", e)
            img = None
        st.waiting_captcha = True
        await ui.ensure_canvas(
            update,
            st,
            f"{ui.render_header('Verificación')}\n\n<b>Captcha requerido</b>\nEscribe en el chat las letras de la imagen:",
            ui.menu_cancel_only(),
        )
        if img:
            msg = await update.get_bot().send_photo(
                chat_id=update.effective_chat.id, photo=InputFile(img, filename="captcha.png")
            )
            st.captcha_msg_id = msg.message_id

    def job():
        global _LAST_SYNC_FINISH
        mgr = SyncManager(
            config.SAT_RFC,
            config.SAT_PASSWORD,
            progress=Progress(loop, send_status),
            captcha_relay=CaptchaRelay(loop, send_captcha, captcha_ready, captcha_done, holder),
        )
        with SYNC_LOCK:
            result = mgr.run(months, cancel_event=st.cancel_requested)
        _LAST_SYNC_FINISH = dt.datetime.now().isoformat(timespec="seconds")
        return ("ok", result)

    def job_wrapper():
        try:
            return job()
        except SatError as e:
            return ("error", str(e))
        except Exception as e:
            logger.exception("Sync job failed")
            return ("error", f"Error inesperado: {type(e).__name__}")

    st.task = asyncio.create_task(asyncio.to_thread(job_wrapper), name=f"sat-sync-{uid}")
    asyncio.create_task(finish_sync(st.task, uid, st, update))


async def finish_sync(task, uid, st, update):
    try:
        status, payload = await task
    except asyncio.CancelledError:
        state.reset(uid)
        await ui.close_canvas(st, update.get_bot(), update.effective_chat.id)
        return
    finally:
        st.phase = PHASE_IDLE

    if status == "ok":
        descargados = payload.get("descargados", payload.get("nuevos", 0))
        encontrados = payload.get("encontrados", 0)
        pendientes = payload.get("pendientes", 0)
        if descargados == 0 and encontrados == 0:
            resumen = (
                "No se pudo conectar con el SAT o no hay recibos nuevos.\n"
                "Revisa los logs o intentalo de nuevo."
            )
        else:
            resumen = (
                f"Descargados: <b>{descargados}</b>\n"
                f"Encontrados en portal: <b>{encontrados}</b>\n"
            )
            if pendientes > 0:
                resumen += (
                    f"Fallidos: <b>{pendientes}</b>\n"
                    f"Los fallidos se reintentaran en el proximo sync."
                )

        await ui.ensure_canvas(
            update,
            st,
            f"{ui.render_header('Sincronización')}\n\n"
            f"<b>Sync completado</b>\n\n{resumen}",
            ui.menu_main(len(store.get_all())),
        )
    else:
        await ui.ensure_canvas(
            update,
            st,
            f"{ui.render_header('Error')}\n\n<b>Fallo del proceso:</b>\n{payload}",
            ui.menu_main(len(store.get_all())),
        )


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    uid = update.effective_user.id
    st = state.get(uid)
    value = (update.message.text or "").strip()

    if st.waiting_captcha:
        # Limpieza estética del chat: borrar la foto del CAPTCHA y el texto enviado por el usuario
        bot = update.get_bot()
        chat_id = update.effective_chat.id
        if st.captcha_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=st.captcha_msg_id)
            except Exception:
                pass
            st.captcha_msg_id = 0
        try:
            await update.message.delete()
        except Exception:
            pass

        st.holder["captcha"] = value.upper()
        st.waiting_captcha = False
        st.captcha_done.set()
        await ui.ensure_canvas(
            update,
            st,
            f"{ui.render_header('Verificación')}\n\n🟢 Captcha recibido. Validando con el SAT...",
            None,
        )
        return

    if st.phase == PHASE_SYNC:
        await ui.ensure_canvas(
            update,
            st,
            f"{ui.render_header('Sincronización')}\n\nHay una sincronización en curso...",
            ui.menu_cancel_only(),
        )
        return

    await ui.ensure_canvas(
        update,
        st,
        f"{ui.render_header('Inicio')}\n\nUsa los botones de abajo para navegar:",
        ui.menu_main(len(store.get_all())),
    )


async def handle_nav(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    q = update.callback_query
    uid = q.from_user.id
    st = state.get(uid)
    data = q.data
    await q.answer()

    if data == "nav:menu":
        total_local = len(store.get_all())
        text = (
            f"{ui.render_header('Inicio')}\n\n"
            f"Bienvenido. Tu almacén local está disponible.\n"
            f"Tienes <b>{total_local}</b> comprobantes guardados (últimos {config.SYNC_MONTHS_BACK} meses).\n\n"
            f"Selecciona una opción para comenzar:"
        )
        await ui.ensure_canvas(update, st, text, ui.menu_main(total_local))
        return
    if data == "nav:ayuda":
        await cmd_help(update, ctx)
        return
    if data == "nav:recibos":
        await cmd_recibos(update, ctx)
        return
    if data == "nav:sync":
        await cmd_sync(update, ctx)
        return
    if data == "nav:cancelar":
        await cmd_cancel(update, ctx)
        return
    if data.startswith("nav:noop_page:"):
        parts = data.split(":")
        await q.answer(f"Página {parts[2]} de {parts[3]}", show_alert=False)
        return
    if data.startswith("nav:sync_mes:"):
        parts = data.split(":")
        anio, mes = int(parts[2]), int(parts[3])
        await cmd_sync(update, ctx, months=[(anio, mes)])
        return
    if data.startswith("nav:sync_year:"):
        anio = int(data.split(":")[2])
        # Sincronizar todos los meses del año indicado
        meses_año = [(anio, m) for m in range(1, 13)]
        await cmd_sync(update, ctx, months=meses_año)
        return
    if data.startswith("nav:year:"):
        anio = int(data.split(":", 2)[2])
        await cmd_recibos(update, ctx, anio_req=anio)
        return
    if data.startswith("nav:ym:"):
        # Formato: nav:ym:<anio>:<mes>:<filter>:<page>
        parts = data.split(":")
        anio = int(parts[2])
        mes = int(parts[3])
        solo_nomina = (parts[4] == "nom") if len(parts) > 4 else False
        page = int(parts[5]) if len(parts) > 5 else 0
        
        await ui.set_working(update, st)
        cfdis = store.get_all(anio, mes, solo_nomina=solo_nomina)
        
        nom_mes = ui._month_name(mes)
        filtro_txt = "💼 Solo Nómina" if solo_nomina else "📄 Todos los comprobantes"
        
        if not cfdis:
            if solo_nomina:
                text = (
                    f"{ui.render_header(f'{nom_mes} {anio}')}\n\n"
                    f"Filtro activo: <b>{filtro_txt}</b>\n"
                    f"No se encontraron recibos de nómina para este mes."
                )
                kb = ui.menu_cfdi_list(cfdis, anio, mes, solo_nomina=True, page=0)
            else:
                text = (
                    f"{ui.render_header(f'{nom_mes} {anio}')}\n\n"
                    f"No hay comprobantes guardados para este mes."
                )
                kb = ui.menu_mes_vacio(anio, mes)
            await ui.ensure_canvas(update, st, text, kb)
            return

        text = (
            f"{ui.render_header(f'{nom_mes} {anio}')}\n\n"
            f"Mostrando: <b>{filtro_txt}</b> ({len(cfdis)} comprobantes)\n"
            f"<i>Toca un comprobante para descargarlo:</i>"
        )
        await ui.ensure_canvas(update, st, text, ui.menu_cfdi_list(cfdis, anio, mes, solo_nomina=solo_nomina, page=page))
        return

    if data.startswith("nav:cfdi:"):
        # Formato: nav:cfdi:{uuid}:{anio}:{mes}:{filter}:{page}
        parts = data.split(":")
        uuid = parts[2]
        anio = int(parts[3])
        mes = int(parts[4])
        filter_mode = parts[5]
        page_num = int(parts[6])

        c = store.get_by_uuid(uuid)
        if not c:
            await ui.alert(q, "Comprobante no encontrado.")
            return

        pdf_path = c.get("pdf_path")
        xml_path = c.get("xml_path")
        has_pdf = bool(pdf_path and os.path.exists(pdf_path))
        has_xml = bool(xml_path and os.path.exists(xml_path))

        fecha = (c.get("fecha_emision") or "fecha")[:10]
        emisor = (c.get("emisor_nombre") or c.get("emisor_rfc") or "?")
        total = c.get("total") or "$0"
        efecto = c.get("efecto") or "Desconocido"

        pdf_status = "✅ Descargado" if has_pdf else "❌ No disponible"
        xml_status = "✅ Descargado" if has_xml else "❌ No disponible"

        text = (
            f"{ui.render_header('Detalle del CFDI')}\n\n"
            f"🏢 <b>Emisor:</b> {emisor}\n"
            f"📅 <b>Fecha:</b> {fecha}\n"
            f"💵 <b>Total:</b> {total}\n"
            f"🏷️ <b>Tipo:</b> {efecto}\n\n"
            f"<b>Archivos locales:</b>\n"
            f"• PDF: {pdf_status}\n"
            f"• XML: {xml_status}\n\n"
            f"Selecciona los formatos que deseas recibir:"
        )

        kb = ui.menu_cfdi_detalle(uuid, anio, mes, filter_mode, page_num, has_pdf, has_xml)
        await ui.ensure_canvas(update, st, text, kb)
        return

    if data.startswith("nav:send_"):
        # Formato: nav:send_[pdf|xml|zip]:{uuid}
        parts = data.split(":")
        action = parts[1].replace("send_", "")
        uuid = parts[2]

        c = store.get_by_uuid(uuid)
        if not c:
            await ui.alert(q, "Comprobante no encontrado.")
            return

        chat_id = update.effective_chat.id
        bot = update.get_bot()
        pdf_path = c.get("pdf_path")
        xml_path = c.get("xml_path")

        fecha = (c.get("fecha_emision") or "fecha")[:10]
        emisor_raw = (c.get("emisor_nombre") or c.get("emisor_rfc") or "CFDI")
        emisor = "".join(ch for ch in emisor_raw if ch.isalnum() or ch in " _-")[:30].strip() or "CFDI"
        es_nom = "nómina" in (c.get("efecto") or "").lower() or "nomina" in (c.get("efecto") or "").lower()
        prefijo = "Nomina" if es_nom else "Factura"
        uuid_short = uuid[:8]

        if action == "pdf":
            if pdf_path and os.path.exists(pdf_path):
                await q.answer("Enviando PDF...")
                try:
                    with open(pdf_path, "rb") as fh:
                        await bot.send_document(
                            chat_id=chat_id,
                            document=fh,
                            filename=f"{prefijo}_{emisor}_{fecha}_{uuid_short}.pdf",
                            caption=f"📄 {prefijo} - {emisor} ({fecha})"
                        )
                except Exception as e:
                    logger.warning("Fallo al enviar PDF %s: %s", uuid, e)
                    await ui.alert(q, "Fallo al enviar el PDF.")
            else:
                await ui.alert(q, "El PDF no está disponible localmente.")
            return

        if action == "xml":
            if xml_path and os.path.exists(xml_path):
                await q.answer("Enviando XML...")
                try:
                    with open(xml_path, "rb") as fh:
                        await bot.send_document(
                            chat_id=chat_id,
                            document=fh,
                            filename=f"{prefijo}_{emisor}_{fecha}_{uuid_short}.xml",
                            caption=f"📝 XML - {emisor} ({fecha})"
                        )
                except Exception as e:
                    logger.warning("Fallo al enviar XML %s: %s", uuid, e)
                    await ui.alert(q, "Fallo al enviar el XML.")
            else:
                await ui.alert(q, "El XML no está disponible localmente.")
            return

        if action == "zip":
            has_pdf = bool(pdf_path and os.path.exists(pdf_path))
            has_xml = bool(xml_path and os.path.exists(xml_path))
            if has_pdf and has_xml:
                await q.answer("Creando y enviando ZIP...")

                zip_filename = f"CFDI_{emisor}_{fecha}_{uuid_short}.zip"
                with tempfile.TemporaryDirectory() as tmpdir:
                    zip_filepath = os.path.join(tmpdir, zip_filename)
                    try:
                        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            zipf.write(pdf_path, arcname=f"{prefijo}_{emisor}_{fecha}_{uuid_short}.pdf")
                            zipf.write(xml_path, arcname=f"{prefijo}_{emisor}_{fecha}_{uuid_short}.xml")
                        with open(zip_filepath, "rb") as fh:
                            await bot.send_document(
                                chat_id=chat_id,
                                document=fh,
                                filename=zip_filename,
                                caption=f"📦 CFDI Completo (ZIP) - {emisor} ({fecha})"
                            )
                    except Exception as e:
                        logger.warning("Fallo al crear o enviar ZIP %s: %s", uuid, e)
                        await ui.alert(q, "Fallo al crear o enviar el archivo ZIP.")
            else:
                await ui.alert(q, "Ambos archivos deben estar disponibles localmente para crear el ZIP.")
            return

    await q.answer()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador global de errores para silenciar caídas de red transitorias."""
    err = context.error
    if isinstance(err, (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError, telegram.error.NetworkError)):
        logger.debug("Error de red transitorio en Telegram: %s", err)
        return
    logger.error("Excepción no controlada en handler de Telegram: %s", err, exc_info=err)


def main() -> None:
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancelar", cmd_cancel))
    app.add_handler(CommandHandler("recibos", cmd_recibos))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CallbackQueryHandler(handle_nav, pattern=r"^nav:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("SATanas bot iniciado (almacén: %s)", config.CFDI_DB)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
