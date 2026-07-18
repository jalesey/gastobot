function checkBancoChile() {
  var props = PropertiesService.getScriptProperties();
  var BOT_TOKEN = props.getProperty("BOT_TOKEN");
  var CHAT_ID   = props.getProperty("CHAT_ID");
  var SHEET_ID  = props.getProperty("SHEET_ID");

  var ss = SpreadsheetApp.openById(SHEET_ID);
  var wsGastos = ss.getSheetByName("Gastos");
  var wsMap = ss.getSheetByName("Comercios");

  // Mantenemos 1 día para detectar el correo de Prime Video en las pruebas
  var threads = GmailApp.search('from:enviodigital@bancochile.cl newer_than:30d');
  var props = PropertiesService.getScriptProperties();

  console.log("🔍 Hilos encontrados: " + threads.length);

  for (var i = 0; i < threads.length; i++) {
    var msgs = threads[i].getMessages();
    
    for (var j = 0; j < msgs.length; j++) {
      var msg = msgs[j];
      var emailId = msg.getId();

      // --- IMPORTANTE: ---
      // Si quieres volver a procesar el MISMO correo muchas veces para probar,
      // mantén comentada la siguiente línea.
      // Cuando ya lo uses en la vida real, DESCOMENTALA para no repetir gastos.
      
      if (props.getProperty(emailId)) continue; 

      var subject = msg.getSubject() || "";
      var body = msg.getPlainBody() || "";
      // Limpiamos saltos de línea raros para facilitar la lectura
      var text = (subject + " " + body).replace(/\s+/g, " ");

      var data = extractBancoChileData(text);

      if (!data || !data.monto) {
        // Si falla, lo ignoramos silenciosamente en el log
        console.log("Salto correo sin datos de compra: " + subject);
        continue;
      }

      console.log("✅ Datos detectados: " + data.comercio_raw);

      // Valores por defecto
      var desc = "Compra Tarjeta Crédito";
      // Intentamos sacar fecha, si falla usamos la fecha del correo
      var fh = extractFechaHora(text) || { 
          fecha: Utilities.formatDate(msg.getDate(), "GMT-3", "dd/MM/yyyy"), 
          hora: Utilities.formatDate(msg.getDate(), "GMT-3", "HH:mm") 
      };

      var comercioRaw = data.comercio_raw;
      var monto = data.monto;
      var mapping = findMapping(wsMap, comercioRaw);

      if (mapping) {
        // --- CASO 1: YA CONOCIDO ---
        appendGasto(wsGastos, {
          fecha: fh.fecha, hora: fh.hora, descripcion: desc,
          monto: monto, categoria: mapping.categoria || "Otros",
          comercio_raw: comercioRaw, comercio_alias: mapping.alias,
          usuario: "gmail", chat_id: CHAT_ID, email_id: emailId
        });

        // Enviamos mensaje simple (sin botones)
        sendTelegram(BOT_TOKEN, CHAT_ID, 
          "✅ <b>Gasto Registrado:</b>\n" +
          "- " + fh.fecha + " a las " + fh.hora + "\n" +
          "- " + mapping.alias + "\n" +
          "- $" + monto + "\n" +
          "- <i>" + mapping.categoria + "</i>"
        );

} else {
        // --- CASO 2: NUEVO / DESCONOCIDO (CON BOTONES) ---
        
        // 1. Guardar en Pendientes (Se mantiene igual)
        upsertPendiente(ss, {
          email_id: emailId, fecha_email: fh.fecha, hora_email: fh.hora,
          monto: monto, comercio_raw: comercioRaw, desc: desc, estado: "PENDIENTE"
        });

        // 2. Definir Botones (MODIFICADO)
        // Ahora el paso 1 es decidir el nombre (Alias)
        var keyboard = {
          "inline_keyboard": [
            [
              // NUEVA OPCIÓN: Buscar en la hoja "Comercios"
              {
                  "text": "🔍 Buscar si ya existe", 
                  "callback_data": "CHECK|" + emailId
              }
            ],
            [
              // Opción A: Mantener el nombre tal cual llega del banco
              // Usamos un callback nuevo 'KEEP' que deberás gestionar en Python
              {
                  "text": "✅ Mantener: " + comercioRaw, 
                  "callback_data": "KEEP|" + emailId
              }
            ],
            [
              // Opción B: Cambiar el nombre (Tu lógica 'OTRO' ya hace esto: pide escribir)
              {
                  "text": "✏️ Asignar nuevo nombre...", 
                  "callback_data": "OTRO|" + emailId
              }
            ],
            [
              {
                  "text": "❌ Ignorar", 
                  "callback_data": "IGNORE|" + emailId
              }
            ]
          ]
        };

        // 3. Mensaje ajustado
        var mensaje = "📩 <b>Nuevo gasto detectado:</b>\n" +
                      "- Fecha: " + fh.fecha +
                      "- Comercio original: <b>" + comercioRaw + "</b>\n" +
                      "- Monto: $" + monto + "\n\n" +
                      "¿Quieres mantener este nombre o asignar uno nuevo?";

        sendTelegram(BOT_TOKEN, CHAT_ID, mensaje, keyboard);
      }
      
      // Marcamos como procesado (Descomentar para producción)
      props.setProperty(emailId, "OK");
    }
  }
}

// --- REGEX PROBADO Y VERIFICADO ---
function extractBancoChileData(text) {
  var montoMatch = text.match(/compra por \$([0-9\.]+)/i);
  
  // Ancla al patrón "****XXXX en COMERCIO el DD/MM/YYYY HH:MM"
  var comercioMatch = text.match(/\*{4}\d+\s+en\s+(.+?)\s+el\s+\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}/i);

  if (!montoMatch) return null;

  return {
    monto: montoMatch[1].replace(/\./g, ""),
    comercio_raw: comercioMatch ? comercioMatch[1].trim() : "DESCONOCIDO"
  };
}

function extractFechaHora(text) {
  var m = text.match(/el\s+(\d{2}\/\d{2}\/\d{4})\s+(\d{2}:\d{2})/);
  if (!m) return null;
  return { fecha: m[1], hora: m[2] };
}

function findMapping(wsMap, comercioRaw) {
  var values = wsMap.getDataRange().getValues();
  var target = String(comercioRaw).trim().toUpperCase();
  for (var i = 1; i < values.length; i++) {
    var raw = String(values[i][0] || "").trim().toUpperCase();
    if (raw === target) {
      return {
        alias: String(values[i][1] || "").trim(),
        categoria: String(values[i][2] || "").trim()
      };
    }
  }
  return null;
}

function appendGasto(wsGastos, g) {
  wsGastos.appendRow([
    g.fecha, g.hora, g.descripcion, g.monto, g.categoria,
    g.comercio_raw, g.comercio_alias, g.usuario, g.chat_id, g.email_id
  ]);
}

function upsertPendiente(ss, p) {
  var tab = "Pendientes";
  var ws = ss.getSheetByName(tab);
  if (!ws) {
    ws = ss.insertSheet(tab);
    ws.appendRow(["email_id", "fecha_email", "hora_email", "monto", "comercio_raw", "desc", "estado"]);
  }
  var values = ws.getDataRange().getValues();
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][0]) === String(p.email_id)) return; // Ya existe
  }
  ws.appendRow([p.email_id, p.fecha_email, p.hora_email, p.monto, p.comercio_raw, p.desc, p.estado]);
}

function sendTelegram(token, chatId, text, keyboard) {
  var url = "https://api.telegram.org/bot" + token + "/sendMessage";
  
  // Aquí activamos HTML y pasamos el teclado si existe
  var payload = { 
    chat_id: chatId, 
    text: text, 
    parse_mode: "HTML" 
  };

  if (keyboard) {
    payload.reply_markup = JSON.stringify(keyboard);
  }

  try {
    UrlFetchApp.fetch(url, {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
    console.log("📨 Mensaje enviado a Telegram");
  } catch (e) {
    console.log("❌ Error enviando a Telegram: " + e.toString());
  }
}
