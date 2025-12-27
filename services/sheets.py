import os
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_client():
    sa_path = os.getenv("GOOGLE_SA_JSON", "secrets/service_account.json")
    if not os.path.exists(sa_path):
        raise FileNotFoundError(f"No existe el JSON de Service Account en: {sa_path}")

    creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _open_ws(tab_name: str):
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en .env")

    gc = get_client()
    sh = gc.open_by_key(sheet_id)
    return sh.worksheet(tab_name)


# -------------------------
# MAPPINGS (Comercios)
# -------------------------
def get_mapping(comercio_raw: str):
    """Retorna (alias, categoria) o (None, None) desde hoja 'Comercios'."""
    map_tab = os.getenv("GOOGLE_MAP_TAB", "Comercios")
    ws = _open_ws(map_tab)

    values = ws.get_all_values()
    target = comercio_raw.strip().upper()

    for row in values[1:]:
        if not row:
            continue
        raw = (row[0] if len(row) > 0 else "").strip().upper()
        if raw == target:
            alias = (row[1] if len(row) > 1 else "").strip() or None
            categoria = (row[2] if len(row) > 2 else "").strip() or None
            return alias, categoria

    return None, None


def upsert_mapping(comercio_raw: str, alias: str, categoria: str | None = None):
    """Inserta o actualiza alias/categoria en 'Comercios'."""
    map_tab = os.getenv("GOOGLE_MAP_TAB", "Comercios")
    ws = _open_ws(map_tab)

    values = ws.get_all_values()
    target = comercio_raw.strip().upper()

    for idx, row in enumerate(values[1:], start=2):
        raw = (row[0] if len(row) > 0 else "").strip().upper()
        if raw == target:
            ws.update_acell(f"B{idx}", alias)
            if categoria is not None:
                ws.update_acell(f"C{idx}", categoria)
            return

    ws.append_row([comercio_raw, alias, categoria or ""], value_input_option="USER_ENTERED")


# -------------------------
# GASTOS
# -------------------------
def append_gasto(
    fecha: str,
    hora: str,
    descripcion: str,
    monto: int,
    categoria: str,
    comercio_raw: str,
    comercio_alias: str,
    usuario: str,
    chat_id: str,
    email_id: str,
):
    """Inserta fila en hoja 'Gastos'."""
    gastos_tab = os.getenv("GOOGLE_SHEET_TAB", "Gastos")
    ws = _open_ws(gastos_tab)

    ws.append_row(
        [fecha, hora, descripcion, monto, categoria, comercio_raw, comercio_alias, usuario, str(chat_id), email_id],
        value_input_option="USER_ENTERED",
    )


# -------------------------
# PENDIENTES
# -------------------------
def get_pendiente(email_id: str):
    """
    Busca en hoja 'Pendientes' por email_id y retorna:
    (row_index, data_dict) o (None, None)
    """
    ws = _open_ws("Pendientes")
    values = ws.get_all_values()

    for idx, row in enumerate(values[1:], start=2):
        if len(row) < 7:
            continue
        if str(row[0]).strip() == str(email_id).strip():
            return idx, {
                "email_id": row[0],
                "fecha_email": row[1],
                "hora_email": row[2],
                "monto": int(str(row[3]).replace(".", "").replace(",", "")),
                "comercio_raw": row[4],
                "desc": row[5] or "Compra Tarjeta Crédito",
                "estado": row[6] or "",
            }

    return None, None


def mark_pendiente_ok(row_index: int):
    """Marca estado = OK en hoja Pendientes (columna G)."""
    ws = _open_ws("Pendientes")
    ws.update_acell(f"G{row_index}", "OK")
