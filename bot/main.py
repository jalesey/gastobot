import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

def get_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN. Crea un .env con TELEGRAM_BOT_TOKEN=...")
    return token

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hola 👋 Soy Gastobot.\n"
        "Envíame un mensaje con un gasto, por ejemplo:\n"
        "  almuerzo 4500\n"
        "y te lo confirmo."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Comandos:\n"
        "/start - iniciar\n"
        "/help - ayuda\n\n"
        "Ejemplo de gasto:\n"
        "café 1800"
    )

def parse_gasto(texto: str):
    """
    Parse simple: 'descripcion monto'
    Ej: 'almuerzo 4500' -> ('almuerzo', 4500)
    """
    parts = texto.strip().split()
    if len(parts) < 2:
        return None
    try:
        monto = int(parts[-1].replace(".", "").replace(",", ""))
    except ValueError:
        return None
    descripcion = " ".join(parts[:-1]).strip()
    if not descripcion:
        return None
    return descripcion, monto

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = (update.message.text or "").strip()
    parsed = parse_gasto(texto)

    if not parsed:
        await update.message.reply_text(
            "No pude leer el gasto 😅\n"
            "Formato esperado: `descripcion monto`\n"
            "Ej: `almuerzo 4500`",
            parse_mode="Markdown"
        )
        return

    descripcion, monto = parsed
    await update.message.reply_text(
        f"✅ Registrado (por ahora solo confirmo):\n"
        f"- Descripción: {descripcion}\n"
        f"- Monto: {monto}"
    )

def main() -> None:
    # Cargar .env manualmente (sin librerías extra)
    # Si quieres, después lo cambiamos a python-dotenv.
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    app = Application.builder().token(get_token()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("🤖 Gastobot corriendo... (Ctrl+C para detener)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
