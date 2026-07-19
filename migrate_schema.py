"""
Script de uso único: migra la planilla del esquema viejo (un solo usuario)
al esquema multi-tenant nuevo (usuario_id en Gastos/Comercios/Pendientes,
Usuarios con email_reenvio/codigo_vinculacion, pestaña Sesiones nueva).

Se asume:
- Todos los "Gastos" existentes ya tienen su propio chat_id -> se mapean
  al usuario_id correspondiente automáticamente.
- Las hojas "Comercios" y "Pendientes" viejas NO tienen chat_id (son de
  cuando el sistema era de un solo usuario), así que TODAS sus filas se
  asignan al usuario marcado como DUEÑO_PRINCIPAL más abajo. Si eso no es
  correcto, ajusta la constante antes de correr el script.

Uso:
    python migrate_schema.py

Requiere las mismas variables de entorno que services/sheets.py
(GOOGLE_SHEET_ID, GOOGLE_SA_JSON).
"""

import os
import random
import string
import uuid

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# chat_id del usuario al que se le van a asignar todas las filas viejas
# de Comercios y Pendientes (que no tenían chat_id propio).
DUENO_PRINCIPAL_CHAT_ID = "6499524601"  # "admin" según la hoja Usuarios actual


def get_client():
    sa_path = os.getenv("GOOGLE_SA_JSON", "secrets/service_account.json")
    creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return gspread.authorize(creds)


def nuevo_usuario_id():
    return uuid.uuid4().hex[:8]


def nuevo_codigo():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def migrar_usuarios(sh):
    ws = sh.worksheet("Usuarios")
    values = ws.get_all_values()
    filas = values[1:]  # esquema viejo: chat_id | nombre | estado | fecha

    nuevas_filas = []
    chat_a_usuario_id = {}

    for row in filas:
        chat_id = row[0] if len(row) > 0 else ""
        nombre = row[1] if len(row) > 1 else ""
        estado_viejo = row[2] if len(row) > 2 else ""
        fecha = row[3] if len(row) > 3 else ""
        if not chat_id:
            continue

        usuario_id = nuevo_usuario_id()
        chat_a_usuario_id[str(chat_id).strip()] = usuario_id

        # Ya estaba autorizado antes -> queda ACTIVO directamente (no necesita /vincular)
        estado = "ACTIVO" if estado_viejo.strip().upper() == "AUTORIZADO" else "SIN_VINCULAR"

        nuevas_filas.append([
            usuario_id, nombre, "", str(chat_id), estado, nuevo_codigo(), fecha
        ])

    ws.clear()
    ws.append_row(
        ["usuario_id", "nombre", "email_reenvio", "chat_id", "estado", "codigo_vinculacion", "fecha_registro"],
        value_input_option="USER_ENTERED",
    )
    for fila in nuevas_filas:
        ws.append_row(fila, value_input_option="USER_ENTERED")

    print(f"✅ Usuarios migrados: {len(nuevas_filas)}")
    for chat_id, usuario_id in chat_a_usuario_id.items():
        print(f"   chat_id={chat_id} -> usuario_id={usuario_id}")

    return chat_a_usuario_id


def migrar_gastos(sh, chat_a_usuario_id, dueno_usuario_id):
    ws = sh.worksheet("Gastos")
    values = ws.get_all_values()
    filas = values[1:]  # esquema viejo: fecha,hora,descripcion,monto,categoria,comercio_raw,comercio_alias,usuario,chat_id,email_id

    nuevas_filas = []
    for row in filas:
        row = row + [""] * (10 - len(row))
        fecha, hora, descripcion, monto, categoria, comercio_raw, comercio_alias, origen, chat_id, email_id = row[:10]
        usuario_id = chat_a_usuario_id.get(str(chat_id).strip(), dueno_usuario_id)
        nuevas_filas.append([
            usuario_id, fecha, hora, descripcion, monto, categoria,
            comercio_raw, comercio_alias, origen, chat_id, email_id
        ])

    ws.clear()
    ws.append_row(
        ["usuario_id", "fecha", "hora", "descripcion", "monto", "categoria",
         "comercio_raw", "comercio_alias", "origen", "chat_id", "email_id"],
        value_input_option="USER_ENTERED",
    )
    for fila in nuevas_filas:
        ws.append_row(fila, value_input_option="USER_ENTERED")

    print(f"✅ Gastos migrados: {len(nuevas_filas)}")


def migrar_comercios(sh, dueno_usuario_id):
    ws = sh.worksheet("Comercios")
    values = ws.get_all_values()
    filas = values[1:]  # esquema viejo: comercio_raw, alias_default, categoria_default

    nuevas_filas = []
    for row in filas:
        row = row + [""] * (3 - len(row))
        comercio_raw, alias, categoria = row[:3]
        nuevas_filas.append([dueno_usuario_id, comercio_raw, alias, categoria])

    ws.clear()
    ws.append_row(["usuario_id", "comercio_raw", "alias", "categoria"], value_input_option="USER_ENTERED")
    for fila in nuevas_filas:
        ws.append_row(fila, value_input_option="USER_ENTERED")

    print(f"✅ Comercios migrados: {len(nuevas_filas)} (todos asignados al dueño principal)")


def migrar_pendientes(sh, dueno_usuario_id):
    ws = sh.worksheet("Pendientes")
    values = ws.get_all_values()
    filas = values[1:]  # esquema viejo: email_id,fecha_email,hora_email,monto,comercio_raw,desc,estado

    nuevas_filas = []
    for row in filas:
        row = row + [""] * (7 - len(row))
        email_id, fecha_email, hora_email, monto, comercio_raw, desc, estado = row[:7]
        nuevas_filas.append([
            dueno_usuario_id, email_id, fecha_email, hora_email, monto, comercio_raw, desc, estado, ""
        ])

    ws.clear()
    ws.append_row(
        ["usuario_id", "email_id", "fecha_email", "hora_email", "monto", "comercio_raw", "desc", "estado", "alias_temp"],
        value_input_option="USER_ENTERED",
    )
    for fila in nuevas_filas:
        ws.append_row(fila, value_input_option="USER_ENTERED")

    print(f"✅ Pendientes migrados: {len(nuevas_filas)} (todos asignados al dueño principal)")


def crear_hoja_sesiones(sh):
    try:
        sh.worksheet("Sesiones")
        print("ℹ️  La hoja Sesiones ya existe, no se crea de nuevo.")
        return
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Sesiones", rows=100, cols=4)
        ws.append_row(["chat_id", "message_id", "email_id", "tipo"], value_input_option="USER_ENTERED")
        print("✅ Hoja Sesiones creada.")


def main():
    gc = get_client()
    sh = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])

    chat_a_usuario_id = migrar_usuarios(sh)
    dueno_usuario_id = chat_a_usuario_id.get(DUENO_PRINCIPAL_CHAT_ID)
    if not dueno_usuario_id:
        raise RuntimeError(
            f"No encontré el chat_id {DUENO_PRINCIPAL_CHAT_ID} en Usuarios. "
            "Ajusta DUENO_PRINCIPAL_CHAT_ID en este script antes de correrlo."
        )

    migrar_gastos(sh, chat_a_usuario_id, dueno_usuario_id)
    migrar_comercios(sh, dueno_usuario_id)
    migrar_pendientes(sh, dueno_usuario_id)
    crear_hoja_sesiones(sh)

    print("\n🎉 Migración completa. Falta: completar manualmente la columna "
          "'email_reenvio' en Usuarios para cada persona.")


if __name__ == "__main__":
    main()
