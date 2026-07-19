import os
import random
import string
import uuid
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Columnas de cada pestaña (1-based, coinciden con el orden de append_row)
USUARIOS_COLS = ["usuario_id", "nombre", "email_reenvio", "chat_id", "estado", "codigo_vinculacion", "fecha_registro"]
GASTOS_COLS = ["usuario_id", "fecha", "hora", "descripcion", "monto", "categoria",
               "comercio_raw", "comercio_alias", "origen", "chat_id", "email_id"]
COMERCIOS_COLS = ["usuario_id", "comercio_raw", "alias", "categoria"]
PENDIENTES_COLS = ["usuario_id", "email_id", "fecha_email", "hora_email", "monto",
                    "comercio_raw", "desc", "estado", "alias_temp"]

BASE_CATEGORIAS = ["Comida", "Supermercado", "Salud", "Transporte", "Hogar", "Ocio"]


def get_client():
    sa_path = os.getenv("GOOGLE_SA_JSON", "secrets/service_account.json")
    if not os.path.exists(sa_path):
        raise FileNotFoundError(f"No existe el JSON de Service Account en: {sa_path}")
    creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _open_ws(tab_name: str):
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en el entorno")
    gc = get_client()
    sh = gc.open_by_key(sheet_id)
    return sh.worksheet(tab_name)


def _find_row(ws, col_index: int, value: str):
    """Busca la primera fila (2-based, saltando encabezado) cuya columna col_index == value."""
    values = ws.get_all_values()
    target = str(value).strip()
    for idx, row in enumerate(values[1:], start=2):
        cell = row[col_index] if len(row) > col_index else ""
        if str(cell).strip() == target:
            return idx, row
    return None, None


# -------------------------
# USUARIOS
# -------------------------
def crear_usuario(nombre: str, email_reenvio: str) -> dict:
    ws = _open_ws("Usuarios")
    usuario_id = uuid.uuid4().hex[:8]
    codigo = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

    ws.append_row([
        usuario_id, nombre, email_reenvio.strip().lower(), "", "SIN_VINCULAR",
        codigo, str(date.today())
    ], value_input_option="USER_ENTERED")

    return {"usuario_id": usuario_id, "codigo_vinculacion": codigo}


def vincular_chat(codigo: str, chat_id: str) -> str | None:
    """Activa un usuario_id con su chat_id de Telegram. Devuelve usuario_id o None si el código no existe."""
    ws = _open_ws("Usuarios")
    idx, row = _find_row(ws, USUARIOS_COLS.index("codigo_vinculacion"), codigo)
    if not idx:
        return None
    ws.update_cell(idx, USUARIOS_COLS.index("chat_id") + 1, str(chat_id))
    ws.update_cell(idx, USUARIOS_COLS.index("estado") + 1, "ACTIVO")
    return row[USUARIOS_COLS.index("usuario_id")]


def get_usuario_by_chat(chat_id: str) -> dict | None:
    ws = _open_ws("Usuarios")
    idx, row = _find_row(ws, USUARIOS_COLS.index("chat_id"), str(chat_id))
    if not idx:
        return None
    return dict(zip(USUARIOS_COLS, row))


def get_usuario_by_email(email: str) -> dict | None:
    ws = _open_ws("Usuarios")
    idx, row = _find_row(ws, USUARIOS_COLS.index("email_reenvio"), email.strip().lower())
    if not idx:
        return None
    return dict(zip(USUARIOS_COLS, row))


# -------------------------
# COMERCIOS (mapeo alias/categoria por usuario)
# -------------------------
def find_mapping(usuario_id: str, comercio_raw: str):
    ws = _open_ws("Comercios")
    values = ws.get_all_values()
    target = comercio_raw.strip().upper()
    for row in values[1:]:
        if len(row) < 4:
            continue
        if row[0] == usuario_id and row[1].strip().upper() == target:
            return (row[2].strip() or None), (row[3].strip() or None)
    return None, None


def upsert_mapping(usuario_id: str, comercio_raw: str, alias: str, categoria: str | None = None):
    ws = _open_ws("Comercios")
    values = ws.get_all_values()
    target = comercio_raw.strip().upper()
    for idx, row in enumerate(values[1:], start=2):
        if len(row) >= 4 and row[0] == usuario_id and row[1].strip().upper() == target:
            ws.update_cell(idx, COMERCIOS_COLS.index("alias") + 1, alias)
            if categoria:
                ws.update_cell(idx, COMERCIOS_COLS.index("categoria") + 1, categoria)
            return
    ws.append_row([usuario_id, comercio_raw, alias, categoria or ""], value_input_option="USER_ENTERED")


def get_unique_categories(usuario_id: str):
    ws = _open_ws("Comercios")
    values = ws.get_all_values()
    encontradas = set()
    for row in values[1:]:
        if len(row) >= 4 and row[0] == usuario_id:
            cat = row[3].strip()
            if cat:
                encontradas.add(cat)
    return sorted(set(BASE_CATEGORIAS).union(encontradas))


# -------------------------
# GASTOS
# -------------------------
def append_gasto(usuario_id, fecha, hora, descripcion, monto, categoria,
                  comercio_raw, comercio_alias, origen, chat_id, email_id):
    ws = _open_ws("Gastos")
    ws.append_row([
        usuario_id, fecha, hora, descripcion, monto, categoria,
        comercio_raw, comercio_alias, origen, str(chat_id), email_id
    ], value_input_option="USER_ENTERED")


# -------------------------
# PENDIENTES
# -------------------------
def upsert_pendiente(usuario_id, email_id, fecha_email, hora_email, monto, comercio_raw, desc):
    ws = _open_ws("Pendientes")
    values = ws.get_all_values()
    for row in values[1:]:
        if len(row) > 1 and row[1] == email_id:
            return  # ya existe
    ws.append_row([
        usuario_id, email_id, fecha_email, hora_email, monto, comercio_raw, desc, "PENDIENTE", ""
    ], value_input_option="USER_ENTERED")


def get_pendiente(email_id: str):
    """Devuelve (row_index, data_dict) o (None, None)."""
    ws = _open_ws("Pendientes")
    values = ws.get_all_values()
    for idx, row in enumerate(values[1:], start=2):
        if len(row) < 8:
            continue
        if str(row[1]).strip() == str(email_id).strip():
            return idx, {
                "usuario_id": row[0],
                "email_id": row[1],
                "fecha_email": row[2],
                "hora_email": row[3],
                "monto": int(str(row[4]).replace(".", "").replace(",", "")),
                "comercio_raw": row[5],
                "desc": row[6] or "Compra Tarjeta Crédito",
                "estado": row[7] or "",
                "alias_temp": row[8] if len(row) > 8 else "",
            }
    return None, None


def set_pendiente_alias_temp(row_index: int, alias: str):
    _open_ws("Pendientes").update_cell(row_index, PENDIENTES_COLS.index("alias_temp") + 1, alias)


def mark_pendiente(row_index: int, estado: str):
    _open_ws("Pendientes").update_cell(row_index, PENDIENTES_COLS.index("estado") + 1, estado)


# -------------------------
# SESIONES (estado "esperando alias/categoría", por mensaje)
# -------------------------
def set_await(chat_id, message_id, email_id, tipo):
    ws = _open_ws("Sesiones")
    values = ws.get_all_values()
    target_chat, target_msg = str(chat_id), str(message_id)
    for idx, row in enumerate(values[1:], start=2):
        if len(row) >= 2 and row[0] == target_chat and row[1] == target_msg:
            ws.update_cell(idx, 3, email_id)
            ws.update_cell(idx, 4, tipo)
            return
    ws.append_row([target_chat, target_msg, email_id, tipo], value_input_option="USER_ENTERED")


def get_await(chat_id, message_id):
    ws = _open_ws("Sesiones")
    values = ws.get_all_values()
    target_chat, target_msg = str(chat_id), str(message_id)
    for row in values[1:]:
        if len(row) >= 4 and row[0] == target_chat and row[1] == target_msg:
            return {"email_id": row[2], "tipo": row[3]}
    return None


def delete_await(chat_id, message_id):
    ws = _open_ws("Sesiones")
    values = ws.get_all_values()
    target_chat, target_msg = str(chat_id), str(message_id)
    for idx, row in enumerate(values[1:], start=2):
        if len(row) >= 2 and row[0] == target_chat and row[1] == target_msg:
            ws.delete_rows(idx)
            return
