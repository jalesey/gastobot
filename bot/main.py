import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from services.sheets import (
    get_pendiente,
    mark_pendiente_ok,
    append_gasto,
    upsert_mapping,
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


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Mensaje de fallback (por si escribes cualquier cosa)
    await update.message.reply_text(
        "Te leo 👀\n"
        "Si estás clasificando un gasto, usa:\n"
        "/clasificar <email_id> Categoria | Alias\n"
        "Ej:\n"
        "/clasificar 19b547fd2f29cd4e Transporte | Metro"
    )


def main():
    load_env()
    app = Application.builder().token(get_token()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("clasificar", clasificar))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("🤖 Bot corriendo... (Ctrl+C para detener)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
