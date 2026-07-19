from services.sheets import get_unique_categories


def keyboard_nuevo_gasto(email_id):
    return {
        "inline_keyboard": [
            [{"text": "🔍 Buscar si ya existe", "callback_data": f"CHECK|{email_id}"}],
            [{"text": "✏️ Asignar nuevo nombre...", "callback_data": f"OTRO|{email_id}"}],
            [{"text": "❌ Ignorar", "callback_data": f"IGNORE|{email_id}"}],
        ]
    }


def keyboard_confirmar_o_asignar(email_id, comercio_raw):
    return {
        "inline_keyboard": [
            [{"text": f"✅ Mantener: {comercio_raw}", "callback_data": f"KEEP|{email_id}"}],
            [{"text": "✏️ Asignar nuevo nombre...", "callback_data": f"OTRO|{email_id}"}],
            [{"text": "❌ Ignorar", "callback_data": f"IGNORE|{email_id}"}],
        ]
    }


def build_category_keyboard(usuario_id, email_id):
    categorias = get_unique_categories(usuario_id)

    keyboard, row = [], []
    for cat in categorias:
        row.append({"text": cat, "callback_data": f"CAT|{email_id}|{cat}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([{"text": "➕ Nueva Categoría...", "callback_data": f"NEW_CAT|{email_id}"}])
    keyboard.append([{"text": "❌ Cancelar", "callback_data": f"IGNORE|{email_id}"}])

    return {"inline_keyboard": keyboard}
