import os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from zoneinfo import ZoneInfo

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def get_client():
    sa_path = os.getenv("GOOGLE_SA_JSON", "secrets/service_account.json")
    if not os.path.exists(sa_path):
        raise FileNotFoundError(f"No existe el JSON de Service Account en: {sa_path}")

    creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    return gspread.authorize(creds)

def append_gasto(descripcion: str, monto: int, usuario: str, chat_id: str) -> None:
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    tab_name = os.getenv("GOOGLE_SHEET_TAB", "Gastos")
    tz_name = os.getenv("TZ", "America/Santiago")

    if not sheet_id:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en el .env")

    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    fecha = now.strftime("%Y-%m-%d")
    hora = now.strftime("%H:%M:%S")

    gc = get_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab_name)

    ws.append_row([fecha, hora, descripcion, monto, usuario, str(chat_id)], value_input_option="USER_ENTERED")
