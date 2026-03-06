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

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

_auth_cache = {"ids": set(), "ts": 0}

def is_authorized(update: Update) -> bool:
    import time
    ahora = time.time()
    
    if ahora - _auth_cache["ts"] > 120:   # ¿Pasaron más de 60s?
        _auth_cache["ids"] = get_usuarios_autorizados()  # → consulta Sheets
        _auth_cache["ts"] = ahora                        # → resetea el reloj
    
    return update.effective_chat.id in _auth_cache["ids"]  # usa lo que hay en memoria

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

    from telegram import Bot
    bot = update.get_bot()
    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"🔔 <b>Solicitud de acceso:</b>\n"
             f"👤 {nombre}\n"
             f"🆔 <code>{chat_id}</code>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

    # Le avisamos al usuario que está esperando
    await update.message.reply_text(
        "⏳ Tu solicitud fue enviada al administrador. "
        "Te avisaré cuando seas aprobado."
    )



def build_category_keyboard(email_id):
    # 1. Obtenemos las categorías actuales (Fijas + Las que aprendió el Excel)
    cats = get_unique_categories()
    
    keyboard = []
    row = []
    
    # 2. Creamos botones de a 2 por fila
    for cat in cats:
        btn = InlineKeyboardButton(cat, callback_data=f"CAT|{email_id}|{cat}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    # Si quedó uno suelto, lo agregamos
    if row:
        keyboard.append(row)
        
    # 3. Agregamos el botón para CREAR UNA NUEVA
    keyboard.append([
        InlineKeyboardButton("➕ Nueva Categoría...", callback_data=f"NEW_CAT|{email_id}")
    ])
    
    # 4. Botón de cancelar
    keyboard.append([
        InlineKeyboardButton("❌ Cancelar", callback_data=f"IGNORE|{email_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)

# 1. Modificamos button_handler para guardar el ID del mensaje

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.callback_query.answer("⛔ No autorizado.", show_alert=True)
        return
    query = update.callback_query
    await query.answer() # Avisa a Telegram que se recibió el clic
    
    data = query.data
    parts = data.split("|")
    action = parts[0]
    email_id = parts[1] if len(parts) > 1 else None

    if action == "AUTH_OK":
        target_chat_id = int(parts[1])
        nombre = parts[2] if len(parts) > 2 else str(target_chat_id)

        upsert_usuario(target_chat_id, nombre, "AUTORIZADO")
        
        _auth_cache["ts"] = 0
        await query.edit_message_text(f"✅ {nombre} autorizado.")
        

        # Avisamos al usuario aprobado
        await context.bot.send_message(
            chat_id=target_chat_id,
            text="✅ ¡Acceso aprobado! Ya puedes usar el bot."
        )
        return

    if action == "AUTH_DENY":
        target_chat_id = int(parts[1])
        nombre = parts[2] if len(parts) > 2 else str(target_chat_id)

        upsert_usuario(target_chat_id, nombre, "RECHAZADO")

        await query.edit_message_text(f"❌ {nombre} rechazado.")
        return

    # --- CASO 1: DESCARTAR ---
    if action == "IGNORE":
        await query.edit_message_text(text="❌ Gasto descartado.")
        return

    # --- CASO 2: MANTENER NOMBRE ORIGINAL (NUEVO) ---
    if action == "KEEP":
        # Feedback visual inmediato
        await query.edit_message_text(text="⏳ Cargando categorías...")

        # 1. Obtenemos el nombre original desde Sheets
        row_idx, p = get_pendiente(email_id)
        if not p:
            await query.edit_message_text(text="⚠️ Error: No encontré el gasto en Pendientes.")
            return
            
        alias_original = p["comercio_raw"]
        
        # 2. Guardamos este alias en memoria (como si lo hubieras escrito)
        context.user_data["temp_alias"] = alias_original
        context.user_data["esperando_categoria_id"] = email_id
        
        # 3. Mostramos DIRECTAMENTE los botones de categoría
        # USAMOS EL TECLADO DINÁMICO
        reply_markup = build_category_keyboard(email_id) 

        await query.edit_message_text(
            text=f"✅ Alias: <b>{alias_original}</b>\n\n📂 Selecciona la <b>CATEGORÍA</b>:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return
    
    # --- CASO: NUEVA CATEGORÍA (Botón ➕) ---
    if action == "NEW_CAT":
        email_id = parts[1]
        await query.edit_message_text(text="✍️ Escribe el nombre de la <b>NUEVA CATEGORÍA</b>:", parse_mode="HTML")
        
        # Guardamos estado para esperar texto
        context.user_data["esperando_nueva_cat_id"] = email_id
        context.user_data["mensaje_instruccion_id"] = query.message.message_id
        return

    # --- CASO 3: CAMBIAR NOMBRE MANUALMENTE ---
    if action == "OTRO":
        # Feedback visual
        await query.edit_message_text(text="🔍 Preparando...")
        
        # Guardamos estado: Esperamos que el usuario escriba un ALIAS
        context.user_data["esperando_alias_id"] = email_id
        # Guardamos ID del mensaje para editarlo después
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
        # Feedback visual
        await query.edit_message_text(text="⏳ Guardando en Sheets...")
        
        categoria_seleccionada = parts[2]
        
        # Recuperamos el alias que guardamos en el paso anterior (KEEP o OTRO)
        alias_guardado = context.user_data.get("temp_alias")
        
        # Limpieza de memoria
        context.user_data.pop("esperando_alias_id", None)
        context.user_data.pop("esperando_categoria_id", None)
        context.user_data.pop("temp_alias", None)
        
        # Procesamos
        await procesar_gasto(
            update, context, 
            email_id, 
            categoria_seleccionada, 
            alias_manual=alias_guardado
        )
    if action == "CHECK":
        await query.edit_message_text(text="🔍 Buscando en la base de datos...")
        row_idx, p = get_pendiente(email_id)
        print(p)
        if not p:
            await query.edit_message_text(text="⚠️ Error: No encontré el gasto en Pendientes.")
            return
        comercio_raw = p["comercio_raw"]
        print(comercio_raw)
        # Usamos tu función get_mapping para buscar en la hoja 'Comercios'
        alias_encontrado, categoria_encontrada = get_mapping(comercio_raw)
        if alias_encontrado and categoria_encontrada:
            # Si existe, lo procesamos automáticamente con los datos guardados
            await query.edit_message_text(text="✅ ¡Comercio encontrado! Registrando...")
            await procesar_gasto(
                update, context,
                email_id,
                categoria_encontrada,
                alias_manual=alias_encontrado
            )
        else:
            # Si no existe, volvemos a mostrar los botones para que lo clasifique
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

# 2. Modificamos on_text para borrar el mensaje de instrucción viejo
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    # Verificamos estado
    esperando_alias_email = context.user_data.get("esperando_alias_id")
    esperando_cat_email = context.user_data.get("esperando_categoria_id")
    esperando_nueva_cat_email = context.user_data.get("esperando_nueva_cat_id")
    
    mensaje_instruccion_id = context.user_data.get("mensaje_instruccion_id")
    chat_id = update.effective_chat.id

    # --- PASO 1: RECIBIMOS EL ALIAS ---
    if esperando_alias_email:
        nuevo_alias = update.message.text.strip()
        
        # Feedback visual rápido
        temp_msg = await update.message.reply_text("🔄 Generando opciones de categoría...")
        
        # 1. Borramos tu mensaje
        
        try: await update.message.delete()
        except: pass

        # 2. Guardamos Alias
        context.user_data["temp_alias"] = nuevo_alias
        
        # 3. Cambiamos estado: Ahora esperamos CATEGORÍA
        del context.user_data["esperando_alias_id"]
        context.user_data["esperando_categoria_id"] = esperando_alias_email

        # 4. DEFINIMOS LOS BOTONES (Igual que en Apps Script)
        # Usamos el email_id actual para que el botón sepa qué gasto es
        eid = esperando_alias_email 
       # --- NUEVO MENÚ DE BOTONES ---

        reply_markup = build_category_keyboard(esperando_alias_email)   

        # 5. Actualizamos el mensaje preguntando la categoría + BOTONES
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
                    reply_markup=reply_markup # <--- AQUÍ AGREGAMOS LOS BOTONES
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
        nueva_cat_texto = update.message.text.strip().title() # Ej: "Viajes"
        
        # Feedback visual rápido
        temp_msg = await update.message.reply_text("🔄 Creando categoría...")
        
        # 1. Borramos tu mensaje
        
        try: await update.message.delete()
        except: pass

        
        # Recuperamos el alias que ya teníamos guardado
        alias_final = context.user_data.get("temp_alias")
        
        # Procesamos el gasto con la NUEVA categoría
        # Al procesarlo, upsert_mapping guardará "Viajes" en la hoja Comercios.
        # La próxima vez que llames a get_unique_categories(), "Viajes" aparecerá.
        await procesar_gasto(
            update, context,
            esperando_nueva_cat_email,
            nueva_cat_texto,
            mensaje_id_to_edit=mensaje_instruccion_id,
            alias_manual=alias_final
        )
        
        # Limpieza
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
        
        # Limpieza
        context.user_data.pop("esperando_categoria_id", None)
        context.user_data.pop("temp_alias", None)
        context.user_data.pop("mensaje_instruccion_id", None)
        return

    await update.message.reply_text("Te leo 👀 Esperando gastos...")

# 3. Función auxiliar para procesar y responder bonito
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

    # Datos
    comercio_raw = p["comercio_raw"]
    monto = p["monto"]
    
    # --- LÓGICA DEL ALIAS ---
    # Si nos dieron un manual (Paso 1), lo usamos. Si no, generamos automático.
    if alias_manual:
        alias = alias_manual
    else:
        alias = comercio_raw.title().strip()

    # Guardar en Sheets
    append_gasto(
        fecha=p["fecha_email"], hora=p["hora_email"], descripcion=p["desc"], monto=monto,
        categoria=categoria, comercio_raw=comercio_raw, comercio_alias=alias,
        usuario="telegram", chat_id=str(chat_id), email_id=email_id
    )
    upsert_mapping(comercio_raw, alias, categoria)
    mark_pendiente_ok(row_idx)

    # Texto Final
    texto_final = (
        f"✅ <b>Listo.</b> Gasto de <b>${monto}</b>\n"
        f"🏪 <b>{alias}</b>\n"
        f"📂 <b>{categoria}</b>"
    )
    
    # Actualizar mensaje
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
        await update.message.reply_text("⛔ No autorizado.")
    
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
    await update.message.reply_text(f"Tu chat_id es: {update.effective_chat.id}")


def parse_clasificacion(text: str):
    """
    Espera: /clasificar <email_id> Categoria | Alias
    Retorna (email_id, categoria, alias) o None
    """
    # separa "/clasificar", email_id, y el resto
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

    # Datos del pendiente
    comercio_raw = p["comercio_raw"]
    monto = p["monto"]
    descripcion = p["desc"]

    # Fecha/hora: si viene del correo úsala, si no usa ahora
    fecha = p["fecha_email"] or now_local()[0]
    hora = p["hora_email"] or now_local()[1]

    username = update.effective_user.username or update.effective_user.first_name or "usuario"
    chat_id = update.effective_chat.id

    # 1) Registrar gasto
    append_gasto(
        fecha=fecha,
        hora=hora,
        descripcion=descripcion,
        monto=monto,
        categoria=categoria,
        comercio_raw=comercio_raw,
        comercio_alias=alias,
        usuario=username,
        chat_id=str(chat_id),
        email_id=email_id,
    )

    # 2) Guardar mapping para futuro
    upsert_mapping(comercio_raw=comercio_raw, alias=alias, categoria=categoria)

    # 3) Marcar pendiente OK
    mark_pendiente_ok(row_idx)

    await update.message.reply_text(
        "✅ Listo. Registré el gasto y aprendí el comercio:\n"
        f"- {alias} ({comercio_raw})\n"
        f"- ${monto}\n"
        f"- {categoria}\n"
        f"ID: {email_id}"
    )


        

def main():
    load_env()
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
