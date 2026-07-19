// =========================================================
// CONFIG
// =========================================================
function getConfig_() {
  var props = PropertiesService.getScriptProperties();
  return {
    BOT_TOKEN: props.getProperty("BOT_TOKEN"),
    CHAT_ID: props.getProperty("CHAT_ID"),
    ADMIN_CHAT_ID: props.getProperty("ADMIN_CHAT_ID") || props.getProperty("CHAT_ID"),
    SHEET_ID: props.getProperty("SHEET_ID")
  };
}

function getSpreadsheet_(cfg) {
  return SpreadsheetApp.openById(cfg.SHEET_ID);
}

// =========================================================
// LECTURA DE GMAIL (sin cambios respecto al original)
// =========================================================
function checkBancoChile() {
  var cfg = getConfig_();
  var ss = getSpreadsheet_(cfg);
  var wsGastos = ss.getSheetByName("Gastos");
  var wsMap = ss.getSheetByName("Comercios");
  var props = PropertiesService.getScriptProperties();

  var threads = GmailApp.search('from:enviodigital@bancochile.cl newer_than:30d');
  console.log("🔍 Hilos encontrados: " + threads.length);

  for (var i = 0; i < threads.length; i++) {
    var msgs = threads[i].getMessages();

    for (var j = 0; j < msgs.length; j++) {
      var msg = msgs[j];
      var emailId = msg.getId();

      if (props.getProperty(emailId)) continue;

      var subject = msg.getSubject() || "";
      var body = msg.getPlainBody() || "";
      var text = (subject + " " + body).replace(/\s+/g, " ");

      var data = extractBancoChileData(text);

      if (!data || !data.monto) {
        console.log("Salto correo sin datos de compra: " + subject);
        continue;
      }

      console.log("✅ Datos detectados: " + data.comercio_raw);

      var desc = "Compra Tarjeta Crédito";
      var fh = extractFechaHora(text) || {
        fecha: Utilities.formatDate(msg.getDate(), "GMT-3", "dd/MM/yyyy"),
        hora: Utilities.formatDate(msg.getDate(), "GMT-3", "HH:mm")
      };

      var comercioRaw = data.comercio_raw;
      var monto = data.monto;
      var mapping = findMapping(wsMap, comercioRaw);

      if (mapping) {
        appendGasto(wsGastos, {
          fecha: fh.fecha, hora: fh.hora, descripcion: desc,
          monto: monto, categoria: mapping.categoria || "Otros",
          comercio_raw: comercioRaw, comercio_alias: mapping.alias,
          usuario: "gmail", chat_id: cfg.CHAT_ID, email_id: emailId
        });

        sendTelegram(cfg.BOT_TOKEN, cfg.CHAT_ID,
          "✅ <b>Gasto Registrado:</b>\n" +
          "- " + fh.fecha + " a las " + fh.hora + "\n" +
          "- " + mapping.alias + "\n" +
          "- $" + monto + "\n" +
          "- <i>" + mapping.categoria + "</i>"
        );

      } else {
        upsertPendiente(ss, {
          email_id: emailId, fecha_email: fh.fecha, hora_email: fh.hora,
          monto: monto, comercio_raw: comercioRaw, desc: desc, estado: "PENDIENTE"
        });

        var mensaje = "📩 <b>Nuevo gasto detectado:</b>\n" +
          "- Fecha: " + fh.fecha +
          "- Comercio original: <b>" + comercioRaw + "</b>\n" +
          "- Monto: $" + monto + "\n\n" +
          "¿Quieres mantener este nombre o asignar uno nuevo?";

        sendTelegram(cfg.BOT_TOKEN, cfg.CHAT_ID, mensaje, keyboardNuevoGasto_(emailId));
      }

      props.setProperty(emailId, "OK");
    }
  }
}

function extractBancoChileData(text) {
  var montoMatch = text.match(/compra por \$([0-9\.]+)/i);
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

function upsertMapping_(wsMap, comercioRaw, alias, categoria) {
  var values = wsMap.getDataRange().getValues();
  var target = String(comercioRaw).trim().toUpperCase();
  for (var i = 1; i < values.length; i++) {
    var raw = String(values[i][0] || "").trim().toUpperCase();
    if (raw === target) {
      wsMap.getRange(i + 1, 2).setValue(alias);
      if (categoria) wsMap.getRange(i + 1, 3).setValue(categoria);
      return;
    }
  }
  wsMap.appendRow([comercioRaw, alias, categoria || ""]);
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
    if (String(values[i][0]) === String(p.email_id)) return;
  }
  ws.appendRow([p.email_id, p.fecha_email, p.hora_email, p.monto, p.comercio_raw, p.desc, p.estado]);
}

// Busca una fila de "Pendientes" por email_id. Devuelve {rowIndex, data} o null.
function getPendiente_(ss, emailId) {
  var ws = ss.getSheetByName("Pendientes");
  var values = ws.getDataRange().getValues();
  for (var i = 1; i < values.length; i++) {
    var row = values[i];
    if (row.length < 7) continue;
    if (String(row[0]).trim() === String(emailId).trim()) {
      return {
        rowIndex: i + 1,
        data: {
          email_id: row[0],
          fecha_email: row[1],
          hora_email: row[2],
          monto: String(row[3]).replace(/\./g, "").replace(/,/g, ""),
          comercio_raw: row[4],
          desc: row[5] || "Compra Tarjeta Crédito",
          estado: row[6] || ""
        }
      };
    }
  }
  return null;
}

function marcarPendienteFila_(ss, rowIndex, estado) {
  ss.getSheetByName("Pendientes").getRange(rowIndex, 7).setValue(estado);
}

function marcarPendientePorEmail_(ss, emailId, estado) {
  var info = getPendiente_(ss, emailId);
  if (info) marcarPendienteFila_(ss, info.rowIndex, estado);
}

// =========================================================
// TELEGRAM: helpers de bajo nivel
// =========================================================
function callTelegram_(token, method, payload) {
  var url = "https://api.telegram.org/bot" + token + "/" + method;
  try {
    var res = UrlFetchApp.fetch(url, {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
    return JSON.parse(res.getContentText());
  } catch (e) {
    console.log("❌ Error llamando a Telegram (" + method + "): " + e.toString());
    return null;
  }
}

function sendTelegram(token, chatId, text, keyboard) {
  var payload = { chat_id: chatId, text: text, parse_mode: "HTML" };
  if (keyboard) payload.reply_markup = JSON.stringify(keyboard);
  return callTelegram_(token, "sendMessage", payload);
}

function editMessageText_(token, chatId, messageId, text, keyboard) {
  var payload = { chat_id: chatId, message_id: messageId, text: text, parse_mode: "HTML" };
  if (keyboard) payload.reply_markup = JSON.stringify(keyboard);
  return callTelegram_(token, "editMessageText", payload);
}

function answerCallback_(token, callbackQueryId, text, showAlert) {
  var payload = { callback_query_id: callbackQueryId };
  if (text) payload.text = text;
  if (showAlert) payload.show_alert = true;
  callTelegram_(token, "answerCallbackQuery", payload);
}

function deleteMessage_(token, chatId, messageId) {
  callTelegram_(token, "deleteMessage", { chat_id: chatId, message_id: messageId });
}

// =========================================================
// TECLADOS
// =========================================================
function keyboardNuevoGasto_(emailId) {
  return {
    inline_keyboard: [
      [{ text: "🔍 Buscar si ya existe", callback_data: "CHECK|" + emailId }],
      [{ text: "✏️ Asignar nuevo nombre...", callback_data: "OTRO|" + emailId }],
      [{ text: "❌ Ignorar", callback_data: "IGNORE|" + emailId }]
    ]
  };
}

function keyboardConfirmarOAsignar_(emailId, comercioRaw) {
  return {
    inline_keyboard: [
      [{ text: "✅ Mantener: " + comercioRaw, callback_data: "KEEP|" + emailId }],
      [{ text: "✏️ Asignar nuevo nombre...", callback_data: "OTRO|" + emailId }],
      [{ text: "❌ Ignorar", callback_data: "IGNORE|" + emailId }]
    ]
  };
}

function buildCategoryKeyboard_(ss, emailId) {
  var ws = ss.getSheetByName("Comercios");
  var values = ws.getDataRange().getValues();
  var categorias = {};
  for (var i = 1; i < values.length; i++) {
    var cat = String(values[i][2] || "").trim();
    if (cat) categorias[cat] = true;
  }
  var base = ["Comida", "Supermercado", "Salud", "Transporte", "Hogar", "Ocio"];
  base.forEach(function (c) { categorias[c] = true; });
  var todas = Object.keys(categorias).sort();

  var keyboard = [];
  var row = [];
  todas.forEach(function (cat) {
    row.push({ text: cat, callback_data: "CAT|" + emailId + "|" + cat });
    if (row.length === 2) { keyboard.push(row); row = []; }
  });
  if (row.length) keyboard.push(row);

  keyboard.push([{ text: "➕ Nueva Categoría...", callback_data: "NEW_CAT|" + emailId }]);
  keyboard.push([{ text: "❌ Cancelar", callback_data: "IGNORE|" + emailId }]);

  return { inline_keyboard: keyboard };
}

// =========================================================
// AUTORIZACIÓN (hoja "Usuarios")
// =========================================================
function getEstadoUsuario_(ss, chatId) {
  var ws = ss.getSheetByName("Usuarios");
  var values = ws.getDataRange().getValues();
  var target = String(chatId);
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][0]).trim() === target) {
      return String(values[i][2] || "").trim().toUpperCase();
    }
  }
  return null;
}

function isAuthorized_(ss, chatId) {
  return getEstadoUsuario_(ss, chatId) === "AUTORIZADO";
}

function upsertUsuario_(ss, chatId, nombre, estado) {
  var ws = ss.getSheetByName("Usuarios");
  var values = ws.getDataRange().getValues();
  var target = String(chatId);
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][0]).trim() === target) {
      ws.getRange(i + 1, 2).setValue(nombre);
      ws.getRange(i + 1, 3).setValue(estado);
      return;
    }
  }
  ws.appendRow([String(chatId), nombre, estado, Utilities.formatDate(new Date(), "GMT-3", "yyyy-MM-dd")]);
}

function requestAccess_(cfg, ss, chatId, nombre) {
  var estado = getEstadoUsuario_(ss, chatId);
  if (estado === "PENDIENTE") {
    sendTelegram(cfg.BOT_TOKEN, chatId, "⏳ Tu solicitud ya fue enviada. Espera la aprobación.");
    return;
  }
  if (estado === "RECHAZADO") {
    sendTelegram(cfg.BOT_TOKEN, chatId, "⛔ Tu acceso fue rechazado.");
    return;
  }

  upsertUsuario_(ss, chatId, nombre, "PENDIENTE");

  var keyboard = {
    inline_keyboard: [
      [{ text: "✅ Aprobar a " + nombre, callback_data: "AUTH_OK|" + chatId + "|" + nombre }],
      [{ text: "❌ Rechazar", callback_data: "AUTH_DENY|" + chatId + "|" + nombre }]
    ]
  };

  sendTelegram(cfg.BOT_TOKEN, cfg.ADMIN_CHAT_ID,
    "🔔 <b>Solicitud de acceso:</b>\n👤 " + nombre + "\n🆔 <code>" + chatId + "</code>",
    keyboard
  );

  sendTelegram(cfg.BOT_TOKEN, chatId,
    "⏳ Tu solicitud fue enviada al administrador. Te avisaré cuando seas aprobado."
  );
}

// =========================================================
// ESTADO DE CONVERSACIÓN ("esperando alias/categoría")
// Se guarda por mensaje (chatId + messageId), no por chat,
// para no mezclar respuestas cuando hay varios gastos pendientes.
// =========================================================
function setAwait_(chatId, messageId, info) {
  PropertiesService.getScriptProperties().setProperty(
    "AWAIT_" + chatId + "_" + messageId, JSON.stringify(info)
  );
}

function getAwait_(chatId, messageId) {
  var raw = PropertiesService.getScriptProperties().getProperty("AWAIT_" + chatId + "_" + messageId);
  return raw ? JSON.parse(raw) : null;
}

function deleteAwait_(chatId, messageId) {
  PropertiesService.getScriptProperties().deleteProperty("AWAIT_" + chatId + "_" + messageId);
}

// =========================================================
// WEBHOOK DE TELEGRAM
// =========================================================
function doPost(e) {
  try {
    var update = JSON.parse(e.postData.contents);
    if (update.callback_query) {
      handleCallback_(update.callback_query);
    } else if (update.message) {
      handleMessage_(update.message);
    }
  } catch (err) {
    console.log("❌ Error en doPost: " + err.stack);
  }
  return ContentService.createTextOutput("OK");
}

function fullName_(from) {
  var nombre = (from.first_name || "") + (from.last_name ? " " + from.last_name : "");
  return nombre.trim() || from.username || String(from.id);
}

function handleMessage_(msg) {
  var cfg = getConfig_();
  var ss = getSpreadsheet_(cfg);
  var chatId = msg.chat.id;
  var text = (msg.text || "").trim();
  var nombre = fullName_(msg.from);

  // Respuesta (reply) a un mensaje que pedía alias o categoría nueva
  if (msg.reply_to_message) {
    var awaiting = getAwait_(chatId, msg.reply_to_message.message_id);
    if (awaiting) {
      deleteAwait_(chatId, msg.reply_to_message.message_id);
      handleAwaitedReply_(cfg, ss, chatId, msg, awaiting, text);
      return;
    }
  }

  if (text.indexOf("/start") === 0) {
    if (!isAuthorized_(ss, chatId)) { requestAccess_(cfg, ss, chatId, nombre); return; }
    sendTelegram(cfg.BOT_TOKEN, chatId,
      "Hola 👋 Soy tu bot de gastos.\n\n" +
      "Cuando llegue un correo de BancoChile:\n" +
      "- Si el comercio es conocido: se registra solo.\n" +
      "- Si no: te pediré que lo clasifiques con los botones.\n"
    );
    return;
  }

  if (text.indexOf("/help") === 0) {
    if (!isAuthorized_(ss, chatId)) { requestAccess_(cfg, ss, chatId, nombre); return; }
    sendTelegram(cfg.BOT_TOKEN, chatId, "Comandos:\n/start\n/help\n/chatid");
    return;
  }

  if (text.indexOf("/chatid") === 0) {
    if (!isAuthorized_(ss, chatId)) { requestAccess_(cfg, ss, chatId, nombre); return; }
    sendTelegram(cfg.BOT_TOKEN, chatId, "Tu chat_id es: " + chatId);
    return;
  }

  if (!isAuthorized_(ss, chatId)) {
    requestAccess_(cfg, ss, chatId, nombre);
    return;
  }

  sendTelegram(cfg.BOT_TOKEN, chatId, "Te leo 👀 Esperando gastos...");
}

function handleAwaitedReply_(cfg, ss, chatId, msg, awaiting, text) {
  var promptMessageId = msg.reply_to_message.message_id;

  if (awaiting.type === "ALIAS") {
    PropertiesService.getScriptProperties().setProperty("TEMP_ALIAS_" + awaiting.emailId, text);
    editMessageText_(cfg.BOT_TOKEN, chatId, promptMessageId,
      "✅ Alias guardado: <b>" + text + "</b>\n\n📂 Selecciona la <b>CATEGORÍA</b>:",
      buildCategoryKeyboard_(ss, awaiting.emailId));
    deleteMessage_(cfg.BOT_TOKEN, chatId, msg.message_id);
    return;
  }

  if (awaiting.type === "NEWCAT") {
    var categoria = text.replace(/\w\S*/g, function (t) {
      return t.charAt(0).toUpperCase() + t.substr(1).toLowerCase();
    });
    deleteMessage_(cfg.BOT_TOKEN, chatId, msg.message_id);
    finalizarGasto_(cfg, ss, chatId, promptMessageId, awaiting.emailId, categoria);
    return;
  }
}

function handleCallback_(cq) {
  var cfg = getConfig_();
  var ss = getSpreadsheet_(cfg);
  var parts = cq.data.split("|");
  var action = parts[0];
  var chatId = cq.message.chat.id;
  var messageId = cq.message.message_id;

  // Las acciones de autorización no requieren estar autorizado.
  if (action === "AUTH_OK" || action === "AUTH_DENY") {
    answerCallback_(cfg.BOT_TOKEN, cq.id);
    var targetChatId = parts[1];
    var nombre = parts[2] || targetChatId;

    if (action === "AUTH_OK") {
      upsertUsuario_(ss, targetChatId, nombre, "AUTORIZADO");
      editMessageText_(cfg.BOT_TOKEN, chatId, messageId, "✅ " + nombre + " autorizado.");
      sendTelegram(cfg.BOT_TOKEN, targetChatId, "✅ ¡Acceso aprobado! Ya puedes usar el bot.");
    } else {
      upsertUsuario_(ss, targetChatId, nombre, "RECHAZADO");
      editMessageText_(cfg.BOT_TOKEN, chatId, messageId, "❌ " + nombre + " rechazado.");
    }
    return;
  }

  if (!isAuthorized_(ss, chatId)) {
    answerCallback_(cfg.BOT_TOKEN, cq.id, "⛔ No autorizado.", true);
    return;
  }
  answerCallback_(cfg.BOT_TOKEN, cq.id);

  var emailId = parts[1];

  if (action === "IGNORE") {
    marcarPendientePorEmail_(ss, emailId, "IGNORADO");
    editMessageText_(cfg.BOT_TOKEN, chatId, messageId, "❌ Gasto descartado.");
    return;
  }

  if (action === "KEEP") {
    var info = getPendiente_(ss, emailId);
    if (!info) { editMessageText_(cfg.BOT_TOKEN, chatId, messageId, "⚠️ Error: No encontré el gasto en Pendientes."); return; }
    editMessageText_(cfg.BOT_TOKEN, chatId, messageId,
      "✅ Alias: <b>" + info.data.comercio_raw + "</b>\n\n📂 Selecciona la <b>CATEGORÍA</b>:",
      buildCategoryKeyboard_(ss, emailId));
    return;
  }

  if (action === "OTRO") {
    var info2 = getPendiente_(ss, emailId);
    var nombreBanco = info2 ? info2.data.comercio_raw : "este comercio";
    editMessageText_(cfg.BOT_TOKEN, chatId, messageId,
      "✍️ <b>Nuevo Alias:</b>\nResponde a ESTE mensaje (usa 'Responder') escribiendo cómo quieres llamar a: <i>" + nombreBanco + "</i>");
    setAwait_(chatId, messageId, { emailId: emailId, type: "ALIAS" });
    return;
  }

  if (action === "NEW_CAT") {
    editMessageText_(cfg.BOT_TOKEN, chatId, messageId,
      "✍️ Responde a ESTE mensaje (usa 'Responder') con el nombre de la <b>NUEVA CATEGORÍA</b>:");
    setAwait_(chatId, messageId, { emailId: emailId, type: "NEWCAT" });
    return;
  }

  if (action === "CHECK") {
    var info3 = getPendiente_(ss, emailId);
    if (!info3) { editMessageText_(cfg.BOT_TOKEN, chatId, messageId, "⚠️ Error: No encontré el gasto en Pendientes."); return; }
    var comercioRaw = info3.data.comercio_raw;
    var mapping = findMapping(ss.getSheetByName("Comercios"), comercioRaw);

    if (mapping && mapping.alias && mapping.categoria) {
      finalizarGasto_(cfg, ss, chatId, messageId, emailId, mapping.categoria, mapping.alias);
    } else {
      editMessageText_(cfg.BOT_TOKEN, chatId, messageId,
        "❌ No encontré <b>" + comercioRaw + "</b> en tus registros.\n\n¿Qué deseas hacer?",
        keyboardConfirmarOAsignar_(emailId, comercioRaw));
    }
    return;
  }

  if (action === "CAT") {
    var categoria = parts[2];
    var aliasGuardado = PropertiesService.getScriptProperties().getProperty("TEMP_ALIAS_" + emailId);
    finalizarGasto_(cfg, ss, chatId, messageId, emailId, categoria, aliasGuardado);
    return;
  }
}

function finalizarGasto_(cfg, ss, chatId, messageId, emailId, categoria, aliasManual) {
  var info = getPendiente_(ss, emailId);
  if (!info) {
    var msg = "⚠️ Ya no encuentro ese gasto pendiente.";
    if (messageId) editMessageText_(cfg.BOT_TOKEN, chatId, messageId, msg);
    else sendTelegram(cfg.BOT_TOKEN, chatId, msg);
    return;
  }

  var p = info.data;
  var alias = aliasManual || p.comercio_raw;

  appendGasto(ss.getSheetByName("Gastos"), {
    fecha: p.fecha_email, hora: p.hora_email, descripcion: p.desc, monto: p.monto,
    categoria: categoria, comercio_raw: p.comercio_raw, comercio_alias: alias,
    usuario: "telegram", chat_id: chatId, email_id: emailId
  });
  upsertMapping_(ss.getSheetByName("Comercios"), p.comercio_raw, alias, categoria);
  marcarPendienteFila_(ss, info.rowIndex, "OK");
  PropertiesService.getScriptProperties().deleteProperty("TEMP_ALIAS_" + emailId);

  var texto = "✅ <b>Listo.</b> Gasto de <b>$" + p.monto + "</b>\n🏪 <b>" + alias + "</b>\n📂 <b>" + categoria + "</b>";
  if (messageId) editMessageText_(cfg.BOT_TOKEN, chatId, messageId, texto);
  else sendTelegram(cfg.BOT_TOKEN, chatId, texto);
}
