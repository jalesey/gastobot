"""
Script de uso único: genera el refresh token de la cuenta Gmail recolectora.

Requisitos previos:
1. En Google Cloud Console, crea un OAuth Client ID de tipo "Desktop app"
   y descarga el JSON como client_secret.json (misma carpeta que este script).
2. Ejecuta este script UNA VEZ, logueado con la cuenta Gmail que hará de
   buzón centralizado (ej. gastos.tuapp@gmail.com), no con tu cuenta personal.

Uso:
    python oauth_setup.py

Al terminar, guarda los 3 valores impresos como variables de entorno /
secretos de Cloud Run: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n=== Guarda estos valores como secretos de Cloud Run ===")
    print(f"GMAIL_CLIENT_ID={creds.client_id}")
    print(f"GMAIL_CLIENT_SECRET={creds.client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
