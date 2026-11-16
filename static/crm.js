// Shared by the two CRM pages: the words on screen, small helpers and the window for noting a contact.

const STAGES = {
  to_contact: "Por contactar", contacted: "Contactada", replied: "Respondió",
  call_booked: "Llamada agendada", call_done: "Llamada hecha", proposal_sent: "Propuesta enviada",
  client: "Cliente", later: "Más adelante", not_interested: "No interesada",
};
// The path a clinic walks, in order. The same list is in crm.py.
const PATH = ["to_contact", "contacted", "replied", "call_booked", "call_done", "proposal_sent", "client"];
const KINDS = {
  message_sent: "Le escribí", audit_sent: "Le envié la mini auditoría", reply: "Me respondió",
  call_booked: "Agendamos la llamada", call: "Hicimos la llamada de diagnóstico",
  proposal_sent: "Le envié la propuesta", note: "Nota para mí", stage: "Cambio de etapa",
};
const CHANNELS = {
  instagram: "Instagram", whatsapp: "WhatsApp", email: "Correo",
  phone: "Teléfono", linkedin: "LinkedIn", in_person: "En persona",
};
const CLINIC_KINDS = { independent: "Independiente", chain: "Cadena o grande" };
const NETWORKS = {
  instagram: "Instagram", tiktok: "TikTok", facebook: "Facebook", x: "X", threads: "Threads", linkedin: "LinkedIn",
};
// After each kind of contact: what usually comes next, and in how many days. It comes from my method:
// the written proposal goes out within a day of the diagnosis call. No days means "I pick the day".
const NEXT = {
  message_sent: ["Volver a escribir si no responde", 3],
  audit_sent: ["Preguntar si ha visto la mini auditoría", 3],
  reply: ["Contestar y proponer la llamada de diagnóstico", 0],
  call_booked: ["Llamada de diagnóstico", null],
  call: ["Enviar la propuesta escrita", 1],
  proposal_sent: ["Preguntar por la propuesta", 3],
};
// The contact I most likely want to note, by the stage the clinic is in.
const LIKELY = {
  to_contact: "message_sent", contacted: "message_sent", replied: "call_booked", call_booked: "call",
  call_done: "proposal_sent", proposal_sent: "message_sent", later: "message_sent",
};

const $ = (id) => document.getElementById(id);

// Build elements with textContent only, so nothing typed into the CRM is ever run as HTML.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function link(text, href, className) {
  const node = el("a", className, text);
  node.href = href;
  if (/^https?:/i.test(href)) {
    node.target = "_blank";
    node.rel = "noopener noreferrer";
  }
  return node;
}

function option(value, text) {
  const node = el("option", "", text);
  node.value = value;
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "Revisa lo que has escrito (error " + response.status + ").");
  }
  return response.json();
}

function send(method, path, data) {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
}

function showError(message) {
  $("error").textContent = message;
  $("error").style.display = message ? "block" : "none";
  if (message) $("error").scrollIntoView({ block: "nearest" });
}

// Do one thing on the server and then draw the page again. If it goes wrong, the page says why
// and the message stays until the next thing I do.
async function act(work, redraw, done) {
  showError("");
  try {
    await work();
    if (done) flash(done);
  } catch (error) { showError(error.message); }
  await redraw();
}

// A short confirmation at the bottom of the screen. It goes away by itself.
let flashTimer = null;
function flash(message) {
  $("flash").textContent = message;
  $("flash").classList.add("on");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => $("flash").classList.remove("on"), 4500);
}

// A button that deletes asks once more before doing it: the first click only changes its words.
function askTwice(button, sureText, action) {
  const calm = button.textContent;
  let timer = null;
  const relax = () => { delete button.dataset.sure; button.textContent = calm; };
  button.addEventListener("click", () => {
    if (button.dataset.sure) {
      clearTimeout(timer);
      relax();
      action();
      return;
    }
    button.dataset.sure = "1";
    button.textContent = sureText;
    timer = setTimeout(relax, 5000);
  });
}

// ---- Days. They travel as YYYY-MM-DD and are read as days of my calendar, never as UTC moments. ----

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function isoDay(date) {
  const two = (number) => String(number).padStart(2, "0");
  return date.getFullYear() + "-" + two(date.getMonth() + 1) + "-" + two(date.getDate());
}

function parseDay(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function addDays(iso, days) {
  const date = parseDay(iso);
  date.setDate(date.getDate() + days);
  return isoDay(date);
}

function daysBetween(fromIso, toIso) {
  return Math.round((parseDay(toIso) - parseDay(fromIso)) / 86400000);
}

function shortDay(iso) {
  const date = parseDay(iso);
  const year = date.getFullYear() === new Date().getFullYear() ? "" : " " + date.getFullYear();
  return date.getDate() + " " + MONTHS[date.getMonth()] + year;
}

// "hoy", "mañana", "hace 3 días", "en 5 días"; further away, the date itself.
function sayDay(iso, today) {
  const days = daysBetween(today, iso);
  if (days === 0) return "hoy";
  if (days === 1) return "mañana";
  if (days === -1) return "ayer";
  if (days < 0 && days >= -30) return "hace " + (-days) + " días";
  if (days > 0 && days <= 6) return "en " + days + " días";
  return shortDay(iso);
}

// ---- Ways to reach a clinic or a person ----

const isWeb = (value) => /^https?:\/\//i.test(value || "");

// The links only open my own apps (WhatsApp, the phone, the mail): Atelier never sends anything.
function reachLinks(who) {
  const box = el("div", "reach");
  const digits = (who.phone || "").replace(/\D/g, "");
  if (digits) {
    const national = digits.replace(/^(00)?34(?=\d{9}$)/, "");
    const full = national.length === 9 ? "34" + national : digits;
    // Spanish mobiles start with 6 or 7: those can have WhatsApp.
    if (/^[67]\d{8}$/.test(national)) box.append(link("WhatsApp", "https://wa.me/" + full));
    box.append(link("Llamar", "tel:+" + full));
  }
  if (who.email) box.append(link("Correo", "mailto:" + encodeURIComponent(who.email).replace("%40", "@")));
  if (isWeb(who.website)) box.append(link("Web", who.website));
  Object.keys(NETWORKS).forEach((network) => {
    if (isWeb(who[network])) box.append(link(NETWORKS[network], who[network]));
  });
  return box;
}

// What reading a clinic's website found out, in plain words.
function webCheckSays(clinic) {
  switch (clinic.website_check) {
    case "phone": return "Web comprobada: su teléfono sale en ella.";
    case "address": return "Web comprobada: su dirección sale en ella.";
    case "unconfirmed": return "Web sin comprobar: no vi en ella su teléfono ni su calle. Mira que sea la suya.";
    case "blocked": return "Su web pide a los programas que no entren: no la he leído.";
    case "failed": return "No pude abrir su web. " + ((clinic.web_scan || {}).error || "");
    case "not_found": return "No encontré su web probando con su nombre. Búscala en «Google Maps» y pégala en la ficha.";
    default: return isWeb(clinic.website) ? "Su web está apuntada, pero todavía no la he leído." : "Todavía no tiene web apuntada.";
  }
}

// Ask the server to read (or look for) one clinic's website. It can take half a minute.
function readWeb(clinicId) {
  return send("POST", "/api/crm/clinics/" + clinicId + "/read-web");
}

// "web, Instagram y TikTok": the fields a reading filled in.
function filledSays(filled) {
  const names = filled.map((field) => field === "website" ? "web" : NETWORKS[field] || field);
  return names.length > 1 ? names.slice(0, -1).join(", ") + " y " + names[names.length - 1] : names[0] || "";
}

// ---- The quick X-ray ----

const LEVELS = { hole: "Agujero", improve: "Mejorable", good: "Bien", unknown: "Sin datos" };

// Take a clinic's X-ray: it reads its website again and asks Google how fast it is on a phone.
// Half a minute or so, and free.
function takeXray(clinicId) {
  return send("POST", "/api/crm/clinics/" + clinicId + "/xray");
}

// Three dots, one per step of the patient's path, colored by how each step is doing.
function xrayDots(xray) {
  const words = xray.steps.map((step, index) => (index + 1) + ". " + step.title + ": " + LEVELS[step.level]);
  const box = el("span", "dots");
  box.setAttribute("role", "img");
  box.setAttribute("aria-label", words.join("; "));
  box.title = words.join("\n");
  box.append(...xray.steps.map((step) => el("span", "dot " + step.level)));
  return box;
}

// "Sin WhatsApp ni reserva online", or what the X-ray says when it found no hole.
function holeLabel(xray) {
  return xray.hole ? xray.hole.label : "Sin agujeros a la vista";
}

// "16 nov a las 14:05"
function sayMoment(iso) {
  return shortDay(iso.slice(0, 10)) + " a las " + iso.slice(11, 16);
}

// ---- The video with the proposal, in the lists ----

// A video usually takes this long on my computer (the same as SECONDS_EACH in video.py).
const VIDEO_SECONDS = 80;

// The video of a clinic in two or three words, and the tone of its dot. null when it cannot be made yet.
function videoWords(video) {
  switch (video.state) {
    case "working": return { text: "Haciéndose… " + Math.round(video.progress * 100) + " %", tone: "working" };
    case "waiting": return { text: "En la cola", tone: "waiting" };
    case "failed": return { text: "Falló", tone: "failed" };
    case "done": return video.outdated ? { text: "Antiguo", tone: "old" } : { text: "Listo", tone: "done" };
    default: return video.ready ? { text: "Sin hacer", tone: "none" } : null;
  }
}

// "Vídeo: listo", with its dot. A finished video is a link to it, on the clinic's card.
function videoTag(clinic, prefix) {
  const words = videoWords(clinic.video);
  if (!words) return null;
  const text = prefix ? prefix + words.text.toLowerCase() : words.text;
  return clinic.video.state === "done"
    ? link(text, "/crm/clinics/" + clinic.id + "#video", "video-tag " + words.tone)
    : el("span", "video-tag " + words.tone, text);
}

// "unos 25 min": how long a number of videos takes.
function videoMinutes(count) {
  const minutes = Math.max(1, Math.round(count * VIDEO_SECONDS / 60));
  return minutes === 1 ? "1 min" : "unos " + minutes + " min";
}

function stagePill(clinic) {
  return el("span", "pill " + clinic.stage, STAGES[clinic.stage] || clinic.stage);
}

// ---- The window for noting a contact ----

// The markup is fixed text written here: nothing typed by anyone goes through innerHTML.
const CONTACT_FORM = `
  <form class="dialog-body" id="contact-form">
    <div>
      <h2>Apuntar contacto</h2>
      <p class="sub" id="c-clinic"></p>
    </div>
    <div><label for="c-kind">Qué pasó</label><select id="c-kind"></select></div>
    <div class="row">
      <div><label for="c-channel">Por dónde</label><select id="c-channel"></select></div>
      <div><label for="c-day">Qué día</label><input type="date" id="c-day" required></div>
    </div>
    <div id="c-person-box" hidden><label for="c-person">Con quién</label><select id="c-person"></select></div>
    <div><label for="c-summary">Qué dije y qué pasó (opcional)</label><textarea id="c-summary" rows="4" maxlength="6000"></textarea></div>
    <div class="next-box">
      <div class="row">
        <div><label for="c-next">Próximo paso</label><input id="c-next" maxlength="300" placeholder="Nada pendiente"></div>
        <div><label for="c-next-day" id="c-next-day-label">Qué día</label><input type="date" id="c-next-day"></div>
      </div>
    </div>
    <p class="form-error" id="c-error" role="alert" hidden></p>
    <div class="form-actions">
      <button type="submit" id="c-save">Guardar contacto</button>
      <button type="button" class="quiet" id="c-cancel">Cancelar</button>
    </div>
  </form>`;

// Open the window for one clinic. onSaved gets the clinic as it is after saving.
// preset can bring the kind of contact, the channel and the text (from the message I just sent).
async function openContact(clinicId, onSaved, preset) {
  preset = preset || {};
  let clinic;
  try {
    clinic = await api("/api/crm/clinics/" + clinicId);  // fresh: its people and its next step
  } catch (error) { showError(error.message); return; }

  const old = $("contact-dialog");
  if (old) old.remove();
  const dialog = el("dialog");
  dialog.id = "contact-dialog";
  dialog.innerHTML = CONTACT_FORM;
  document.body.append(dialog);

  const today = isoDay(new Date());
  $("c-clinic").textContent = clinic.name + ". Ahora está en «" + STAGES[clinic.stage] + "».";
  $("c-kind").append(...Object.keys(KINDS).filter((kind) => kind !== "stage").map((kind) => option(kind, KINDS[kind])));
  $("c-kind").value = preset.kind || LIKELY[clinic.stage] || "note";
  $("c-channel").append(option("", "Sin canal"), ...Object.keys(CHANNELS).map((channel) => option(channel, CHANNELS[channel])));
  let remembered = "";
  try { remembered = localStorage.getItem("atelier.crm.channel") || ""; } catch (error) { /* works the same without it */ }
  $("c-channel").value = preset.channel in CHANNELS ? preset.channel : remembered in CHANNELS ? remembered : "";
  if (preset.summary) $("c-summary").value = preset.summary;
  $("c-day").value = today;
  $("c-day").max = today;
  if (clinic.people.length) {
    $("c-person-box").hidden = false;
    $("c-person").append(option("", "Con la clínica, sin persona concreta"),
      ...clinic.people.map((person) => option(person.id, person.name + (person.role ? " (" + person.role + ")" : ""))));
  }

  // Suggest the next step for this kind of contact, until I write my own.
  let mine = false;
  const suggest = () => {
    const kind = $("c-kind").value;
    $("c-next-day-label").textContent = kind === "call_booked" ? "Día de la llamada" : "Qué día";
    if (mine) return;
    if (NEXT[kind]) {
      const [text, days] = NEXT[kind];
      $("c-next").value = text;
      $("c-next-day").value = days === null ? "" : addDays(today, days);
    } else {
      // A note changes nothing: the next step stays as it was.
      $("c-next").value = clinic.next_step || "";
      $("c-next-day").value = clinic.next_step_on || "";
    }
  };
  suggest();
  $("c-kind").addEventListener("change", suggest);
  $("c-next").addEventListener("input", () => { mine = true; });
  $("c-next-day").addEventListener("input", () => { mine = true; });
  $("c-cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove());

  $("contact-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("c-save").disabled = true;
    $("c-error").hidden = true;
    try {
      const saved = await send("POST", "/api/crm/clinics/" + clinic.id + "/history", {
        kind: $("c-kind").value,
        channel: $("c-channel").value,
        happened_on: $("c-day").value,
        summary: $("c-summary").value,
        person_id: $("c-person").value ? Number($("c-person").value) : null,
        next_step: $("c-next").value,
        next_step_on: $("c-next-day").value || null,
      });
      try { localStorage.setItem("atelier.crm.channel", $("c-channel").value); } catch (error) { /* same */ }
      dialog.close();
      flash(saved.moved ? "Apuntado. " + saved.name + " pasa a «" + STAGES[saved.stage] + "»." : "Apuntado.");
      onSaved(saved);
    } catch (error) {
      $("c-error").textContent = error.message;
      $("c-error").hidden = false;
      $("c-save").disabled = false;
    }
  });

  dialog.showModal();
}
