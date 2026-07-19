import os

from flask import Flask, request, render_template, abort

from keyboards import build_category_keyboard, keyboard_confirmar_o_asignar
from services.sheets import (
    crear_usuario, vincular_chat, get_usuario_by_chat,
    find_mapping, upsert_mapping, append_gasto,
    get_pendiente, mark_pendiente, set_pendiente_alias_temp,
    set_await, get_await, delete_await,
)
from services.telegram_api import (
    send_message, edit_message_text, answer_callback_query, delete_message,
)
import gmail_client

app = Flask(__name__)


# =========================================================
# ALTA DE USUARIOS
# =========================================================
@app.route("/registro", methods=["GET"])
def registro_form():
    return render_template("registro.html")


@app.route("/registro", methods=["POST"])
def registro_submit():
    nombre = request.form.get("nombre", "").strip()
    email = request.form.get("email", "").strip()
    if not nombre or not email:
        return render_template("registro.html", error="Nombre y email son obligatorios."), 400

    datos = crear_usuario(nombre, email)
    bot_username = os.getenv("TELEGRAM_BOT_USERNAME", "tu_bot")
    return render_template(
        "registro.html",
        exito=True,
        codigo=datos["codigo_vinculacion"],
        bot_username=bot_username,
    )


# =========================================================
# WEBHOOK DE TELEGRAM
# =========================================================
@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        abort(401)

    update = request.get_json(force=True, silent=True) or {}
    try:
        if "callback_query" in update:
            handle_callback(update["callback_query"])
        elif "message" in update:
            handle_message(update["message"])
    except Exception as e:
        print(f"❌ Error procesando update: {e}")

    return "OK"


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if msg.get("reply_to_message"):
        awaiting = get_await(chat_id, msg["reply_to_message"]["message_id"])
        if awaiting:
            delete_await(chat_id, msg["reply_to_message"]["message_id"])
            handle_awaited_reply(chat_id, msg, awaiting, text)
            return

    if text.startswith("/vincular"):
        partes = text.split(maxsplit=1)
        if len(partes) < 2:
            send_message(chat_id, "Uso: /vincular <código>\n(el código te lo dio el formulario de registro)")
            return
        usuario_id = vincular_chat(partes[1].strip(), chat_id)
        if usuario_id:
            send_message(chat_id, "✅ ¡Cuenta vinculada! Ya puedes reenviar tus correos del banco.")
        else:
            send_message(chat_id, "❌ Código inválido. Revisa que lo hayas copiado bien.")
        return

    usuario = get_usuario_by_chat(chat_id)

    if text.startswith("/start"):
        if not usuario:
            send_message(chat_id,
                "Hola 👋 Para usar el bot primero regístrate en el formulario y "
                "luego escribe /vincular <código> que te va a dar el formulario."
            )
        else:
            send_message(chat_id, "Hola 👋 Tu cuenta ya está vinculada. Reenvía tus correos del banco cuando quieras.")
        return

    if text.startswith("/chatid"):
        send_message(chat_id, f"Tu chat_id es: {chat_id}")
        return

    if not usuario:
        send_message(chat_id, "Aún no vinculas tu cuenta. Usa /vincular <código>.")
        return

    send_message(chat_id, "Te leo 👀 Esperando gastos...")


def handle_awaited_reply(chat_id, msg, awaiting, text):
    usuario = get_usuario_by_chat(chat_id)
    if not usuario:
        return
    usuario_id = usuario["usuario_id"]
    prompt_message_id = msg["reply_to_message"]["message_id"]

    if awaiting["tipo"] == "ALIAS":
        row_idx, _ = get_pendiente(awaiting["email_id"])
        if row_idx:
            set_pendiente_alias_temp(row_idx, text)
        edit_message_text(
            chat_id, prompt_message_id,
            f"✅ Alias guardado: <b>{text}</b>\n\n📂 Selecciona la <b>CATEGORÍA</b>:",
            build_category_keyboard(usuario_id, awaiting["email_id"]),
        )
        delete_message(chat_id, msg["message_id"])
        return

    if awaiting["tipo"] == "NEWCAT":
        categoria = text.title()
        delete_message(chat_id, msg["message_id"])
        finalizar_gasto(usuario_id, chat_id, prompt_message_id, awaiting["email_id"], categoria)
        return


def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    parts = cq["data"].split("|")
    action = parts[0]

    usuario = get_usuario_by_chat(chat_id)
    if not usuario:
        answer_callback_query(cq["id"], "⛔ Vincula tu cuenta primero con /vincular.", show_alert=True)
        return
    usuario_id = usuario["usuario_id"]
    answer_callback_query(cq["id"])

    email_id = parts[1] if len(parts) > 1 else None

    if action == "IGNORE":
        row_idx, _ = get_pendiente(email_id)
        if row_idx:
            mark_pendiente(row_idx, "IGNORADO")
        edit_message_text(chat_id, message_id, "❌ Gasto descartado.")
        return

    if action == "KEEP":
        _, p = get_pendiente(email_id)
        if not p:
            edit_message_text(chat_id, message_id, "⚠️ Error: No encontré el gasto en Pendientes.")
            return
        edit_message_text(
            chat_id, message_id,
            f"✅ Alias: <b>{p['comercio_raw']}</b>\n\n📂 Selecciona la <b>CATEGORÍA</b>:",
            build_category_keyboard(usuario_id, email_id),
        )
        return

    if action == "OTRO":
        _, p = get_pendiente(email_id)
        nombre_banco = p["comercio_raw"] if p else "este comercio"
        edit_message_text(
            chat_id, message_id,
            f"✍️ <b>Nuevo Alias:</b>\nResponde a ESTE mensaje (usa 'Responder') escribiendo cómo quieres llamar a: <i>{nombre_banco}</i>",
        )
        set_await(chat_id, message_id, email_id, "ALIAS")
        return

    if action == "NEW_CAT":
        edit_message_text(
            chat_id, message_id,
            "✍️ Responde a ESTE mensaje (usa 'Responder') con el nombre de la <b>NUEVA CATEGORÍA</b>:",
        )
        set_await(chat_id, message_id, email_id, "NEWCAT")
        return

    if action == "CHECK":
        _, p = get_pendiente(email_id)
        if not p:
            edit_message_text(chat_id, message_id, "⚠️ Error: No encontré el gasto en Pendientes.")
            return
        alias, categoria = find_mapping(usuario_id, p["comercio_raw"])
        if alias and categoria:
            finalizar_gasto(usuario_id, chat_id, message_id, email_id, categoria, alias_manual=alias)
        else:
            edit_message_text(
                chat_id, message_id,
                f"❌ No encontré <b>{p['comercio_raw']}</b> en tus registros.\n\n¿Qué deseas hacer?",
                keyboard_confirmar_o_asignar(email_id, p["comercio_raw"]),
            )
        return

    if action == "CAT":
        categoria = parts[2]
        finalizar_gasto(usuario_id, chat_id, message_id, email_id, categoria)
        return


def finalizar_gasto(usuario_id, chat_id, message_id, email_id, categoria, alias_manual=None):
    row_idx, p = get_pendiente(email_id)
    if not p:
        texto = "⚠️ Ya no encuentro ese gasto pendiente."
        edit_message_text(chat_id, message_id, texto) if message_id else send_message(chat_id, texto)
        return

    alias = alias_manual or p.get("alias_temp") or p["comercio_raw"]

    append_gasto(
        usuario_id=usuario_id, fecha=p["fecha_email"], hora=p["hora_email"],
        descripcion=p["desc"], monto=p["monto"], categoria=categoria,
        comercio_raw=p["comercio_raw"], comercio_alias=alias,
        origen="telegram", chat_id=chat_id, email_id=email_id,
    )
    upsert_mapping(usuario_id, p["comercio_raw"], alias, categoria)
    mark_pendiente(row_idx, "OK")
    set_pendiente_alias_temp(row_idx, "")

    texto = f"✅ <b>Listo.</b> Gasto de <b>${p['monto']}</b>\n🏪 <b>{alias}</b>\n📂 <b>{categoria}</b>"
    edit_message_text(chat_id, message_id, texto) if message_id else send_message(chat_id, texto)


# =========================================================
# TAREA PROGRAMADA: revisar el buzón centralizado (Cloud Scheduler)
# =========================================================
@app.route("/tasks/poll-gmail", methods=["POST"])
def poll_gmail():
    secret = os.getenv("POLL_SECRET")
    if secret and request.headers.get("X-Poll-Secret") != secret:
        abort(401)

    procesados = gmail_client.poll_inbox()
    return {"procesados": procesados}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
