import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler

from services.sheets import (
    get_pendiente,
    mark_pendiente_ok,
    append_gasto,
    upsert_mapping,
    get_unique_categories,
    get_mapping, get_usuarios_autorizados, upsert_usuario
)
def load_env():
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

_auth_cache = {"ids": set(), "ts": 0}

def is_authorized(update: Update) -> bool:
    import time
    ahora = time.time()
    
    if ahora - _auth_cache["ts"] > 120:
        _auth_cache["ids"] = get_usuarios_autorizados()
        _auth_cache["ts"] = ahora
    
    return update.effective_chat.id in _auth_cache["ids"]

async def request_access(update: Update):
    """Notifica al admin cuando alguien desconocido escribe."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    nombre = user.full_name or user.username or str(chat_id)

    # Guardamos como PENDIENTE
    upsert_usuario(chat_id, nombre, "PENDIENTE")

    # Notificamos al admin con botones
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"✅ Aprobar a {nombre}", 
                callback_data=f"AUTH_OK|{chat_id}|{nombre}"
            )
        ],
        [
            InlineKeyboardButton(
                f"❌ Rechazar", 
                callback_data=f"AUTH_DENY|{chat_id}|{nombre}"
            )
        ]
    ])

    bot = update.get_bot()
    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"🔔 <b>Solicitud de acceso:</b>\n"
             f"👤 {nombre}\n"
             f"🆔 <code>{chat_id}</code>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

    await update.message.reply_text(
        "⏳ Tu solicitud fue enviada al administrador. "
        "Te avisaré cuando seas aprobado."
    )


def build_category_keyboard(email_id):
    cats = get_unique_categories()
    
    keyboard = []
    row = []
    
    for cat in cats:
        btn = InlineKeyboardButton(cat, callback_data=f"CAT|{email_id}|{cat}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
        
    keyboard.append([
        InlineKeyboardButton("➕ Nueva Categoría...", callback_data=f"NEW_CAT|{email_id}")
    ])
    
    keyboard.append([
        InlineKeyboardButton("❌ Cancelar", callback_data=f"IGNORE|{email_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split("|")
    action = parts[0]

    # AUTH actions no requieren estar autorizado previamente
    if action in ("AUTH_OK", "AUTH_DENY"):
        await query.answer()

        target_chat_id = int(parts[1])
        nombre = parts[2] if len(parts) > 2 else str(target_chat_id)

        if action == "AUTH_OK":
            upsert_usuario(target_chat_id, nombre, "AUTORIZADO")
            _auth_cache["ts"] = 0  # fuerza refresco inmediato
            await query.edit_message_text(f"✅ {nombre} autorizado.")
            await context.bot.send_message(
                chat_id=target_chat_id,
                text="✅ ¡Acceso aprobado! Ya puedes usar el bot."
            )
        else:
            upsert_usuario(target_chat_id, nombre, "RECHAZADO")
            await query.edit_message_text(f"❌ {nombre} rechazado.")
        return

    # El resto de acciones sí requieren autorización
    if not is_authorized(update):
        await query.answer("⛔ No autorizado.", show_alert=True)
        return

    await query.answer()
    
    email_id = parts[1] if len(parts) > 1 else None

    # --- CASO 1: DESCARTAR ---
    if action == "IGNORE":
        await query.edit_message_text(text="❌ Gasto descartado.")
        return

    # --- CASO 2: MANTENER NOMBRE ORIGINAL ---
    if action == "KEEP":
        await query.edit_message_text(text="⏳ Cargando categorías...")

        row_idx, p = get_pendiente(email_id)
        if not p:
            await query.edit_message_text(text="⚠️ Error: No encontré el gasto en Pendientes.")
            return
            
        alias_original = p["comercio_raw"]
        
        context.user_data["temp_alias"] = alias_original
        context.user_data["esperando_categoria_id"] = email_id
        
        reply_markup = build_category_keyboard(email_id) 

        await query.edit_message_text(
            text=f"✅ Alias: <b>{alias_original}</b>\n\n📂 Selecciona la <b>CATEGORÍA</b>:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return
    
    # --- CASO: NUEVA CATEGORÍA (Botón ➕) ---
    if action == "NEW_CAT":
        await query.edit_message_text(text="✍️ Escribe el nombre de la <b>NUEVA CATEGORÍA</b>:", parse_mode="HTML")
        context.user_data["esperando_nueva_cat_id"] = email_id
        context.user_data["mensaje_instruccion_id"] = query.message.message_id
        return

    # --- CASO 3: CAMBIAR NOMBRE MANUALMENTE ---
    if action == "OTRO":
        await query.edit_message_text(text="🔍 Preparando...")
        
        context.user_data["esperando_alias_id"] = email_id
        context.user_data["mensaje_instruccion_id"] = query.message.message_id
        
        row_idx, p = get_pendiente(email_id)
        nombre_banco = p["comercio_raw"] if p else "este comercio"

        await query.edit_message_text(
            text=f"✍️ <b>Nuevo Alias:</b>\nEscribe cómo quieres llamar a: <i>{nombre_banco}</i>",
            parse_mode="HTML"
        )
        return

    # --- CASO 4: SELECCIONAR CATEGORÍA ---
    if action == "CAT":
        await query.edit_message_text(text="⏳ Guardando en Sheets...")
        
        categoria_seleccionada = parts[2]
        alias_guardado = context.user_data.get("temp_alias")
        
        context.user_data.pop("esperando_alias_id", None)
        context.user_data.pop("esperando_categoria_id", None)
        context.user_data.pop("temp_alias", None)
        
        await procesar_gasto(
            update, context, 
            email_id, 
            categoria_seleccionada, 
            alias_manual=alias_guardado
        )

    if action == "CHECK":
        await query.edit_message_text(text="🔍 Buscando en la base de datos...")
        row_idx, p = get_pendiente(email_id)
        if not p:
            await query.edit_message_text(text="⚠️ Error: No encontré el gasto en Pendientes.")
            return
        comercio_raw = p["comercio_raw"]
        alias_encontrado, categoria_encontrada = get_mapping(comercio_raw)
        if alias_encontrado and categoria_encontrada:
            await query.edit_message_text(text="✅ ¡Comercio encontrado! Registrando...")
            await procesar_gasto(
                update, context,
                email_id,
                categoria_encontrada,
                alias_manual=alias_encontrado
            )
        else:
            keyboard = [
                [InlineKeyboardButton(f"✅ Mantener: {comercio_raw}", callback_data=f"KEEP|{email_id}")],
                [InlineKeyboardButton("✏️ Asignar nuevo nombre...", callback_data=f"OTRO|{email_id}")],
                [InlineKeyboardButton("❌ Ignorar", callback_data=f"IGNORE|{email_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text=f"❌ No encontré <b>{comercio_raw}</b> en tus registros.\n\n¿Qué deseas hacer?",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await request_access(update)
        return

    esperando_alias_email = context.user_data.get("esperando_alias_id")
    esperando_cat_email = context.user_data.get("esperando_categoria_id")
    esperando_nueva_cat_email = context.user_data.get("esperando_nueva_cat_id")
    
    mensaje_instruccion_id = context.user_data.get("mensaje_instruccion_id")
    chat_id = update.effective_chat.id

    # --- PASO 1: RECIBIMOS EL ALIAS ---
    if esperando_alias_email:
        nuevo_alias = update.message.text.strip()
        
        temp_msg = await update.message.reply_text("🔄 Generando opciones de categoría...")
        
        try: await update.message.delete()
        except: pass

        context.user_data["temp_alias"] = nuevo_alias
        
        del context.user_data["esperando_alias_id"]
        context.user_data["esperando_categoria_id"] = esperando_alias_email

        reply_markup = build_category_keyboard(esperando_alias_email)   

        texto_siguiente = (
            f"✅ Alias guardado: <b>{nuevo_alias}</b>\n\n"
            f"✍️ <b>Paso 2/2:</b>\n"
            f"Selecciona la <b>CATEGORÍA</b> o escríbela:"
        )
        
        if mensaje_instruccion_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, 
                    message_id=mensaje_instruccion_id,
                    text=texto_siguiente, 
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                await temp_msg.delete()
            except:
                msg = await context.bot.send_message(
                    chat_id=chat_id, 
                    text=texto_siguiente, 
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                context.user_data["mensaje_instruccion_id"] = msg.message_id
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id, 
                text=texto_siguiente, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        return

    if esperando_nueva_cat_email:
        nueva_cat_texto = update.message.text.strip().title()
        
        temp_msg = await update.message.reply_text("🔄 Creando categoría...")
        
        try: await update.message.delete()
        except: pass

        alias_final = context.user_data.get("temp_alias")
        
        await procesar_gasto(
            update, context,
            esperando_nueva_cat_email,
            nueva_cat_texto,
            mensaje_id_to_edit=mensaje_instruccion_id,
            alias_manual=alias_final
        )
        
        context.user_data.pop("esperando_nueva_cat_id", None)
        context.user_data.pop("mensaje_instruccion_id", None)
        context.user_data.pop("temp_alias", None)
        return

    # --- PASO 2: RECIBIMOS LA CATEGORÍA (MANUALMENTE) ---
    if esperando_cat_email:
        nueva_categoria = update.message.text.strip()
        alias_final = context.user_data.get("temp_alias")

        try: await update.message.delete()
        except: pass

        await procesar_gasto(
            update, context, 
            esperando_cat_email, 
            nueva_categoria, 
            mensaje_id_to_edit=mensaje_instruccion_id,
            alias_manual=alias_final
        )
        
        context.user_data.pop("esperando_categoria_id", None)
        context.user_data.pop("temp_alias", None)
        context.user_data.pop("mensaje_instruccion_id", None)
        return

    await update.message.reply_text("Te leo 👀 Esperando gastos...")


async def procesar_gasto(update, context, email_id, categoria, mensaje_id_to_edit=None, alias_manual=None):
    chat_id = update.effective_chat.id
    row_idx, p = get_pendiente(email_id)
    
    if not p:
        msg = "⚠️ Ya no encuentro ese gasto pendiente."
        if update.callback_query: await update.callback_query.edit_message_text(msg)
        elif mensaje_id_to_edit: 
            try: await context.bot.edit_message_text(chat_id=chat_id, message_id=mensaje_id_to_edit, text=msg)
            except: await context.bot.send_message(chat_id=chat_id, text=msg)
        return

    comercio_raw = p["comercio_raw"]
    monto = p["monto"]
    
    if alias_manual:
        alias = alias_manual
    else:
        alias = comercio_raw.title().strip()

    append_gasto(
        fecha=p["fecha_email"], hora=p["hora_email"], descripcion=p["desc"], monto=monto,
        categoria=categoria, comercio_raw=comercio_raw, comercio_alias=alias,
        usuario="telegram", chat_id=str(chat_id), email_id=email_id
    )
    upsert_mapping(comercio_raw, alias, categoria)
    mark_pendiente_ok(row_idx)

    texto_final = (
        f"✅ <b>Listo.</b> Gasto de <b>${monto}</b>\n"
        f"🏪 <b>{alias}</b>\n"
        f"📂 <b>{categoria}</b>"
    )
    
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text=texto_final, parse_mode="HTML")
        elif mensaje_id_to_edit:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=mensaje_id_to_edit,
                text=texto_final, parse_mode="HTML"
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text=texto_final, parse_mode="HTML")
    except Exception as e:
        print(f"Error finalizando mensaje: {e}")
        await context.bot.send_message(chat_id=chat_id, text=texto_final, parse_mode="HTML")





def get_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en .env")
    return token


def now_local():
    tz_name = os.getenv("TZ", "America/Santiago")
    tz = ZoneInfo(tz_name)
    dt = datetime.now(tz)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await request_access(update)
        return
    
    await update.message.reply_text(
        "Hola 👋 Soy tu bot de gastos.\n\n"
        "Cuando llegue un correo de BancoChile:\n"
        "- Si el comercio es conocido: se registra solo.\n"
        "- Si no: te pediré /clasificar.\n\n"
        "Uso:\n"
        "/clasificar <email_id> Categoria | Alias\n"
        "Ej:\n"
        "/clasificar 19b... Transporte | Metro"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await request_access(update)
        return

    await update.message.reply_text(
        "Comandos:\n"
        "/start\n"
        "/help\n"
        "/chatid\n"
        "/clasificar <email_id> Categoria | Alias\n\n"
        "Ej:\n"
        "/clasificar 19b547fd2f29cd4e Transporte | Metro"
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await request_access(update)
        return

    await update.message.reply_text(f"Tu chat_id es: {update.effective_chat.id}")


def parse_clasificacion(text: str):
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 3:
        return None
    _, email_id, rest = parts
    if "|" not in rest:
        return None

    categoria, alias = [x.strip() for x in rest.split("|", 1)]
    if not email_id or not categoria or not alias:
        return None
    return email_id.strip(), categoria, alias


async def clasificar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ No autorizado.")
        return
    
    msg = update.message.text or ""
    parsed = parse_clasificacion(msg)

    if not parsed:
        await update.message.reply_text(
            "Formato inválido.\n"
            "Usa:\n"
            "/clasificar <email_id> Categoria | Alias\n"
            "Ej:\n"
            "/clasificar 19b547fd2f29cd4e Transporte | Metro"
        )
        return

    email_id, categoria, alias = parsed

    row_idx, p = get_pendiente(email_id)
    if not p:
        await update.message.reply_text(
            f"No encontré ese email_id en Pendientes: {email_id}\n"
            "¿Seguro copiaste bien el ID?"
        )
        return

    if (p.get("estado") or "").strip().upper() == "OK":
        await update.message.reply_text("Ese pendiente ya estaba marcado como OK ✅")
        return

    comercio_raw = p["comercio_raw"]
    monto = p["monto"]
    descripcion = p["desc"]

    fecha = p["fecha_email"] or now_local()[0]
    hora = p["hora_email"] or now_local()[1]

    username = update.effective_user.username or update.effective_user.first_name or "usuario"
    chat_id = update.effective_chat.id

    append_gasto(
        fecha=fecha, hora=hora, descripcion=descripcion, monto=monto,
        categoria=categoria, comercio_raw=comercio_raw, comercio_alias=alias,
        usuario=username, chat_id=str(chat_id), email_id=email_id,
    )

    upsert_mapping(comercio_raw=comercio_raw, alias=alias, categoria=categoria)
    mark_pendiente_ok(row_idx)

    await update.message.reply_text(
        "✅ Listo. Registré el gasto y aprendí el comercio:\n"
        f"- {alias} ({comercio_raw})\n"
        f"- ${monto}\n"
        f"- {categoria}\n"
        f"ID: {email_id}"
    )


def main():

    app = Application.builder().token(get_token()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("clasificar", clasificar))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("🤖 Bot corriendo... (Ctrl+C para detener)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
