"use strict";

const state = {
  date: null,
  stops: [],
  gsmtasks: null,
  precheckReady: false,
};

const el = {
  dateInput: document.getElementById("delivery-date"),
  btnLoadTour: document.getElementById("btn-load-tour"),
  loadStatus: document.getElementById("load-status"),
  profileSelect: document.getElementById("profile-select"),
  btnLoadProfile: document.getElementById("btn-load-profile"),
  profileName: document.getElementById("profile-name"),
  btnSaveProfile: document.getElementById("btn-save-profile"),
  btnDownloadXlsx: document.getElementById("btn-download-xlsx"),
  btnDownloadPdf: document.getElementById("btn-download-pdf"),
  tourTotals: document.getElementById("tour-totals"),
  warnings: document.getElementById("warnings"),
  warningsList: document.getElementById("warnings-list"),
  emptyHint: document.getElementById("empty-hint"),
  stopsHeader: document.getElementById("stops-header"),
  stopsList: document.getElementById("stops-list"),
  gsmtasksSection: document.getElementById("gsmtasks-section"),
  gsmtasksAlreadyTransferred: document.getElementById("gsmtasks-already-transferred"),
  gsmtasksStatus: document.getElementById("gsmtasks-status"),
  gsmtasksLocked: document.getElementById("gsmtasks-locked"),
  gsmtasksPrecheck: document.getElementById("gsmtasks-precheck"),
  gsmtasksConfirmManual: document.getElementById("gsmtasks-confirm-manual"),
  btnGsmtasksTransfer: document.getElementById("btn-gsmtasks-transfer"),
  gsmtasksResults: document.getElementById("gsmtasks-results"),
  gsmtasksModal: document.getElementById("gsmtasks-modal"),
  gsmtasksModalDate: document.getElementById("gsmtasks-modal-date"),
  gsmtasksModalStops: document.getElementById("gsmtasks-modal-stops"),
  gsmtasksModalKisten: document.getElementById("gsmtasks-modal-kisten"),
  gsmtasksModalSchnittware: document.getElementById("gsmtasks-modal-schnittware"),
  gsmtasksModalKresse: document.getElementById("gsmtasks-modal-kresse"),
  btnGsmtasksCancel: document.getElementById("btn-gsmtasks-cancel"),
  btnGsmtasksConfirm: document.getElementById("btn-gsmtasks-confirm"),
};

function setStatus(message, isError) {
  el.loadStatus.textContent = message || "";
  el.loadStatus.classList.toggle("error", Boolean(isError));
}

const API_PREFIX = window.APP_PREFIX || "";

async function apiFetch(url, options) {
  const response = await fetch(API_PREFIX + url, options);
  let body = null;
  try {
    body = await response.json();
  } catch (err) {
    body = null;
  }
  if (!response.ok) {
    const message = (body && body.error) || `Fehler ${response.status}`;
    throw new Error(message);
  }
  return body;
}

const WARNING_LABELS = {
  unklassifiziert: "Nicht klassifiziert",
  ve_nicht_ganzzahlig: "VE-Menge nicht ganzzahlig",
  ve_groesse_widerspruch: "VE-Größe widersprüchlich",
};

function renderWarnings(warnings) {
  el.warningsList.innerHTML = "";
  if (!warnings || warnings.length === 0) {
    el.warnings.hidden = true;
    return;
  }
  el.warnings.hidden = false;
  for (const w of warnings) {
    const li = document.createElement("li");
    if (w.reason === "adresse_unvollstaendig") {
      li.textContent = `${w.article_name} — Kunde: ${w.customer_name}`;
    } else {
      const label = WARNING_LABELS[w.reason] || WARNING_LABELS.unklassifiziert;
      li.textContent = `${label}: ${w.article_name} — Menge ${w.quantity} — Kunde: ${w.customer_name} (nicht in Summen)`;
    }
    el.warningsList.appendChild(li);
  }
}

function makeQtyField(stop, field, label) {
  const wrapper = document.createElement("div");
  wrapper.className = "col-qty";

  const labelEl = document.createElement("label");
  labelEl.textContent = label;
  wrapper.appendChild(labelEl);

  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.step = field === "schnittware" ? "0.1" : "1";
  input.value = stop[field] === 0 ? "" : stop[field];
  input.setAttribute("aria-label", `${label} für ${stop.customer_name}`);
  if (stop[`${field}_manual`]) {
    input.classList.add("manual");
  }
  input.addEventListener("change", () => {
    const raw = input.value.trim();
    const value = raw === "" ? 0 : Number(raw);
    stop[field] = Number.isFinite(value) && value >= 0 ? value : 0;
    input.value = stop[field] === 0 ? "" : stop[field];
    renderTotals();
    saveDraft();
  });
  wrapper.appendChild(input);
  return wrapper;
}

// Gesamtmengen ueber dem Profilabschnitt - wird bei jedem Rendern der Liste
// und bei jeder Mengenaenderung neu aus state.stops berechnet.
function renderTotals() {
  el.tourTotals.hidden = state.stops.length === 0;
  el.tourTotals.textContent = RouteTotals.formatSummary(RouteTotals.summarize(state.stops));
}

function renderStops() {
  renderTotals();
  el.stopsList.innerHTML = "";
  const hasStops = state.stops.length > 0;
  el.emptyHint.hidden = hasStops;
  el.stopsHeader.hidden = !hasStops;

  state.stops.forEach((stop, index) => {
    const row = document.createElement("div");
    row.className = "stop-row";
    row.dataset.customerId = stop.customer_id;

    const handle = document.createElement("span");
    handle.className = "col-handle";
    handle.textContent = "☰";
    handle.setAttribute("aria-label", "Ziehen zum Sortieren");
    row.appendChild(handle);

    row.appendChild(makeStopNumberField(stop, index));

    const customer = document.createElement("div");
    customer.className = "col-customer";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = stop.customer_name;
    if (stop.not_in_profile) {
      const badge = document.createElement("span");
      badge.className = "not-in-profile-badge";
      badge.textContent = "nicht im Profil";
      name.appendChild(badge);
    }
    customer.appendChild(name);
    const address = document.createElement("div");
    address.className = "address";
    address.textContent = stop.address;
    customer.appendChild(address);
    row.appendChild(customer);

    const qtyGroup = document.createElement("div");
    qtyGroup.className = "qty-group";
    qtyGroup.appendChild(makeQtyField(stop, "kisten", "Kisten"));
    qtyGroup.appendChild(makeQtyField(stop, "schnittware", "Schnittware"));
    qtyGroup.appendChild(makeQtyField(stop, "kresse", "Kresse"));
    row.appendChild(qtyGroup);

    const actionCol = document.createElement("div");
    actionCol.className = "col-action";
    const btnDelete = document.createElement("button");
    btnDelete.type = "button";
    btnDelete.className = "btn-delete-stop";
    btnDelete.textContent = "Löschen";
    btnDelete.setAttribute("aria-label", `Stopp für ${stop.customer_name} löschen`);
    btnDelete.addEventListener("click", () => deleteStop(stop.customer_id, stop.customer_name));
    actionCol.appendChild(btnDelete);
    row.appendChild(actionCol);

    attachDragHandlers(row, handle);
    el.stopsList.appendChild(row);
  });
}

// -- Stoppnummer direkt eingeben ------------------------------------------
//
// Die eingegebene Zahl ist die neue Stoppnummer des Stopps: alle uebrigen
// Stopps behalten ihre relative Reihenfolge, danach wird 1..N durchgehend
// neu nummeriert und gespeichert (Logik: RouteOrdering.moveToNumber).

function makeStopNumberField(stop, index) {
  const input = document.createElement("input");
  input.type = "number";
  input.className = "col-nr stop-nr";
  input.min = "1";
  input.max = String(state.stops.length);
  input.step = "1";
  input.inputMode = "numeric";
  input.value = String(index + 1);
  input.setAttribute("aria-label", `Stoppnummer für ${stop.customer_name}`);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitStopNumber(stop, input);
    } else if (event.key === "Escape") {
      input.value = String(state.stops.indexOf(stop) + 1);
      input.blur();
    }
  });
  input.addEventListener("change", () => commitStopNumber(stop, input));
  input.addEventListener("blur", () => commitStopNumber(stop, input));
  return input;
}

function commitStopNumber(stop, input) {
  const from = state.stops.indexOf(stop);
  if (from < 0 || !input.isConnected) return;
  const count = state.stops.length;
  const target = RouteOrdering.parseTargetNumber(input.value, count);
  if (target === null) {
    input.value = String(from + 1);
    setStatus(`Ungültige Stoppnummer – bitte eine ganze Zahl von 1 bis ${count} eingeben.`, true);
    return;
  }
  if (target === from + 1) {
    input.value = String(target);
    return;
  }
  state.stops = RouteOrdering.moveToNumber(state.stops, from, target);
  renderStops();
  const moved = el.stopsList.querySelector(
    `.stop-row[data-customer-id="${CSS.escape(stop.customer_id)}"]`
  );
  if (moved) {
    moved.scrollIntoView({ block: "nearest" });
    moved.classList.add("just-moved");
    setTimeout(() => moved.classList.remove("just-moved"), 1200);
  }
  setStatus(`„${stop.customer_name}“ auf Stopp ${target} verschoben.`, false);
  saveDraft();
}

// -- Drag & Drop (Pointer Events, Maus und Touch) ------------------------
//
// Waehrend des Ziehens laeuft eine requestAnimationFrame-Schleife, die
//   1. am oberen/unteren Fensterrand automatisch scrollt und
//   2. die gezogene Zeile vor die erste Zeile setzt, deren Mitte unterhalb
//      des Zeigers liegt (zustandslos, RouteOrdering.findInsertBefore).
// Dadurch laesst sich ein Stopp in einer Bewegung beliebig weit verschieben,
// auch ueber den sichtbaren Bereich hinaus. Frueher wurde die Entscheidung
// "davor/danach" pro Zielzeile nur einmal getroffen (lastTarget) - eine
// Zeile, die von der "falschen" Haelfte aus betreten wurde, blockierte so das
// weitere Verschieben.
//
// Pointer-Capture liegt auf der Liste selbst, nicht auf dem Griff: die
// gezogene Zeile wird im DOM umgehaengt, und ein umgehaengtes Element verliert
// seine Capture - das hat das Ziehen frueher nach einem Schritt beendet.
//
// Beim Ablegen wird die neue Reihenfolge gegen den bisherigen Stand geprueft
// (jede Zeile genau einmal), erst dann gespeichert.

const AUTOSCROLL_EDGE_PX = 80;
const AUTOSCROLL_MAX_PX = 24;

let drag = null;

function attachDragHandlers(row, handle) {
  handle.addEventListener("pointerdown", (event) => {
    if (drag || (event.pointerType === "mouse" && event.button !== 0)) return;
    event.preventDefault();
    try {
      el.stopsList.setPointerCapture(event.pointerId);
    } catch (err) {
      // Ohne Capture kommen die Events weiterhin per Bubbling am document an.
    }
    drag = { pointerId: event.pointerId, row, y: event.clientY, raf: null };
    row.classList.add("dragging");
    document.body.classList.add("is-dragging");
    drag.raf = requestAnimationFrame(dragTick);
  });
}

// Bewegung und Ende werden zentral auf Dokumentenebene verarbeitet (mit
// Capture auf der Liste kommen sie per Bubbling hier an, ohne Capture direkt).
// onDragMove/endDrag pruefen die pointerId und sind idempotent.
document.addEventListener("pointermove", onDragMove);
document.addEventListener("pointerup", endDrag);
document.addEventListener("pointercancel", endDrag);
window.addEventListener("blur", () => drag && finishDrag());

function onDragMove(event) {
  if (!drag || drag.pointerId !== event.pointerId) return;
  drag.y = event.clientY;
}

function dragTick() {
  if (!drag) return;
  const delta = RouteOrdering.autoScrollDelta(
    drag.y,
    window.innerHeight,
    AUTOSCROLL_EDGE_PX,
    AUTOSCROLL_MAX_PX
  );
  if (delta !== 0) window.scrollBy(0, delta);
  placeDraggedRow();
  drag.raf = requestAnimationFrame(dragTick);
}

function placeDraggedRow() {
  const others = Array.from(el.stopsList.children).filter((r) => r !== drag.row);
  const midpoints = others.map((r) => {
    const rect = r.getBoundingClientRect();
    return rect.top + rect.height / 2;
  });
  const index = RouteOrdering.findInsertBefore(midpoints, drag.y);
  const reference = index === -1 ? null : others[index];
  if (drag.row.nextElementSibling !== reference) {
    el.stopsList.insertBefore(drag.row, reference);
    renumberRows();
  }
}

function renumberRows() {
  el.stopsList.querySelectorAll(".stop-row .stop-nr").forEach((input, index) => {
    input.value = String(index + 1);
  });
}

function endDrag(event) {
  if (!drag || drag.pointerId !== event.pointerId) return;
  finishDrag();
}

function finishDrag() {
  if (drag.raf !== null) cancelAnimationFrame(drag.raf);
  drag = null;
  document.body.classList.remove("is-dragging");
  // Defensiv alle Zeilen bereinigen, damit nie eine Zeile hervorgehoben bleibt.
  document.querySelectorAll(".stop-row.dragging").forEach((r) => r.classList.remove("dragging"));

  const before = state.stops.map((s) => s.customer_id);
  const after = Array.from(el.stopsList.querySelectorAll(".stop-row")).map(
    (row) => row.dataset.customerId
  );
  if (!RouteOrdering.isPermutation(after, before)) {
    // Darf nicht vorkommen - lieber den letzten gueltigen Stand zeigen als
    // eine Liste mit doppelten/fehlenden Stopps speichern.
    renderStops();
    setStatus("Verschieben abgebrochen: Stoppliste war inkonsistent, nichts gespeichert.", true);
    return;
  }
  if (after.every((id, i) => id === before[i])) {
    renderStops();
    return;
  }
  const byId = new Map(state.stops.map((s) => [s.customer_id, s]));
  state.stops = after.map((id) => byId.get(id));
  renderStops();
  saveDraft();
}

// -- API-Aufrufe -------------------------------------------------------

function applyDraft(draft) {
  state.date = draft.delivery_date;
  state.stops = draft.stops;
  state.gsmtasks = draft.gsmtasks || null;
  renderWarnings(draft.warnings);
  renderStops();
  renderGsmtasksSection();
}

// -- GSMtasks-Uebertragung -----------------------------------------------

const GSMTASKS_OUTCOME_LABELS = {
  created: "angelegt",
  existing: "bereits vorhanden – nicht erneut angelegt",
  failed: "nicht angelegt",
  unclear: "Ausgang unklar – wird beim nächsten Versuch in GSMtasks gesucht",
  not_sent: "nicht gesendet",
};

const GSMTASKS_PLAN_LABELS = {
  new: "wird angelegt",
  existing: "bereits in GSMtasks vorhanden – wird übersprungen",
  transferred: "bereits übertragen – wird übersprungen",
  address_incomplete: "Adresse unvollständig – blockiert die Übertragung",
};

function renderGsmtasksSection() {
  const hasStops = state.stops.length > 0;
  el.gsmtasksSection.hidden = !hasStops;
  if (!hasStops) return;

  const g = state.gsmtasks || {};
  const alreadyTransferred = Boolean(g.already_transferred);
  const locked = g.locked || null;
  el.gsmtasksLocked.hidden = !locked;
  el.gsmtasksLocked.textContent = locked
    ? `🔒 GSMtasks-Übertragung für diesen Liefertag dauerhaft gesperrt: ${locked.reason}`
    : "";
  el.gsmtasksAlreadyTransferred.hidden = !alreadyTransferred || Boolean(locked);
  el.btnGsmtasksTransfer.hidden = alreadyTransferred || Boolean(locked);

  const parts = [];
  if (g.transferred_count) parts.push(`${g.transferred_count} von ${g.stop_count} Stopps bereits in GSMtasks`);
  if (g.unclear_count) parts.push(`${g.unclear_count} Stopp(s) mit unklarem Ausgang`);
  if (g.last_batch) parts.push(`letzter Versuch: ${g.last_batch.finished_at.replace("T", " ")} (${g.last_batch.status})`);
  el.gsmtasksStatus.hidden = parts.length === 0;
  el.gsmtasksStatus.textContent = parts.join(" · ");
}

function renderPrecheck(result) {
  el.gsmtasksPrecheck.classList.remove("error");
  el.gsmtasksPrecheck.innerHTML = "";
  const line = document.createElement("p");
  line.textContent =
    `Neu anzulegen: ${result.new_count} · bereits in GSMtasks: ${result.existing_count}` +
    ` · bereits übertragen: ${result.transferred_count}`;
  el.gsmtasksPrecheck.appendChild(line);
  const notable = result.stops.filter((s) => s.status !== "new");
  if (notable.length > 0) {
    const list = document.createElement("ul");
    for (const s of notable) {
      const li = document.createElement("li");
      li.textContent = `${s.customer_name}: ${GSMTASKS_PLAN_LABELS[s.status] || s.status}`;
      list.appendChild(li);
    }
    el.gsmtasksPrecheck.appendChild(list);
  }
  if (result.address_incomplete.length > 0) {
    el.gsmtasksPrecheck.classList.add("error");
    const p = document.createElement("p");
    p.textContent =
      "Übertragung nicht möglich: Adresse unvollständig. Bitte in Zoho ergänzen und „Tour laden“ ausführen.";
    el.gsmtasksPrecheck.appendChild(p);
  } else if (result.new_count === 0) {
    const p = document.createElement("p");
    p.textContent = "Es gibt keine neu anzulegenden Stopps.";
    el.gsmtasksPrecheck.appendChild(p);
  }
}

function updateConfirmButton() {
  el.btnGsmtasksConfirm.disabled = !(state.precheckReady && el.gsmtasksConfirmManual.checked);
}

async function openGsmtasksModal() {
  const totals = RouteTotals.summarize(state.stops);
  el.gsmtasksModalDate.textContent = state.date;
  el.gsmtasksModalStops.textContent = String(state.stops.length);
  el.gsmtasksModalKisten.textContent = RouteTotals.formatNumber(totals.kisten);
  el.gsmtasksModalSchnittware.textContent = RouteTotals.formatNumber(totals.schnittware);
  el.gsmtasksModalKresse.textContent = RouteTotals.formatNumber(totals.kresse);
  el.gsmtasksResults.hidden = true;
  el.gsmtasksConfirmManual.checked = false;
  state.precheckReady = false;
  updateConfirmButton();
  el.gsmtasksPrecheck.classList.remove("error");
  el.gsmtasksPrecheck.textContent = "Prüfe GSMtasks (nur lesend) …";
  el.gsmtasksModal.showModal();
  try {
    const result = await apiFetch(`/api/draft/${state.date}/gsmtasks/precheck`);
    renderPrecheck(result);
    state.precheckReady = Boolean(result.ready);
  } catch (err) {
    el.gsmtasksPrecheck.classList.add("error");
    el.gsmtasksPrecheck.textContent = `Vorabprüfung fehlgeschlagen – es wird nichts übertragen. ${err.message}`;
    state.precheckReady = false;
  }
  updateConfirmButton();
}

function renderGsmtasksResults(batch) {
  el.gsmtasksResults.hidden = false;
  el.gsmtasksResults.innerHTML = "";

  const summary = document.createElement("p");
  summary.className = "gsmtasks-summary-line";
  summary.textContent = `Erfolgreich: ${batch.success_count}  Nicht erfolgreich: ${batch.failure_count}`;
  el.gsmtasksResults.appendChild(summary);

  const notCreated = batch.stops.filter((s) => s.outcome !== "created");
  if (notCreated.length > 0) {
    const list = document.createElement("ul");
    for (const stop of notCreated) {
      const li = document.createElement("li");
      const label = GSMTASKS_OUTCOME_LABELS[stop.outcome] || stop.outcome;
      li.textContent = `${stop.customer_name}: ${label}${stop.error ? ` (${stop.error})` : ""}`;
      list.appendChild(li);
    }
    el.gsmtasksResults.appendChild(list);
  }
}

async function confirmGsmtasksTransfer() {
  if (!state.precheckReady || !el.gsmtasksConfirmManual.checked) return;
  el.btnGsmtasksConfirm.disabled = true;
  try {
    const batch = await apiFetch(`/api/draft/${state.date}/gsmtasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    el.gsmtasksModal.close();
    // Stand (inkl. "x von n uebertragen") frisch vom Server holen.
    await loadExistingDraft(state.date);
    renderGsmtasksResults(batch);
    setStatus(`GSMtasks-Übertragung abgeschlossen (${batch.status}).`, batch.status !== "success");
  } catch (err) {
    setStatus(`GSMtasks-Übertragung fehlgeschlagen: ${err.message}`, true);
    el.gsmtasksModal.close();
    await loadExistingDraft(state.date);
  } finally {
    updateConfirmButton();
  }
}

async function loadTour() {
  const date = el.dateInput.value;
  if (!date) {
    setStatus("Bitte ein Datum waehlen.", true);
    return;
  }
  setStatus("Lade Aufträge aus Zoho ...", false);
  el.btnLoadTour.disabled = true;
  try {
    const draft = await apiFetch("/api/tour/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date }),
    });
    applyDraft(draft);
    setStatus(`${draft.stops.length} Stopp(s) geladen.`, false);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    el.btnLoadTour.disabled = false;
  }
}

async function saveDraft() {
  if (!state.date) return;
  try {
    const payload = {
      stops: state.stops.map((s) => ({
        customer_id: s.customer_id,
        kisten: s.kisten,
        schnittware: s.schnittware,
        kresse: s.kresse,
      })),
    };
    await apiFetch(`/api/draft/${state.date}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    setStatus(`Speichern fehlgeschlagen: ${err.message}`, true);
  }
}

async function deleteStop(customerId, customerName) {
  if (!state.date) return;
  if (!window.confirm(`Stopp für "${customerName}" aus dem Tourentwurf entfernen?`)) return;
  try {
    const draft = await apiFetch(
      `/api/draft/${state.date}/stops/${encodeURIComponent(customerId)}`,
      { method: "DELETE" }
    );
    applyDraft(draft);
    setStatus(`Stopp für "${customerName}" entfernt.`, false);
  } catch (err) {
    setStatus(`Löschen fehlgeschlagen: ${err.message}`, true);
  }
}

async function loadExistingDraft(date) {
  try {
    const draft = await apiFetch(`/api/draft/${date}`);
    applyDraft(draft);
  } catch (err) {
    // Kein gespeicherter Entwurf fuer dieses Datum - kein Fehler, einfach leer.
  }
}

async function refreshProfileList() {
  try {
    const data = await apiFetch("/api/profiles");
    el.profileSelect.innerHTML = '<option value="">– auswaehlen –</option>';
    for (const name of data.profiles) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      el.profileSelect.appendChild(option);
    }
  } catch (err) {
    setStatus(`Profile konnten nicht geladen werden: ${err.message}`, true);
  }
}

async function loadProfile() {
  const name = el.profileSelect.value;
  if (!name) {
    setStatus("Bitte ein Profil auswaehlen.", true);
    return;
  }
  if (!state.date) {
    setStatus("Bitte zuerst eine Tour laden.", true);
    return;
  }
  try {
    const draft = await apiFetch(`/api/profiles/${encodeURIComponent(name)}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: state.date }),
    });
    applyDraft(draft);
    setStatus(`Profil "${name}" angewendet.`, false);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function saveProfile() {
  const name = el.profileName.value.trim();
  if (!name) {
    setStatus("Bitte einen Profilnamen eingeben.", true);
    return;
  }
  if (!state.date) {
    setStatus("Bitte zuerst eine Tour laden.", true);
    return;
  }
  try {
    await apiFetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, date: state.date }),
    });
    setStatus(`Profil "${name}" gespeichert.`, false);
    el.profileName.value = "";
    await refreshProfileList();
  } catch (err) {
    setStatus(err.message, true);
  }
}

function downloadXlsx() {
  if (!state.date) {
    setStatus("Bitte zuerst eine Tour laden.", true);
    return;
  }
  window.location.href = `${API_PREFIX}/api/draft/${state.date}/xlsx`;
}

function downloadPdf() {
  if (!state.date) {
    setStatus("Bitte zuerst eine Tour laden.", true);
    return;
  }
  window.location.href = `${API_PREFIX}/api/draft/${state.date}/pdf`;
}

el.btnLoadTour.addEventListener("click", loadTour);
el.btnLoadProfile.addEventListener("click", loadProfile);
el.btnSaveProfile.addEventListener("click", saveProfile);
el.btnDownloadXlsx.addEventListener("click", downloadXlsx);
el.btnDownloadPdf.addEventListener("click", downloadPdf);
el.btnGsmtasksTransfer.addEventListener("click", openGsmtasksModal);
el.btnGsmtasksCancel.addEventListener("click", () => el.gsmtasksModal.close());
el.btnGsmtasksConfirm.addEventListener("click", confirmGsmtasksTransfer);
el.gsmtasksConfirmManual.addEventListener("change", updateConfirmButton);

refreshProfileList();
loadExistingDraft(el.dateInput.value);
