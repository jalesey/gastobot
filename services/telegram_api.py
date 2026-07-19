import os

import requests

BASE_URL = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en el entorno")
    return token


def _call(method: str, payload: dict):
    url = BASE_URL.format(token=_token(), method=method)
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.json()
    except requests.RequestException as e:
        print(f"❌ Error llamando a Telegram ({method}): {e}")
        return None


def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    return _call("sendMessage", payload)


def edit_message_text(chat_id, message_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    return _call("editMessageText", payload)


def answer_callback_query(callback_query_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    return _call("answerCallbackQuery", payload)


def delete_message(chat_id, message_id):
    return _call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def set_webhook(url: str):
    return _call("setWebhook", {"url": url})
