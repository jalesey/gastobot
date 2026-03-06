import os
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def get_estado_usuario(chat_id: int) -> str | None:
    ws = _open_ws("Usuarios")
    values = ws.get_all_values()
    target = str(chat_id)
    for row in values[1:]:
        if str(row[0]).strip() == target:
            return row[2].strip().upper() if len(row) > 2 else None
    return None

def get_usuarios_autorizados() -> set[int]:
    ws = _open_ws("Usuarios")
    values = ws.get_all_values()
    return {
        int(row[0]) for row in values[1:]
        if len(row) > 2 and row[2].strip().upper() == "AUTORIZADO"
    }

def upsert_usuario(chat_id: int, nombre: str, estado: str):
    from datetime import date
    ws = _open_ws("Usuarios")
    values = ws.get_all_values()
    target = str(chat_id)

    for idx, row in enumerate(values[1:], start=2):
        if str(row[0]).strip() == target:
            ws.update_acell(f"B{idx}", nombre)
            ws.update_acell(f"C{idx}", estado)
            return

    ws.append_row(
        [str(chat_id), nombre, estado, str(date.today())],
        value_input_option="USER_ENTERED"
    )


def get_unique_categories():
    """
    Retorna una lista de categorías únicas encontradas en la hoja 'Comercios' (Columna C),
    más las básicas por defecto.
    """
    map_tab = os.getenv("GOOGLE_MAP_TAB", "Comercios")
    ws = _open_ws(map_tab)
    
    # Obtenemos toda la columna C (Categorías)
    # Asumimos que la columna C es la 3ra (índice 2, pero gspread usa 1-based para col_values o get_all_values)
    # Es más seguro traer todo y procesar en python
    values = ws.get_all_values()
    
    categorias_encontradas = set()
    
    # Recorremos desde la fila 1 (saltando encabezado)
    for row in values[1:]:
        if len(row) > 2:
            cat = row[2].strip()
            if cat:
                categorias_encontradas.add(cat)
    
    # Categorías base que SIEMPRE queremos que estén
    base = ["Comida", "Supermercado", "Salud", "Transporte", "Hogar", "Ocio"]
    
    # Unimos las base con las encontradas
    todas = set(base).union(categorias_encontradas)
    
    return sorted(list(todas))

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
