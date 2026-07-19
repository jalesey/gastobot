import re

# Cada parser recibe el texto plano del correo (asunto + cuerpo, ya sea original
# o reenviado) y devuelve {"monto": "...", "comercio_raw": "..."} o None si no
# reconoce el formato. Para agregar un banco nuevo: escribir su función y
# sumarla a PARSERS con una palabra clave que lo identifique dentro del texto
# (el remitente original ya no sirve para identificarlo, porque el correo que
# llega al buzón centralizado viene reenviado por el usuario, no por el banco).


def _extract_bancochile(text: str):
    monto_match = re.search(r"compra por \$([0-9\.]+)", text, re.IGNORECASE)
    comercio_match = re.search(
        r"\*{4}\d+\s+en\s+(.+?)\s+el\s+\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}",
        text, re.IGNORECASE
    )
    if not monto_match:
        return None
    return {
        "monto": monto_match.group(1).replace(".", ""),
        "comercio_raw": comercio_match.group(1).strip() if comercio_match else "DESCONOCIDO",
    }


def extract_fecha_hora(text: str):
    m = re.search(r"el\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", text)
    if not m:
        return None
    return {"fecha": m.group(1), "hora": m.group(2)}


# clave de detección (substring en minúsculas) -> parser
PARSERS = {
    "bancochile.cl": _extract_bancochile,
    "banco de chile": _extract_bancochile,
}


def parse_gasto(text: str):
    """Prueba cada banco conocido contra el texto. Devuelve dict con monto/comercio_raw o None."""
    lower = text.lower()
    for marca, parser in PARSERS.items():
        if marca in lower:
            data = parser(text)
            if data:
                return data
    return None
