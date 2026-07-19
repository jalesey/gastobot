import base64
import os
from email.utils import parseaddr

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from bank_parsers import parse_gasto, extract_fecha_hora
from keyboards import keyboard_nuevo_gasto
from services.sheets import find_mapping, append_gasto, upsert_pendiente, get_usuario_by_email
from services.telegram_api import send_message

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
LABEL_NAME = "GastoBot-Procesado"


def _get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def _get_or_create_label(service, name=LABEL_NAME):
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == name:
            return label["id"]
    created = service.users().labels().create(
        userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
    ).execute()
    return created["id"]


def _plain_text(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []) or []:
        text = _plain_text(part)
        if text:
            return text
    return ""


def _header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def poll_inbox():
    """Revisa el buzón centralizado por correos reenviados nuevos. Devuelve cuántos procesó."""
    service = _get_service()
    label_id = _get_or_create_label(service)

    resp = service.users().messages().list(userId="me", q="is:unread", maxResults=25).execute()
    messages = resp.get("messages", [])
    procesados = 0

    for m in messages:
        msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        headers = msg["payload"].get("headers", [])
        subject = _header(headers, "Subject")
        remitente_raw = _header(headers, "From")
        _, remitente_email = parseaddr(remitente_raw)

        body = _plain_text(msg["payload"])
        text = (subject + " " + body).replace("\r", " ").replace("\n", " ")

        _marcar_leido(service, m["id"], label_id)

        usuario = get_usuario_by_email(remitente_email)
        if not usuario:
            print(f"⚠️ Correo de remitente no registrado: {remitente_email}")
            continue

        data = parse_gasto(text)
        if not data or not data.get("monto"):
            print(f"Salto correo sin datos de compra: {subject}")
            continue

        usuario_id = usuario["usuario_id"]
        chat_id = usuario["chat_id"]
        comercio_raw = data["comercio_raw"]
        monto = data["monto"]
        email_id = m["id"]

        fh = extract_fecha_hora(text) or {"fecha": "", "hora": ""}
        alias, categoria = find_mapping(usuario_id, comercio_raw)

        if alias and categoria:
            append_gasto(
                usuario_id=usuario_id, fecha=fh["fecha"], hora=fh["hora"],
                descripcion="Compra Tarjeta Crédito", monto=monto, categoria=categoria,
                comercio_raw=comercio_raw, comercio_alias=alias,
                origen="gmail", chat_id=chat_id, email_id=email_id,
            )
            if chat_id:
                send_message(chat_id,
                    "✅ <b>Gasto Registrado:</b>\n"
                    f"- {alias}\n- ${monto}\n- <i>{categoria}</i>"
                )
        else:
            upsert_pendiente(
                usuario_id=usuario_id, email_id=email_id, fecha_email=fh["fecha"],
                hora_email=fh["hora"], monto=monto, comercio_raw=comercio_raw,
                desc="Compra Tarjeta Crédito",
            )
            if chat_id:
                send_message(chat_id,
                    "📩 <b>Nuevo gasto detectado:</b>\n"
                    f"- Comercio original: <b>{comercio_raw}</b>\n"
                    f"- Monto: ${monto}\n\n"
                    "¿Quieres mantener este nombre o asignar uno nuevo?",
                    keyboard_nuevo_gasto(email_id),
                )

        procesados += 1

    return procesados


def _marcar_leido(service, message_id, label_id):
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["UNREAD"], "addLabelIds": [label_id]},
    ).execute()
