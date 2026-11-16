// The n8n template for the Automations department: a WhatsApp that answers and books by itself.
// This is n8n Workflow SDK code, the text the workflow "Atelier · Plantilla · WhatsApp que reserva solo"
// was created from. It is kept here so the template can be rebuilt or changed without retyping it.
// It holds no keys and no real clinic: the card in it is an invented one.
const mensajeWhatsApp = trigger({
  type: 'n8n-nodes-base.whatsAppTrigger',
  version: 1,
  config: {
    name: 'Mensaje de WhatsApp',
    parameters: { updates: ['messages'] },
    credentials: { whatsAppTriggerApi: newCredential('WhatsApp de la clínica (avisos)') }
  },
  output: [{ messages: [{ from: '34600000000', type: 'text', text: { body: 'Hola, ¿tenéis hueco esta semana para ácido hialurónico?' } }], contacts: [{ profile: { name: 'Paciente de prueba' }, wa_id: '34600000000' }], metadata: { phone_number_id: '000000000000000' } }]
});

const cadaManana = trigger({
  type: 'n8n-nodes-base.scheduleTrigger',
  version: 1.4,
  config: {
    name: 'Cada día a las 10:00',
    parameters: { rule: { interval: [{ field: 'days', daysInterval: 1, triggerAtHour: 10, triggerAtMinute: 0 }] } }
  },
  output: [{ timestamp: '2026-11-18T10:00:00.000+01:00' }]
});

const ficha = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: {
    name: 'Ficha de la clínica',
    parameters: {
      mode: 'manual',
      includeOtherFields: false,
      assignments: {
        assignments: [
          { id: 'f-clinica', name: 'clinica', value: 'Clínica Inventada', type: 'string' },
          { id: 'f-tratamientos', name: 'tratamientos_json', value: '[{"name":"una primera valoración","minutes":30},{"name":"ácido hialurónico","minutes":45}]', type: 'string' },
          { id: 'f-horario', name: 'horario_json', value: '{"mon":[["10:00","14:00"],["16:00","20:00"]],"tue":[["10:00","14:00"],["16:00","20:00"]],"wed":[["10:00","14:00"],["16:00","20:00"]],"thu":[["10:00","14:00"],["16:00","20:00"]],"fri":[["10:00","14:00"],["16:00","20:00"]],"sat":[],"sun":[]}', type: 'string' },
          { id: 'f-antelacion', name: 'antelacion_horas', value: '2', type: 'string' },
          { id: 'f-calendario', name: 'calendario', value: 'ID_DEL_CALENDARIO_DE_LA_CLINICA', type: 'string' },
          { id: 'f-numero', name: 'phone_number_id', value: 'ID_DEL_NUMERO_DE_WHATSAPP', type: 'string' },
          { id: 'f-origen', name: 'origen', value: expr('{{ $json.messages?.[0]?.text?.body ? "mensaje" : (($json.messages || $json.statuses) ? "otro" : "horario") }}'), type: 'string' },
          { id: 'f-mensaje', name: 'mensaje', value: expr('{{ $json.messages?.[0]?.text?.body ?? "" }}'), type: 'string' },
          { id: 'f-telefono', name: 'paciente_telefono', value: expr('{{ $json.messages?.[0]?.from ?? "" }}'), type: 'string' },
          { id: 'f-nombre', name: 'paciente_nombre', value: expr('{{ $json.contacts?.[0]?.profile?.name ?? "" }}'), type: 'string' }
        ]
      }
    }
  },
  output: [{ clinica: 'Clínica Inventada', tratamientos_json: '[]', horario_json: '{}', antelacion_horas: '2', calendario: 'ID_DEL_CALENDARIO_DE_LA_CLINICA', phone_number_id: 'ID_DEL_NUMERO_DE_WHATSAPP', origen: 'mensaje', mensaje: 'Hola, ¿tenéis hueco esta semana para ácido hialurónico?', paciente_telefono: '34600000000', paciente_nombre: 'Paciente de prueba' }]
});

const porOrigen = switchCase({
  version: 3.2,
  config: {
    name: '¿Mensaje o recordatorio?',
    parameters: {
      rules: {
        values: [
          { outputKey: 'mensaje', conditions: { options: { caseSensitive: true, leftValue: '', typeValidation: 'strict' }, conditions: [{ leftValue: expr('{{ $json.origen }}'), operator: { type: 'string', operation: 'equals' }, rightValue: 'mensaje' }], combinator: 'and' } },
          { outputKey: 'horario', conditions: { options: { caseSensitive: true, leftValue: '', typeValidation: 'strict' }, conditions: [{ leftValue: expr('{{ $json.origen }}'), operator: { type: 'string', operation: 'equals' }, rightValue: 'horario' }], combinator: 'and' } }
        ]
      }
    }
  },
  output: [{ origen: 'mensaje' }]
});

const leerAgenda = node({
  type: 'n8n-nodes-base.googleCalendar',
  version: 1.4,
  config: {
    name: 'Leer la agenda',
    alwaysOutputData: true,
    parameters: {
      resource: 'event',
      operation: 'getAll',
      calendar: { __rl: true, mode: 'id', value: expr('{{ $json.calendario }}') },
      returnAll: true,
      timeMin: expr('{{ $now }}'),
      timeMax: expr('{{ $now.plus({ days: 22 }) }}'),
      options: { orderBy: 'startTime', recurringEventHandling: 'expand' }
    },
    credentials: { googleCalendarOAuth2Api: newCredential('Agenda de la clínica') }
  },
  output: [{ id: 'abc123', summary: 'ácido hialurónico · Marta', status: 'confirmed', start: { dateTime: '2026-11-19T17:00:00+01:00' }, end: { dateTime: '2026-11-19T17:45:00+01:00' } }]
});

const huecosLibres = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Huecos libres',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `// The same diary rules as Atelier's rehearsal (automations.py): a start is free when it is inside the
// opening hours, after the minimum notice, and inside no other event. Next to each start goes how
// many minutes stay free from it, so a visit only fits where it does not touch the next event.
const ficha = $('Ficha de la clínica').first().json;
const zona = 'Europe/Madrid';
const horario = JSON.parse(ficha.horario_json);
const tratamientos = JSON.parse(ficha.tratamientos_json);
const claves = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const nombres = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
const ocupado = [];
for (const item of $input.all()) {
  const cita = item.json;
  if (!cita || !cita.start || cita.status === 'cancelled') continue;
  if (cita.start.dateTime) {
    ocupado.push([DateTime.fromISO(cita.start.dateTime).toMillis(), DateTime.fromISO(cita.end.dateTime).toMillis()]);
  } else if (cita.start.date) {
    ocupado.push([DateTime.fromISO(cita.start.date, { zone: zona }).toMillis(), DateTime.fromISO(cita.end.date, { zone: zona }).toMillis()]);
  }
}
const ahora = DateTime.now().setZone(zona);
const primero = ahora.plus({ hours: Number(ficha.antelacion_horas) || 0 });
const lineas = [];
for (let d = 0; d <= 21; d++) {
  const dia = ahora.startOf('day').plus({ days: d });
  const partes = [];
  for (const tramo of horario[claves[dia.weekday - 1]] || []) {
    const abre = tramo[0].split(':').map(Number);
    const cierra = tramo[1].split(':').map(Number);
    const fin = dia.set({ hour: cierra[0], minute: cierra[1] }).toMillis();
    let inicio = dia.set({ hour: abre[0], minute: abre[1] });
    while (inicio.toMillis() < fin) {
      const ms = inicio.toMillis();
      if (ms >= primero.toMillis() && !ocupado.some((par) => ms >= par[0] && ms < par[1])) {
        let hasta = fin;
        for (const par of ocupado) if (par[0] > ms && par[0] < hasta) hasta = par[0];
        const libres = Math.round((hasta - ms) / 60000);
        if (libres >= 10) partes.push(inicio.toFormat('HH:mm') + ' (' + libres + ' min)');
      }
      inicio = inicio.plus({ minutes: 30 });
    }
  }
  if (partes.length) lineas.push(dia.toFormat('yyyy-MM-dd') + ' ' + nombres[dia.weekday - 1] + ' (' + dia.toFormat('ZZ') + '): ' + partes.join(', '));
}
const salto = String.fromCharCode(10);
return [{ json: Object.assign({}, ficha, {
  ahora: ahora.toFormat('yyyy-MM-dd HH:mm') + ' (' + nombres[ahora.weekday - 1] + ')',
  huecos: lineas.join(salto) || 'No hay huecos libres en las próximas tres semanas.',
  tratamientos_texto: tratamientos.map((uno) => '- ' + uno.name + ': ' + uno.minutes + ' minutos').join(salto),
  horario_texto: claves.map((clave, i) => '- ' + nombres[i] + ': ' + ((horario[clave] || []).map((tramo) => tramo[0] + '-' + tramo[1]).join(', ') || 'cerrado')).join(salto),
}) }];`
    }
  },
  output: [{ clinica: 'Clínica Inventada', mensaje: 'Hola, ¿tenéis hueco esta semana para ácido hialurónico?', paciente_telefono: '34600000000', paciente_nombre: 'Paciente de prueba', phone_number_id: 'ID_DEL_NUMERO_DE_WHATSAPP', calendario: 'ID_DEL_CALENDARIO_DE_LA_CLINICA', ahora: '2026-11-16 23:14 (lunes)', huecos: '2026-11-17 martes (+01:00): 10:00 (240 min), 10:30 (210 min)', tratamientos_texto: '- ácido hialurónico: 45 minutos', horario_texto: '- lunes: 10:00-14:00, 16:00-20:00' }]
});

const modelo = languageModel({
  type: '@n8n/n8n-nodes-langchain.lmChatAnthropic',
  version: 1.6,
  config: {
    name: 'Modelo que contesta',
    parameters: { model: { __rl: true, mode: 'list', value: 'claude-sonnet-5', cachedResultName: 'Claude Sonnet 5' }, options: { maxTokensToSample: 600 } },
    credentials: { anthropicApi: newCredential('Anthropic para clínicas') }
  }
});

const memoriaPaciente = memory({
  type: '@n8n/n8n-nodes-langchain.memoryBufferWindow',
  version: 1.4,
  config: {
    name: 'Memoria de la conversación',
    parameters: { sessionIdType: 'customKey', sessionKey: expr("{{ $('Ficha de la clínica').item.json.paciente_telefono }}"), contextWindowLength: 10 }
  }
});

const reservar = tool({
  type: 'n8n-nodes-base.googleCalendarTool',
  version: 1.4,
  config: {
    name: 'reservar',
    parameters: {
      resource: 'event',
      operation: 'create',
      calendar: { __rl: true, mode: 'id', value: expr("{{ $('Ficha de la clínica').item.json.calendario }}") },
      start: fromAi('inicio', 'Inicio de la cita: uno de los huecos libres, en formato ISO con el desfase de ese día, por ejemplo 2026-11-19T17:00:00+01:00'),
      end: fromAi('fin', 'Fin de la cita: el inicio más los minutos del tratamiento, en el mismo formato ISO'),
      useDefaultReminders: true,
      additionalFields: {
        summary: fromAi('titulo', 'Tratamiento y nombre de la paciente, por ejemplo: ácido hialurónico · Marta'),
        description: expr("Reservada por WhatsApp por el asistente. Tel: {{ $('Ficha de la clínica').item.json.paciente_telefono }}")
      }
    },
    credentials: { googleCalendarOAuth2Api: newCredential('Agenda de la clínica') }
  }
});

const buscarCita = tool({
  type: 'n8n-nodes-base.googleCalendarTool',
  version: 1.4,
  config: {
    name: 'buscar_cita',
    parameters: {
      resource: 'event',
      operation: 'getAll',
      calendar: { __rl: true, mode: 'id', value: expr("{{ $('Ficha de la clínica').item.json.calendario }}") },
      returnAll: false,
      limit: 5,
      timeMin: expr('{{ $now }}'),
      timeMax: expr('{{ $now.plus({ days: 90 }) }}'),
      options: { query: expr("{{ $('Ficha de la clínica').item.json.paciente_telefono }}"), orderBy: 'startTime' }
    },
    credentials: { googleCalendarOAuth2Api: newCredential('Agenda de la clínica') }
  }
});

const cancelarCita = tool({
  type: 'n8n-nodes-base.googleCalendarTool',
  version: 1.4,
  config: {
    name: 'cancelar_cita',
    parameters: {
      resource: 'event',
      operation: 'delete',
      calendar: { __rl: true, mode: 'id', value: expr("{{ $('Ficha de la clínica').item.json.calendario }}") },
      eventId: fromAi('id_cita', 'El id de la cita, tal como lo devolvió buscar_cita'),
      options: {}
    },
    credentials: { googleCalendarOAuth2Api: newCredential('Agenda de la clínica') }
  }
});

const asistente = node({
  type: '@n8n/n8n-nodes-langchain.agent',
  version: 3.1,
  config: {
    name: 'Asistente de la clínica',
    parameters: {
      promptType: 'define',
      text: expr('{{ $json.mensaje }}'),
      options: {
        maxIterations: 6,
        enableStreaming: false,
        systemMessage: expr(
          'Eres el asistente de {{ $json.clinica }}, una clínica de medicina estética. Atiendes su WhatsApp a cualquier hora.\n\n' +
          'Qué haces: dar cita, cambiarla o cancelarla. Nada más.\n\n' +
          'Cómo hablas: en español, de tú, con mensajes cortos y amables, sin listas largas. Te presentas una vez como «el asistente de {{ $json.clinica }}». Nunca dices que eres una persona.\n\n' +
          'Ahora es {{ $json.ahora }}. Escribe {{ $json.paciente_nombre || "una paciente" }}.\n\n' +
          'Reglas que no se rompen:\n' +
          '- Los huecos salen solo de la lista HUECOS LIBRES de abajo. Nunca inventes un día ni una hora.\n' +
          '- Cada hueco lleva entre paréntesis los minutos que quedan libres desde esa hora. Un tratamiento solo cabe si dura eso o menos.\n' +
          '- Ofrece tres huecos como mucho: dos del primer día que tenga sitio, separados, y uno de otro día. Si la paciente pide un día o una parte del día, busca ahí.\n' +
          '- Cuando elija un hueco de la lista, usa la herramienta reservar. Confirma la cita solo si reservar ha ido bien. Dile el día y la hora, y que el día antes recibirá un recordatorio.\n' +
          '- Para cambiar o cancelar, usa primero buscar_cita, que solo encuentra las citas de quien escribe. Para cancelar, usa cancelar_cita con el id que devuelve. Para cambiar, cancela la antigua después de reservar la nueva.\n' +
          '- No des precios, ni consejo médico, ni opiniones sobre un tratamiento. Di que se lo confirma el equipo de la clínica en cuanto abra.\n' +
          '- Si no entiendes algo o la paciente está molesta, dile que el equipo le escribirá en cuanto abra.\n' +
          '- No pidas datos de salud. Para reservar basta el nombre y el tratamiento.\n\n' +
          'Tratamientos y lo que dura cada cita:\n{{ $json.tratamientos_texto }}\n\n' +
          'Horario de la clínica:\n{{ $json.horario_texto }}\n\n' +
          'HUECOS LIBRES (fecha, día, desfase horario y horas de inicio):\n{{ $json.huecos }}'
        )
      }
    },
    subnodes: { model: modelo, memory: memoriaPaciente, tools: [reservar, buscarCita, cancelarCita] }
  },
  output: [{ output: '¡Hola! Soy el asistente de Clínica Inventada. Para ácido hialurónico tengo estos huecos: martes a las 10:00, martes a las 16:00 o miércoles a las 10:30.' }]
});

const contestar = node({
  type: 'n8n-nodes-base.whatsApp',
  version: 1.1,
  config: {
    name: 'Contestar por WhatsApp',
    parameters: {
      resource: 'message',
      operation: 'send',
      phoneNumberId: expr("{{ $('Huecos libres').item.json.phone_number_id }}"),
      recipientPhoneNumber: expr("{{ $('Huecos libres').item.json.paciente_telefono }}"),
      messageType: 'text',
      textBody: expr('{{ $json.output }}')
    },
    credentials: { whatsAppApi: newCredential('WhatsApp de la clínica') }
  },
  output: [{ messaging_product: 'whatsapp', messages: [{ id: 'wamid.000' }] }]
});

const citasDeManana = node({
  type: 'n8n-nodes-base.googleCalendar',
  version: 1.4,
  config: {
    name: 'Citas de mañana',
    parameters: {
      resource: 'event',
      operation: 'getAll',
      calendar: { __rl: true, mode: 'id', value: expr('{{ $json.calendario }}') },
      returnAll: true,
      timeMin: expr('{{ $now.plus({ days: 1 }).startOf("day") }}'),
      timeMax: expr('{{ $now.plus({ days: 1 }).endOf("day") }}'),
      options: { orderBy: 'startTime', recurringEventHandling: 'expand' }
    },
    credentials: { googleCalendarOAuth2Api: newCredential('Agenda de la clínica') }
  },
  output: [{ id: 'abc123', summary: 'ácido hialurónico · Marta', status: 'confirmed', description: 'Reservada por WhatsApp por el asistente. Tel: 34600000000', start: { dateTime: '2026-11-19T17:00:00+01:00' }, end: { dateTime: '2026-11-19T17:45:00+01:00' } }]
});

const citasConTelefono = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Citas con teléfono',
    parameters: {
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `// Only the appointments the assistant booked carry "Tel: ..." in their description. The clinic's own
// appointments have no phone here, so nobody gets a reminder the clinic did not ask for.
const ficha = $('Ficha de la clínica').first().json;
const nombres = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
const salida = [];
for (const item of $input.all()) {
  const cita = item.json;
  if (!cita || !cita.start || !cita.start.dateTime || cita.status === 'cancelled') continue;
  const telefono = /Tel: ([0-9]{8,15})/.exec(cita.description || '');
  if (!telefono) continue;
  const inicio = DateTime.fromISO(cita.start.dateTime).setZone('Europe/Madrid');
  salida.push({ json: { telefono: telefono[1], dia: nombres[inicio.weekday - 1], hora: inicio.toFormat('HH:mm'), clinica: ficha.clinica, phone_number_id: ficha.phone_number_id } });
}
return salida;`
    }
  },
  output: [{ telefono: '34600000000', dia: 'jueves', hora: '17:00', clinica: 'Clínica Inventada', phone_number_id: 'ID_DEL_NUMERO_DE_WHATSAPP' }]
});

const recordatorio = node({
  type: 'n8n-nodes-base.whatsApp',
  version: 1.1,
  config: {
    name: 'Enviar el recordatorio',
    parameters: {
      resource: 'message',
      operation: 'sendTemplate',
      phoneNumberId: expr('{{ $json.phone_number_id }}'),
      recipientPhoneNumber: expr('{{ $json.telefono }}'),
      template: placeholder('La plantilla de recordatorio aprobada por Meta, como nombre|idioma (por ejemplo recordatorio_cita|es)'),
      components: { component: [{ type: 'body', bodyParameters: { parameter: [{ type: 'text', text: expr('{{ $json.dia }}') }, { type: 'text', text: expr('{{ $json.hora }}') }, { type: 'text', text: expr('{{ $json.clinica }}') }] } }] }
    },
    credentials: { whatsAppApi: newCredential('WhatsApp de la clínica') }
  },
  output: [{ messaging_product: 'whatsapp', messages: [{ id: 'wamid.001', message_status: 'accepted' }] }]
});

const nota = sticky(
  '## Plantilla de Atelier: un WhatsApp que contesta y reserva solo\n\n' +
  '**No publicar esta plantilla.** Para cada clínica se hace una copia y se rellena la «Ficha de la clínica» con lo que July aprobó en el panel de Atelier (Automatizaciones).\n\n' +
  '**Qué hay que rellenar en la copia**\n' +
  '1. Ficha de la clínica: nombre, tratamientos, horario, antelación, id del calendario e id del número de WhatsApp.\n' +
  '2. Las cuatro credenciales: WhatsApp (avisos y envío), agenda de Google y Anthropic.\n' +
  '3. En «Enviar el recordatorio»: la plantilla aprobada por Meta, con tres huecos: día, hora y clínica.\n' +
  '4. La zona horaria del flujo: Europe/Madrid.\n\n' +
  '**Lo que esta primera versión todavía no hace**\n' +
  '- No vuelve a comprobar el hueco con código justo antes de reservar: el asistente elige de una lista recién leída de la agenda.\n' +
  '- No avisa al equipo por otro canal cuando deja algo para una persona.\n' +
  '- Solo entiende mensajes de texto.\n\n' +
  '**Cuesta dinero al activarse:** mensajes de WhatsApp, el modelo y las ejecuciones de n8n. Nada se activa sin el sí de July.',
  [],
  { color: 4 }
);

export default workflow('atelier-plantilla-whatsapp-reserva', 'Atelier · Plantilla · WhatsApp que reserva solo')
  .add(mensajeWhatsApp)
  .to(ficha)
  .to(porOrigen
    .onCase(0, leerAgenda.to(huecosLibres.to(asistente.to(contestar))))
    .onCase(1, citasDeManana.to(citasConTelefono.to(recordatorio))))
  .add(cadaManana)
  .to(ficha)
  .add(nota)
  .group('Contestar y reservar', [leerAgenda, huecosLibres, asistente, modelo, memoriaPaciente, reservar, buscarCita, cancelarCita, contestar], {
    description: 'Lee la agenda, calcula los huecos libres, el asistente contesta y reserva, y la respuesta sale por WhatsApp.'
  })
  .group('Recordatorio del día antes', [citasDeManana, citasConTelefono, recordatorio], {
    description: 'Cada mañana busca las citas de mañana que reservó el asistente y manda el recordatorio.'
  });
