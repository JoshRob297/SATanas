import logging

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

HTML = ParseMode.HTML


def get_bot_commands() -> list[BotCommand]:
    return [
        BotCommand("start", "Panel principal"),
        BotCommand("recibos", "Explorador de comprobantes (local)"),
        BotCommand("sync", "Sincronizar con el SAT"),
        BotCommand("help", "Información y ayuda"),
        BotCommand("id", "Obtener ID de usuario/chat"),
        BotCommand("cancelar", "Cancelar operación activa"),
    ]


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def render_header(section: str = "Inicio") -> str:
    return f"🏛️ <b>SATanas</b> | {section}\n━━━━━━━━━━━━━━━━━━━━"


def menu_main(total_cfdis: int = 0) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📂 Ver mis recibos", callback_data="nav:recibos")],
        [InlineKeyboardButton("🔄 Sincronizar con el SAT", callback_data="nav:sync")],
        [
            InlineKeyboardButton("❓ Ayuda", callback_data="nav:ayuda"),
            InlineKeyboardButton("❌ Cancelar", callback_data="nav:cancelar"),
        ],
    ]
    return InlineKeyboardMarkup(kb)


def menu_help() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📂 Ver mis recibos", callback_data="nav:recibos")],
        [InlineKeyboardButton("🔄 Sincronizar", callback_data="nav:sync")],
        [InlineKeyboardButton("« Volver al inicio", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(kb)


def menu_explorador(anios_disponibles: list[int], anio_seleccionado: int, meses_data: list[dict]) -> InlineKeyboardMarkup:
    """Menú tipo Tabs/Calendario: Años arriba como selector, Meses abajo en cuadrícula 4x3."""
    kb = []
    
    # 1. Pestañas de Años (fila superior)
    fila_anios = []
    for y in anios_disponibles:
        txt = f"• {y} •" if y == anio_seleccionado else str(y)
        fila_anios.append(InlineKeyboardButton(txt, callback_data=f"nav:year:{y}"))
    if fila_anios:
        kb.append(fila_anios)

    # 2. Cuadrícula de 12 Meses (4x3)
    por_mes = {m["mes"]: m for m in meses_data if m["anio"] == anio_seleccionado}
    fila_meses = []
    for mes in range(1, 13):
        m_info = por_mes.get(mes)
        nom_mes = _month_name(mes)[:3]
        if m_info and m_info.get("total", 0) > 0:
            txt_btn = f"{nom_mes} ({m_info['total']})"
        else:
            txt_btn = f"{nom_mes}"
        fila_meses.append(InlineKeyboardButton(txt_btn, callback_data=f"nav:ym:{anio_seleccionado}:{mes}:all:0"))
        if len(fila_meses) == 4:
            kb.append(fila_meses)
            fila_meses = []
    if fila_meses:
        kb.append(fila_meses)

    # 3. Acciones de navegación inferior
    kb.append([
        InlineKeyboardButton(f"🔄 Sync {anio_seleccionado}", callback_data=f"nav:sync_year:{anio_seleccionado}"),
        InlineKeyboardButton("« Inicio", callback_data="nav:menu"),
    ])
    return InlineKeyboardMarkup(kb)


def menu_cfdi_list(cfdis: list[dict], anio: int, mes: int, solo_nomina: bool = False, page: int = 0, page_size: int = 6) -> InlineKeyboardMarkup:
    """Lista de comprobantes con paginación limpia y toggle de filtro Nómina / Todos."""
    kb = []
    start = page * page_size
    pagina_cfdis = cfdis[start:start + page_size]
    
    filter_mode = "nom" if solo_nomina else "all"

    for c in pagina_cfdis:
        fecha = _fmt_fecha(c.get("fecha_emision"))
        emisor = (c.get("emisor_nombre") or c.get("emisor_rfc") or "?")[:22]
        total = c.get("total") or "$0"
        es_nomina = "nómina" in (c.get("efecto") or "").lower() or "nomina" in (c.get("efecto") or "").lower()
        badge = "💼" if es_nomina else "📄"
        has_pdf = "✅" if c.get("pdf_path") else "⏳"
        
        # Botón compacto y profesional, pasándole las coordenadas del estado para poder regresar exacto
        label = f"{badge} {fecha} | {emisor} | {total} {has_pdf}"
        kb.append([InlineKeyboardButton(label, callback_data=f"nav:cfdi:{c['uuid']}:{anio}:{mes}:{filter_mode}:{page}")])

    # Fila de paginación
    total_pages = max(1, (len(cfdis) + page_size - 1) // page_size)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("« Ant.", callback_data=f"nav:ym:{anio}:{mes}:{filter_mode}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"nav:noop_page:{page + 1}:{total_pages}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("Sig. »", callback_data=f"nav:ym:{anio}:{mes}:{filter_mode}:{page + 1}"))
    kb.append(nav_row)

    # Botón Toggle de Filtro
    toggle_txt = "🔘 Ver todos los comprobantes" if solo_nomina else "💼 Filtrar solo Nómina"
    toggle_target = "all" if solo_nomina else "nom"
    kb.append([InlineKeyboardButton(toggle_txt, callback_data=f"nav:ym:{anio}:{mes}:{toggle_target}:0")])

    # Botones de navegación inferior
    kb.append([
        InlineKeyboardButton("🔄 Sincronizar mes", callback_data=f"nav:sync_mes:{anio}:{mes}"),
        InlineKeyboardButton("« Volver", callback_data=f"nav:year:{anio}"),
    ])
    return InlineKeyboardMarkup(kb)


def menu_cfdi_detalle(uuid: str, anio: int, mes: int, filter_mode: str, page: int, has_pdf: bool, has_xml: bool) -> InlineKeyboardMarkup:
    """Menú de detalle del CFDI para seleccionar formato de descarga o regresar."""
    kb = []
    
    # Botones individuales
    row_descargas = []
    if has_pdf:
        row_descargas.append(InlineKeyboardButton("📄 Enviar PDF", callback_data=f"nav:send_pdf:{uuid}"))
    if has_xml:
        row_descargas.append(InlineKeyboardButton("📝 Enviar XML", callback_data=f"nav:send_xml:{uuid}"))
    if row_descargas:
        kb.append(row_descargas)
        
    # Enviar Ambos (ZIP)
    if has_pdf and has_xml:
        kb.append([InlineKeyboardButton("📦 Enviar Ambos (ZIP)", callback_data=f"nav:send_zip:{uuid}")])
        
    # Volver a la lista original con las mismas coordenadas
    kb.append([InlineKeyboardButton("« Volver a la lista", callback_data=f"nav:ym:{anio}:{mes}:{filter_mode}:{page}")])
    return InlineKeyboardMarkup(kb)


def menu_mes_vacio(anio: int, mes: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🔄 Descargar comprobantes de este mes", callback_data=f"nav:sync_mes:{anio}:{mes}")],
        [InlineKeyboardButton("« Volver a los meses", callback_data=f"nav:year:{anio}")],
        [InlineKeyboardButton("« Inicio", callback_data="nav:menu")],
    ]
    return InlineKeyboardMarkup(kb)


def menu_cancel_only() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🛑 Cancelar Operación", callback_data="nav:cancelar")],
    ]
    return InlineKeyboardMarkup(kb)


def _month_name(m: int) -> str:
    names = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    return names.get(m, "")


def _fmt_fecha(iso: str) -> str:
    if not iso or len(iso) < 10:
        return "????"
    return f"{iso[8:10]}/{iso[5:7]}"


async def ensure_canvas(update: Update, st, text: str, kb) -> None:
    async with st.canvas_lock:
        ctx = update.get_bot()
        chat_id = update.effective_chat.id
        try:
            if st.canvas_msg_id:
                try:
                    await ctx.edit_message_text(
                        chat_id=chat_id,
                        message_id=st.canvas_msg_id,
                        text=text,
                        parse_mode=HTML,
                        reply_markup=kb,
                    )
                    return
                except BadRequest as e:
                    if "not modified" not in str(e).lower():
                        raise
                    return
        except BadRequest:
            logger.info("Canvas antiguo, recreando (chat %s)", chat_id)
            try:
                await ctx.unpin_chat_message(chat_id=chat_id, message_id=st.canvas_msg_id)
            except Exception:
                pass
            try:
                await ctx.delete_message(chat_id=chat_id, message_id=st.canvas_msg_id)
            except Exception:
                pass
            st.canvas_msg_id = 0
            st.canvas_pinned = False
        msg = await ctx.send_message(chat_id=chat_id, text=text, parse_mode=HTML, reply_markup=kb)
        st.canvas_msg_id = msg.message_id
        try:
            await ctx.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
            st.canvas_pinned = True
        except Exception:
            st.canvas_pinned = False


async def set_working(update: Update, st, label: str = "⏳ Procesando...") -> None:
    """Feedback inmediato tras tocar un botón."""
    try:
        await ensure_canvas(update, st, f"{render_header('Cargando')}\n\n{label}", None)
    except Exception:
        pass


async def alert(query, text: str) -> None:
    try:
        await query.answer(text=text, show_alert=True)
    except Exception:
        pass


async def close_canvas(st, ctx, chat_id: int) -> None:
    if st.canvas_msg_id:
        try:
            await ctx.unpin_chat_message(chat_id=chat_id, message_id=st.canvas_msg_id)
        except Exception:
            pass
        try:
            await ctx.delete_message(chat_id=chat_id, message_id=st.canvas_msg_id)
        except Exception:
            pass
        st.canvas_msg_id = 0
        st.canvas_pinned = False
