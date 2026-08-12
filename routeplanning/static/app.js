"use strict";

const state = {
  date: null,
  stops: [],
  gsmtasks: null,
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
  warnings: document.getElementById("warnings"),
  warningsList: document.getElementById("warnings-list"),
  emptyHint: document.getElementById("empty-hint"),
  stopsHeader: document.getElementById("stops-header"),
  stopsList: document.getElementById("stops-list"),
  gsmtasksSection: document.getElementById("gsmtasks-section"),
  gsmtasksAlreadyTransferred: document.getElementById("gsmtasks-already-transferred"),
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

function renderWarnings(warnings) {
  el.warningsList.innerHTML = "";
  if (!warnings || warnings.length === 0) {
    el.warnings.hidden = true;
    return;
  }
  el.warnings.hidden = false;
  for (const w of warnings) {
    const li = document.createElement("li");
    li.textContent = `${w.article_name} — Menge ${w.quantity} — Kunde: ${w.customer_name}`;
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
    saveDraft();
  });
  wrapper.appendChild(input);
  return wrapper;
}

function renderStops() {
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

    const nr = document.createElement("span");
    nr.className = "col-nr";
    nr.textContent = String(index + 1);
    row.appendChild(nr);

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

// -- Drag & Drop (Pointer Events, funktioniert fuer Maus und Touch) --------
//
// Pragmatischer Ansatz: waehrend des Ziehens wird nur die tatsaechlich
// gezogene Zeile per DOM-insertBefore verschoben (kein Bibliotheks-Overhead,
// kein CDN). Zwei Dinge sorgen fuer ein fluessiges, zuverlaessiges Verhalten:
//   1. pointermove wird per requestAnimationFrame gebuendelt, statt bei jedem
//      Mausereignis sofort layoutintensive Arbeit (elementsFromPoint,
//      insertBefore) auszufuehren - das war die Ursache des Ruckelns.
//   2. Das Aufraeumen (dragging-Klasse entfernen, State synchronisieren) hat
//      mehrere Absicherungen: einen globalen pointerup/pointercancel-Fallback
//      auf document-Ebene, falls die Pointer-Capture-Events aus irgendeinem
//      Grund nicht am Handle ankommen - das war die Ursache der Zeile, die
//      nach dem Drop ausgegraut blieb.

let dragState = null;
let dragRaf = null;

function attachDragHandlers(row, handle) {
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    dragState = { pointerId: event.pointerId, row, x: event.clientX, y: event.clientY, lastTarget: null };
    row.classList.add("dragging");
  });

  handle.addEventListener("pointermove", (event) => {
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    dragState.x = event.clientX;
    dragState.y = event.clientY;
    if (dragRaf !== null) return;
    dragRaf = requestAnimationFrame(applyDragPosition);
  });

  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);
}

// Fallback auf Dokumentenebene: greift, falls pointerup/pointercancel nicht
// am Handle ankommt (z.B. wenn Pointer-Capture in einer bestimmten
// Browser/Touch-Kombination nicht sauber greift). endDrag ist idempotent.
document.addEventListener("pointerup", (event) => endDrag(event));
document.addEventListener("pointercancel", (event) => endDrag(event));

function applyDragPosition() {
  dragRaf = null;
  if (!dragState) return;
  const target = document
    .elementsFromPoint(dragState.x, dragState.y)
    .find((elm) => elm.classList && elm.classList.contains("stop-row") && elm !== dragState.row);
  if (!target || target === dragState.lastTarget) return;

  const rect = target.getBoundingClientRect();
  const before = dragState.y < rect.top + rect.height / 2;
  const reference = before ? target : target.nextSibling;
  if (reference !== dragState.row && dragState.row.nextSibling !== reference) {
    target.parentNode.insertBefore(dragState.row, reference);
    renumberRows();
  }
  dragState.lastTarget = target;
}

function renumberRows() {
  el.stopsList.querySelectorAll(".stop-row .col-nr").forEach((nrEl, index) => {
    nrEl.textContent = String(index + 1);
  });
}

function endDrag(event) {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  if (dragRaf !== null) {
    cancelAnimationFrame(dragRaf);
    dragRaf = null;
  }
  dragState = null;
  // Defensiv alle Zeilen bereinigen statt nur die zuletzt gezogene - falls
  // durch eine Race Condition mehrere/keine Referenz mehr stimmt, bleibt so
  // nie eine Zeile sichtbar ausgegraut.
  document.querySelectorAll(".stop-row.dragging").forEach((r) => r.classList.remove("dragging"));
  syncStateFromDom();
  saveDraft();
}

function syncStateFromDom() {
  const byId = new Map(state.stops.map((s) => [s.customer_id, s]));
  const rows = Array.from(el.stopsList.querySelectorAll(".stop-row"));
  state.stops = rows.map((row) => byId.get(row.dataset.customerId));
  renderStops();
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

function renderGsmtasksSection() {
  const hasStops = state.stops.length > 0;
  el.gsmtasksSection.hidden = !hasStops;
  if (!hasStops) return;

  const alreadyTransferred = Boolean(state.gsmtasks && state.gsmtasks.already_transferred);
  el.gsmtasksAlreadyTransferred.hidden = !alreadyTransferred;
  el.btnGsmtasksTransfer.hidden = alreadyTransferred;
}

function computeTotals() {
  return state.stops.reduce(
    (totals, stop) => ({
      kisten: totals.kisten + Number(stop.kisten || 0),
      schnittware: totals.schnittware + Number(stop.schnittware || 0),
      kresse: totals.kresse + Number(stop.kresse || 0),
    }),
    { kisten: 0, schnittware: 0, kresse: 0 }
  );
}

function openGsmtasksModal() {
  const totals = computeTotals();
  el.gsmtasksModalDate.textContent = state.date;
  el.gsmtasksModalStops.textContent = String(state.stops.length);
  el.gsmtasksModalKisten.textContent = String(totals.kisten);
  el.gsmtasksModalSchnittware.textContent = String(totals.schnittware);
  el.gsmtasksModalKresse.textContent = String(totals.kresse);
  el.gsmtasksResults.hidden = true;
  el.gsmtasksModal.showModal();
}

function renderGsmtasksResults(batch) {
  el.gsmtasksResults.hidden = false;
  el.gsmtasksResults.innerHTML = "";

  const summary = document.createElement("p");
  summary.className = "gsmtasks-summary-line";
  summary.textContent = `Erfolgreich: ${batch.success_count}  Fehlgeschlagen: ${batch.failure_count}`;
  el.gsmtasksResults.appendChild(summary);

  const failed = batch.stops.filter((s) => !s.success);
  if (failed.length > 0) {
    const list = document.createElement("ul");
    for (const stop of failed) {
      const li = document.createElement("li");
      li.textContent = `${stop.customer_name}: ${stop.error || "unbekannter Fehler"}`;
      list.appendChild(li);
    }
    el.gsmtasksResults.appendChild(list);
  }
}

async function confirmGsmtasksTransfer() {
  el.btnGsmtasksConfirm.disabled = true;
  try {
    const batch = await apiFetch(`/api/draft/${state.date}/gsmtasks`, { method: "POST" });
    el.gsmtasksModal.close();
    state.gsmtasks = { already_transferred: batch.status === "success", last_batch: batch };
    renderGsmtasksSection();
    renderGsmtasksResults(batch);
  } catch (err) {
    setStatus(`GSMtasks-Übertragung fehlgeschlagen: ${err.message}`, true);
    el.gsmtasksModal.close();
  } finally {
    el.btnGsmtasksConfirm.disabled = false;
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

el.btnLoadTour.addEventListener("click", loadTour);
el.btnLoadProfile.addEventListener("click", loadProfile);
el.btnSaveProfile.addEventListener("click", saveProfile);
el.btnDownloadXlsx.addEventListener("click", downloadXlsx);
el.btnGsmtasksTransfer.addEventListener("click", openGsmtasksModal);
el.btnGsmtasksCancel.addEventListener("click", () => el.gsmtasksModal.close());
el.btnGsmtasksConfirm.addEventListener("click", confirmGsmtasksTransfer);

refreshProfileList();
loadExistingDraft(el.dateInput.value);
