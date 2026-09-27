"use strict";

const $ = (id) => document.getElementById(id);
const tr = text => window.DoseI18n.t(text);
let preparedUpload = null;
const targetPrescriptions = new Map();
let inspectionReadOnly = false;
function prescriptionSummary(summary) { const range=summary?.prescription_range_Gy; return range?.length === 2 ? (range[0] === range[1] ? fmt(range[0]) : range.map(value=>fmt(value)).join("–")) : fmt(summary?.prescription_Gy); }
function validRx(value) { return Number.isFinite(value) && value > 0 && value <= 100; }
function effectiveTargetRx(number) { return targetPrescriptions.has(number) ? targetPrescriptions.get(number) : Number($("prescription").value); }
function selectedPrescriptionMap() { return Object.fromEntries(selectedTargets().map(number => [String(number), effectiveTargetRx(number)])); }
let pendingInspection = null, inspectionRunning = false, inspectionController = null;
function discardPreparedUpload() { if (preparedUpload?.context) window.DosePrivacy?.forget(preparedUpload.context); preparedUpload = null; }
const activationParams = new URLSearchParams(location.hash.slice(1));
let activation = ['invite', 'recovery'].includes(activationParams.get('type')) && activationParams.get('token_hash') ? {token_hash: activationParams.get('token_hash'), type: activationParams.get('type')} : null;
let registration = Boolean(activation);
$('activation-email').value = activationParams.get('email') || '';
if (location.hash) history.replaceState(null, '', location.pathname + location.search);
const state = {result: null, mode: "predicted", slice: 0, inspected: false, busy: false, generation: 0, controller: null, inspection: null, uploadReady: false, zoom: 1, focus: null, orbit: {yaw: -0.45, pitch: 0.3, zoom: 1, center: null}, contourCache: new Map(), auth: {ready: false, enabled: false, authenticated: false, storage_mode: null}, authBusy: false, runsGeneration: 0};
const colors = ["#277fca", "#8265b2", "#4ca8e5", "#ba79ca", "#646fc8", "#3f91b6", "#c68f5b"];
function comparisonColor(kind) { const dark=document.documentElement.dataset.theme === "dark"; return kind === "predicted" ? (dark ? "#75bcff" : "#155caf") : (dark ? "#ffc078" : "#a64c00"); }
const fmt = (value, digits = 1) => Number.isFinite(Number(value)) && value !== null && value !== undefined ? Number(value).toLocaleString(window.DoseI18n.language === 'en' ? 'en-GB' : 'de-DE', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : "—";
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function errorMessage(error) { return error?.name === "AbortError" ? "" : (error?.message || "Die Anfrage konnte nicht verarbeitet werden."); }
async function rawRequest(url, options = {}) {
  const response = await fetch(url, {credentials: "same-origin", ...options});
  let data;
  try { data = await response.json(); } catch { throw new Error(`Der Server lieferte keine lesbare Antwort (HTTP ${response.status}).`); }
  if (!response.ok) {
    const detail = data.detail || data.error || `Anfrage fehlgeschlagen (HTTP ${response.status}).`;
    const error = new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(item => typeof item === "string" ? item : item.msg || JSON.stringify(item)).join(" · ") : JSON.stringify(detail));
    error.status = response.status; throw error;
  }
  return data;
}
async function request(url, options = {}) {
  if (state.auth.queue_enabled && options.method === 'POST' && ['/api/inspect','/api/predict'].includes(url)) return window.DoseQueue.run(url, options, rawRequest);
  return rawRequest(url, options);
}
function showError(message) { $("error").textContent = message; $("error").hidden = !message; }
function showWarnings(warnings = []) {
  const box = $("warnings"); box.replaceChildren(); box.hidden = !warnings.length;
  if (warnings.length) { box.append(el("strong", "Hinweise zur Einordnung")); const list = el("ul"); warnings.forEach(warning => list.append(el("li", String(warning)))); box.append(list); }
}
function selectedTargets() { return [...$("roi-list").querySelectorAll("input:checked")].map(input => Number(input.value)); }
function canUpload() { return !activation && !registration && state.auth.ready && !state.authBusy && (!state.auth.enabled || state.auth.authenticated); }
function updateButton() {
  updateTargetInputSummary();
  $("process-button").disabled = state.busy || !canUpload() || !$("structure-file").files[0];
  const inRange = selectedTargets().every(number => validRx(effectiveTargetRx(number))) && (!$("prescription").value || validRx(Number($("prescription").value)));
  $('files-step-state').textContent=state.inspected?tr('Dateien geprüft. Weiter mit der Targetauswahl.'):$('structure-file').files[0]?tr('Dateien ausgewählt. Jetzt „Dateien verarbeiten“ drücken.'):tr('Strukturdatei auswählen und Verarbeitung starten.');
  $('targets-step-state').textContent=state.inspected?tr('Vorausgewählte Targets prüfen und bei Bedarf ändern.'):tr('Zuerst die Dateien in Schritt 1 verarbeiten.');
  $('dose-step-state').textContent=state.inspected && inRange && selectedTargets().length?tr('Verschreibung prüfen. Anschließend „Dosis abschätzen“ drücken.'):tr('Targets prüfen, Verschreibung angeben und Dosis abschätzen.');
  $('prepare-files').dataset.ready=String(Boolean($('structure-file').files[0]));
  $('prepare-targets').dataset.ready=String(state.inspected);
  $('prepare-dose').dataset.ready=String(state.inspected && inRange && selectedTargets().length>0);
  $("predict-button").disabled = !canUpload() || state.busy || !state.inspected || !state.uploadReady || !selectedTargets().length || !inRange;
  $("demo-button").disabled = state.busy; $("test-case-button").disabled = state.busy;
  $("apply-prescription-all").disabled = state.busy || inspectionReadOnly || !state.uploadReady || !selectedTargets().length || !validRx(Number($("prescription").value));
  $("confirm-proposals").disabled = state.busy || !state.uploadReady || !selectedTargets().length;
  ["structure-file", "dose-file"].forEach(id => { $(id).disabled = (state.busy && state.operation !== "inspect") || !canUpload(); });
  ['structure','dose'].forEach(kind=>{const clear=$('clear-'+kind+'-file');clear.hidden=!$(kind+'-file').files.length;clear.disabled=$(kind+'-file').disabled;});
  $('metrics-excel-button').disabled=state.busy || !state.result?.metrics?.length;
  $('more-dose-files').disabled=state.busy || !document.getElementById('comparison-files');
  $("anonymize-toggle").disabled = true;
  $("prediction-variant").disabled = state.busy || inspectionReadOnly;
  $("login-button").disabled = state.authBusy || state.busy; $("logout-button").disabled = state.authBusy || state.busy;
  $("archive-refresh").disabled = state.authBusy || state.busy;
  $("archive-list").querySelectorAll("button").forEach(button => { button.disabled = state.busy || state.authBusy; });
}
let processingTimer = null, processingStarted = 0;
function renderProcessingClock() {
  if (!state.busy) return;
  const seconds = Math.floor((Date.now() - processingStarted) / 1000);
  const english = window.DoseI18n.language === 'en';
  $('processing-clock').textContent = (english ? 'Elapsed: ' : 'Vergangen: ') + Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0') + (english ? ' · Waiting for completion. No server progress available.' : ' · Abschluss wird abgewartet. Kein Fortschrittswert vom Server verfügbar.') + (seconds >= 60 ? (english ? ' Larger cases can take several minutes. Please keep this tab open.' : ' Größere Fälle können mehrere Minuten benötigen. Bitte diesen Tab offen lassen.') : '');
}
function busy(value, message = "", operation = "other") {
  if (value && !state.busy) { processingStarted = Date.now(); clearInterval(processingTimer); processingTimer = setInterval(renderProcessingClock, 1000); }
  if (!value) { clearInterval(processingTimer); processingTimer = null; }
  $('processing-clock').hidden = !value;
  document.querySelector('.workspace').setAttribute('aria-busy', String(value));
  state.operation = value ? operation : null;
  state.busy = value; $("status").hidden = !value; $("status").textContent = message; renderProcessingClock();
  ["structure-file", "dose-file", "anonymize-toggle", "prescription", "test-case-select"].forEach(id => { $(id).disabled = value; });
  $("roi-area").querySelectorAll("input").forEach(input => { input.disabled = value || !state.uploadReady; });
  updateButton();
}
function clearResults() {
  window.DoseQueue?.cancelPending();
  window.DoseComparisons?.reset();
  closeExpandedView();
  pendingInspection = null;
  state.generation++; state.controller?.abort(); state.controller = null; state.result = null; state.contourCache.clear();
  $("results").hidden = true; $("empty-state").hidden = false; showError(""); showWarnings([]); busy(false);
}
function checkFile(file) { if (file && file.size > 250 * 1024 * 1024) throw new Error("Die Datei überschreitet die Grenze von 250 MB. Bitte eine einzelne DICOM-Datei bis 250 MB auswählen."); }
async function appendFiles(form) {
  if (!canUpload()) throw new Error("Bitte zuerst anmelden; der Upload ist noch nicht freigegeben.");
  const structure = $("structure-file").files[0], dose = $("dose-file").files[0]; checkFile(structure); checkFile(dose);
  if (!structure) throw new Error("Bitte zuerst eine RTSTRUCT-Datei auswählen.");
  if (structure.size + (dose?.size || 0) > 250 * 1024 * 1024 - 16384) throw new Error("Der gesamte Upload muss unter 250 MB bleiben.");
  if (!window.DosePrivacy) throw new Error('Die Browser-Anonymisierung ist nicht verfügbar. Es wurden keine Dateien übertragen.');
  if (!preparedUpload || preparedUpload.originalStructure !== structure || preparedUpload.originalDose !== dose) {
    const generation = state.generation;
    const prepared = await window.DosePrivacy.prepare(structure, dose || null);
    if (generation !== state.generation || !canUpload()) { window.DosePrivacy.forget(prepared.context); throw new DOMException('Canceled', 'AbortError'); }
    discardPreparedUpload();
    preparedUpload = {...prepared, originalStructure: structure, originalDose: dose};
  }
  form.append("structure", preparedUpload.structure); if (preparedUpload.dose) form.append("dose", preparedUpload.dose); form.append("anonymize", "true");
}
function renderInspection(data, readOnly = false) {
  state.inspection = data; state.uploadReady = !readOnly; inspectionReadOnly = readOnly;
  ["target-selection-details", "target-prescriptions-area", "proposal-area"].forEach(id => { $(id).open = false; });
  if (readOnly) { targetPrescriptions.clear(); Object.entries(data.result?.summary?.target_prescriptions || {}).forEach(([number,rx])=>targetPrescriptions.set(Number(number),Number(rx))); }
  const rois = data.rois || [], preferred = rois.filter(roi => /^ptv/i.test(roi.name || ""));
  const suggested = new Set((data.suggested_targets ?? (preferred.length ? preferred : rois.filter(roi => /^gtv/i.test(roi.name || ""))).map(roi => roi.number)).map(Number));
  const prescriptions = data.prescriptions || [], byNumber = new Map(prescriptions.map(p => [Number(p.number), p]));
  $("roi-list").replaceChildren();
  (data.rois || []).forEach(roi => {
    const proposal = byNumber.get(Number(roi.number)), row = el("label", undefined, "roi-option"), checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.value = roi.number; checkbox.checked = suggested.has(Number(roi.number)); checkbox.disabled = readOnly;
    checkbox.addEventListener("change", () => { clearResults(); $("proposal-note").textContent = "Auswahl geändert. Vorschläge erneut übernehmen oder gemeinsame Verschreibung explizit eingeben."; highlightProposals(); renderTargetPrescriptions(); updateButton(); });
    row.append(checkbox, el("span", roi.name || `ROI ${roi.number}`)); if (proposal?.eligible) row.append(el("small", `${fmt(proposal.suggested_Gy, 0)} Gy`));
    else if (suggested.has(Number(roi.number))) row.append(el("small", /^ptv/i.test(roi.name || "") ? "PTV" : /^gtv/i.test(roi.name || "") ? "GTV" : "Target"));

    $("roi-list").append(row);
  });
  $("roi-count").textContent = `${(data.rois || []).length} Strukturen`; $("roi-area").hidden = false;
  $("proposal-area").hidden = !prescriptions.length; $("proposal-body").replaceChildren();
  prescriptions.forEach(proposal => {
    const row = el("tr"); row.dataset.number = proposal.number;
    const name = el("td"), label = el("label", undefined, "proposal-select"), selector = el("input"); selector.type = "checkbox"; selector.checked = suggested.has(Number(proposal.number)); selector.disabled = readOnly; selector.setAttribute("aria-label", `${proposal.name || `Target ${proposal.number}`} auswählen`);
    selector.addEventListener("change", () => { const original = [...$("roi-list").querySelectorAll("input")].find(input => Number(input.value) === Number(proposal.number)); if (original) { original.checked = selector.checked; original.dispatchEvent(new Event("change")); } });
    label.append(selector, document.createTextNode(proposal.name || `Target ${proposal.number}`)); name.append(label); if (!proposal.eligible) name.append(el("small", proposal.reason || "Kein Vorschlag"));
    row.append(name, el("td", fmt(proposal.Dmean_Gy)), el("td", fmt(proposal.D98_Gy)), el("td", proposal.eligible ? `${fmt(proposal.suggested_Gy, 0)} Gy` : "—")); $("proposal-body").append(row);
  });
  $("confirm-proposals").hidden = readOnly;
  $("proposal-note").textContent = readOnly ? "Realer Testplan · Struktur- und Dosisprüfung schreibgeschützt. Zum Hochladen eigene Dateien neu auswählen." : "Vorschläge fachlich prüfen und die tatsächlich verschriebenen Targets bestätigen.";
  $("anonymize-note").textContent = 'DICOM-Kennungen werden vor jeder Übertragung im Browser entfernt. Die Geometrie bleibt erhalten. Originalkennungen bleiben nur im Arbeitsspeicher dieses Tabs. Kein dauerhafter lokaler DICOM-Cache. Die Zuordnung im Arbeitsspeicher wird beim Wechsel der Dateien oder Schließen des Tabs verworfen.';
  highlightProposals(); renderTargetPrescriptions();
}
function renderTargetPrescriptions() {
  const container = $('target-prescriptions'); container.replaceChildren();
  const rois = new Map((state.inspection?.rois || []).map(roi => [Number(roi.number), roi]));
  selectedTargets().forEach(number => {
    const label = el('label', undefined, 'target-rx-row');
    label.append(el('span', rois.get(number)?.name || `ROI ${number}`));
    const input = el('input'); input.type='number'; input.min='0.000001'; input.max='100'; input.step='any'; input.dataset.roi=number;
    input.value = targetPrescriptions.has(number) ? targetPrescriptions.get(number) : '';
    input.placeholder = $('prescription').value || 'Gy'; input.disabled=inspectionReadOnly || state.busy;
    input.setAttribute('aria-label', `${rois.get(number)?.name || `ROI ${number}`} · Gy`);
    input.addEventListener('input', () => { if (input.value === '') targetPrescriptions.delete(number); else targetPrescriptions.set(number, Number(input.value)); clearResults(); });
    label.append(input, el('span','Gy')); container.append(label);
  });
  $('target-prescriptions-area').hidden = !selectedTargets().length;
  updateTargetInputSummary();
}
function updateTargetInputSummary() {
  const output = $('target-input-summary'); if (!output) return;
  const selected = selectedTargets(), values = selected.map(effectiveTargetRx), valid = values.filter(validRx);
  const missing = values.length - valid.length, distinct = [...new Set(valid)].sort((a,b)=>a-b);
  const common = Number($('prescription').value);
  const exceptions = selected.filter(n => targetPrescriptions.has(n) && effectiveTargetRx(n) !== common).length;
  const dose = distinct.length ? `${fmt(distinct[0])}${distinct.length>1 ? '–'+fmt(distinct.at(-1)) : ''} Gy` : tr('Dosis fehlt');
  output.textContent = `${selected.length} ${tr('Targets ausgewählt')} · ${dose}`;
  if (missing) output.textContent += ` · ${missing} ${tr('Dosiswerte ergänzen oder korrigieren')}`;
  else if (exceptions) output.textContent += ` · ${exceptions} ${tr('individuelle Werte')}`;
}
function highlightProposals() {
  const selected = new Set(selectedTargets());
  $('proposal-body').querySelectorAll('tr').forEach(row => {
    const checked = selected.has(Number(row.dataset.number)); row.classList.toggle('selected', checked);
    row.querySelector('input').checked = checked;
  });
}

async function inspectUpload(start = false) {
  targetPrescriptions.clear(); inspectionReadOnly = false;
  discardPreparedUpload();
  clearResults(); state.inspected = false; state.uploadReady = false; state.inspection = null; $("roi-area").hidden = true; $("roi-list").replaceChildren(); $("prescription").value = ""; updateButton();
  ["structure", "dose"].forEach(kind => { $(kind + "-name").textContent = $(kind + "-file").files[0]?.name || "Einzelne DICOM-Datei · max. 250 MB"; });
  $("anonymize-note").textContent = 'DICOM-Kennungen werden vor jeder Übertragung im Browser entfernt. Die Geometrie bleibt erhalten. Originalkennungen bleiben nur im Arbeitsspeicher dieses Tabs. Kein dauerhafter lokaler DICOM-Cache. Die Zuordnung im Arbeitsspeicher wird beim Wechsel der Dateien oder Schließen des Tabs verworfen.';
  if (!canUpload()) { showError("Bitte zuerst anmelden, um Dateien hochzuladen."); return; }
  if (!$("structure-file").files[0]) return;
  if (!start) return;
  pendingInspection = {generation: state.generation};
  busy(true, 'DICOM-Dateien werden im Browser anonymisiert …', 'inspect');
  void drainInspections();
}
async function drainInspections() {
  if (inspectionRunning) return;
  inspectionRunning = true;
  try {
    while (pendingInspection) {
      // Coalesce a quick RTSTRUCT + RTDOSE selection before doing any work.
      await new Promise(resolve => setTimeout(resolve, 160));
      const job = pendingInspection; pendingInspection = null;
      if (!job || job.generation !== state.generation) continue;
      const generation = job.generation;
      try {
        busy(true, 'DICOM-Dateien werden im Browser anonymisiert …', 'inspect');
        const form = new FormData(); await appendFiles(form);
        if (generation !== state.generation) continue;
        busy(true, "Strukturen und Referenzdosis werden geprüft …", 'inspect');
        inspectionController = new AbortController();
        let data;
        for (let attempt = 0; ; attempt++) {
          if (generation !== state.generation) break;
          try {
            // Keep the request alive when files change: canceling fetch does not
            // cancel server computation and would create overlapping inspections.
            data = await request('/api/inspect', {method:'POST', body:form, signal:inspectionController.signal});
            break;
          } catch (error) {
            if (error.status !== 429 || attempt >= 3 || generation !== state.generation) throw error;
            await new Promise(resolve => setTimeout(resolve, 400 * 2 ** attempt));
          }
        }
        if (generation !== state.generation || !data) continue;
        renderInspection(data); state.inspected = true; showWarnings(data.warnings || []);
        if (!data.rois?.length) showError("Die Strukturdatei enthält keine auswählbaren Konturen.");
      } catch (error) { if (generation === state.generation) showError(errorMessage(error)); }
      finally { inspectionController = null; if (generation === state.generation) busy(false); }
    }
  } finally { inspectionRunning = false; }
}
["structure-file", "dose-file", "anonymize-toggle"].forEach(id => $(id).addEventListener("change", () => inspectUpload(false)));
['structure','dose'].forEach(kind=>$('clear-'+kind+'-file').addEventListener('click',()=>{if($(kind+'-file').disabled)return;$(kind+'-file').value='';void inspectUpload(false);}));
$('more-dose-files').addEventListener('click',()=>{const picker=document.getElementById('comparison-files');if(!picker || state.busy)return;document.getElementById('dose-comparisons').scrollIntoView({behavior:'smooth',block:'start'});picker.click();});
$('process-button').addEventListener('click', () => inspectUpload(true));
$('prediction-variant').addEventListener('change', () => { clearResults(); });
$('prescription').addEventListener('input', () => { clearResults(); $('target-prescriptions').querySelectorAll('input').forEach(input => { input.placeholder = $('prescription').value || 'Gy'; }); });
$('apply-prescription-all').addEventListener('click', () => {
  const rx=Number($('prescription').value); if (!validRx(rx) || inspectionReadOnly || state.busy) return;
  selectedTargets().forEach(number => targetPrescriptions.set(number,rx)); clearResults(); renderTargetPrescriptions();
});
$('confirm-proposals').addEventListener('click', () => {
  if (inspectionReadOnly || state.busy) return;
  const selection=new Set(selectedTargets()), proposals=(state.inspection?.prescriptions || []).filter(p=>selection.has(Number(p.number)) && p.eligible && validRx(Number(p.suggested_Gy)));
  proposals.forEach(p=>targetPrescriptions.set(Number(p.number),Number(p.suggested_Gy)));
  clearResults(); renderTargetPrescriptions();
  $('proposal-note').textContent = proposals.length === selection.size ? 'Einzelvorschläge übernommen. Jedes Target behält seine eigene Verschreibung; Werte können bearbeitet werden.' : 'Verfügbare Einzelvorschläge übernommen. Fehlende Werte je Target oder über die gemeinsame Verschreibung ergänzen.';
});
$("prediction-form").addEventListener("submit", async event => {
  event.preventDefault(); if (!canUpload() || state.busy || !state.inspected || !state.uploadReady || !selectedTargets().length || !selectedTargets().every(number => validRx(effectiveTargetRx(number)))) return;
  clearResults(); const generation = state.generation; state.controller = new AbortController();
  try {
    busy(true, 'DICOM-Dateien werden im Browser anonymisiert …');
    const form = new FormData(); await appendFiles(form); if (generation !== state.generation) return; form.append("targets", JSON.stringify(selectedTargets())); form.append("prescription", validRx(Number($("prescription").value)) ? $("prescription").value : String(effectiveTargetRx(selectedTargets()[0]))); form.append("target_prescriptions", JSON.stringify(selectedPrescriptionMap())); form.append("fractions", "1"); form.append("regularization_sigma_mm", $("prediction-variant").value === "0" ? "0" : "1"); form.append("calibrated", String($("prediction-variant").value === "calibrated"));
    busy(true, "Geometrie wird ausgewertet und Dosis abgeschätzt …");
    const data = await request("/api/predict", {method: "POST", body: form, signal: state.controller.signal});
    if (generation === state.generation) renderResult(data, false);
  } catch (error) { if (generation === state.generation) showError(errorMessage(error)); }
  finally { if (generation === state.generation) busy(false); }
});
$("demo-button").addEventListener("click", async () => {
  clearResults(); const generation = state.generation; state.controller = new AbortController(); busy(true, "Synthetisches Beispiel wird geladen …");
  try { const data = await request("/api/demo", {signal: state.controller.signal}); if (generation === state.generation) renderResult(data, true); }
  catch (error) { if (generation === state.generation) showError(errorMessage(error)); }
  finally { if (generation === state.generation) busy(false); }
});
$("test-case-button").addEventListener("click", async () => {
  clearResults(); state.inspected = false; state.uploadReady = false; $("roi-area").hidden = true; $("roi-list").replaceChildren();
  const generation = state.generation; state.controller = new AbortController(); busy(true, "Realer Testplan und zurückgehaltene Vorhersage werden geladen …");
  try {
    const data = await request(`/api/test-cases/${encodeURIComponent($("test-case-select").value)}`, {signal: state.controller.signal});
    if (generation !== state.generation) return;
    renderInspection(data, true); $("prescription").value = data.planned_prescription_Gy ?? ""; renderTargetPrescriptions();
    renderResult(data.result, false); $("result-badge").textContent = `REALER TESTPLAN ${data.case || ""} · ZURÜCKGEHALTENE VORHERSAGE`;
  } catch (error) { if (generation === state.generation) showError(errorMessage(error)); }
  finally { if (generation === state.generation) busy(false); }
});
async function loadTestCases() {
  try { const data = await request("/api/test-cases"); (data.cases || []).forEach(item => { const option = el("option", item.label); option.value = item.id; $("test-case-select").append(option); }); $("test-case-area").hidden = !data.cases?.length; } catch { $("test-case-area").hidden = true; }
}
function renderResult(data, demo) {
  $('difference-controls').hidden=true; $('scale-zero').hidden=true; state.sliceTransform=null;
  const hasSlices = Boolean(data.slices?.predicted?.length);
  if (!hasSlices && !data.summary) throw new Error("Die Serverantwort enthält keine darstellbaren Ergebnisse.");
  state.result = data; state.mode = "predicted"; state.slice = Math.floor((data.slices?.predicted?.length || 0) / 2); state.zoom = 1; state.focus = null; state.focusTargetNumber = null; state.orbit = {yaw: -0.45, pitch: 0.3, zoom: 1, center: null}; state.contourCache.clear();
  document.querySelector('.viewer-card').classList.toggle('metadata-only', !hasSlices);
  $('archive-view-note').hidden = hasSlices;
  $('view-grid-note').hidden = !hasSlices;
  document.querySelectorAll('.plan-controls input,.plan-controls button,.slice-tools input,#isodose-levels input').forEach(input => { input.disabled = !hasSlices; });
  $("empty-state").hidden = true; $("results").hidden = false; $("demo-notice").hidden = !demo;
  $("result-badge").textContent = demo ? "SYNTHETISCHE DEMO" : "FORSCHUNG · NICHT KLINISCH VALIDiert".toUpperCase();
  showWarnings(data.warnings || []); $("summary").replaceChildren();
  const summary = data.summary || {};
  renderDVHMethodNote(summary);

  const targetCount = summary.target_count ?? summary.targets_count ?? (data.metrics || []).filter(metric => /target|ptv|gtv/i.test(metric.role || "")).length;
  [["Verschreibung", prescriptionSummary(summary), "Gy"], ["Zielvolumen", fmt(summary.target_volume_cc ?? summary.total_target_volume_cc ?? (data.metrics || []).filter(metric => /target|ptv|gtv/i.test(metric.role || "")).reduce((sum, metric) => sum + Number(metric.volume_cc || 0), 0)), "cm³"], ["Targets", fmt(targetCount, 0), "in 1 Fraktion"]].forEach(([label, value, unit]) => { const item = el("div", undefined, "summary-item"); const strong = el("strong", value); strong.append(el("small", unit)); item.append(el("span", label), strong); $("summary").append(item); });
  $("slice-slider").max = Math.max(0, (data.slices?.predicted?.length || 0) - 1); $("slice-slider").value = state.slice;
  if (!hasSlices) { $('slice-meta').textContent = ''; $('slice-number').textContent = ''; }
  document.querySelectorAll("[data-mode]").forEach(button => { button.disabled = !hasSlices || (button.dataset.mode !== "predicted" && !data.slices?.reference); button.setAttribute("aria-pressed", String(button.dataset.mode === "predicted")); });
  renderResultFiles(data, demo); renderPlanMetrics(summary); renderLocalMetrics(); renderRingBenchmark(); renderMetrics(); renderDVHSelector(); renderLegend(); renderTargets(); drawSlice(); drawSpatial(); drawDVH();
  window.DoseComparisons?.setBase(data);
  updateButton();
}
function renderDVHMethodNote(summary=state.result?.summary || {}) {
  const marker=summary.prediction_regularization_sigma_mm, sigma=Number(marker);
  const common=tr('DVH direkt aus derselben Dosis wie Anzeige und Export. Referenz unverändert. Keine native TPS-DVH.');
  const prefix=marker !== undefined && marker !== null && Number.isFinite(sigma)
    ? sigma>0 ? `${tr('Vorhersage räumlich regularisiert')} (σ = ${fmt(sigma,1)} mm). ${tr('Räumliche Regularisierung verändert die Dosis; keine zusätzliche klinische Validierung.')}` : tr('Basismodell ohne räumliche Regularisierung.')
    : tr('Empirische DVHs aus Dosiswerten des Berechnungsrasters; gleiche Werte bleiben als Stufen erhalten.');
  const calibration=summary.prediction_calibration_scale>1 ? `${tr('Empirische Dosiskalibrierung')} × ${fmt(summary.prediction_calibration_scale,2)}. ` : '';
  $('dvh-method-note').textContent=`${calibration}${prefix} ${common} ${tr('Bei großen Kurven werden Rangquantile mit höchstens 0,5 Prozentpunkten ausgelassenem Volumen je Intervall dargestellt.')}`;
}
function renderResultFiles(data, demo) {
  $("metrics-excel-note").textContent=tr("Alle geladenen Dosisvergleiche · Kennzahlen je Struktur, Zusammenfassungen und Auswertungshinweise · lokal im Browser erstellt");
  const synthetic = demo || data.demo === true || ["synthetic", "synthetic_demo"].includes(data.source);
  $("download-links").replaceChildren(); $("storage-error").hidden = !data.storage_error;
  $("storage-error").textContent = data.storage_error ? `Nicht gespeichert: ${String(data.storage_error)} Die dargestellte Berechnung bleibt verfügbar.` : "";
  $("save-status").textContent = data.saved_run_id ? (data.storage_error ? "Speicherung unvollständig" : "Privat gespeichert") : state.auth.enabled && !synthetic ? "Keine gespeicherte Analyse bestätigt" : "";
  let linkCount = 0;
  if (!synthetic) (data.downloads || []).forEach(item => {
    if (!item?.url) return;
    let url; try { url = new URL(item.url, window.location.href); } catch { return; }
    if (url.protocol !== "https:" && !(url.protocol === "http:" && url.origin === window.location.origin)) return;
    const link = el("a", item.label || "Forschungsdatei herunterladen", "download-link"); link.href = url.href; link.rel = "noopener noreferrer"; link.target = "_blank";
    if (url.origin === window.location.origin) link.download = "";
    $("download-links").append(link); linkCount++;
  });
  $("export-note").hidden = !linkCount;
  $('download-retention').hidden = !linkCount;
  const originalAllowed = Boolean(preparedUpload?.context && !synthetic && (data.downloads || []).some(item => /(?:^|\/)prediction\.dcm(?:[?#]|$)/.test(item.url || '')));
  $('original-export-option').hidden = !originalAllowed; $('original-export-toggle').checked = false;
  $('original-export-note').hidden = true; $('original-export-button').hidden = true;
  $("result-files").hidden = !linkCount && !data.saved_run_id && !data.storage_error && !data.metrics?.length;
  $("metrics-excel-button").disabled = !data.metrics?.length;
  if (data.saved_run_id && state.auth.authenticated) void loadRuns();
}
function resetUploadState() {
  targetPrescriptions.clear(); inspectionReadOnly = false;
  discardPreparedUpload();
  clearResults(); state.inspected = false; state.uploadReady = false; state.inspection = null;
  $("roi-area").hidden = true; $("roi-list").replaceChildren(); $("proposal-body").replaceChildren(); $("prescription").value = "";
  ["structure", "dose"].forEach(kind => { $(kind + "-file").value = ""; $(kind + "-name").textContent = "Einzelne DICOM-Datei · max. 250 MB"; });
  updateButton();
}
function renderSession() {
  window.DoseQueue?.configure({enabled: Boolean(state.auth.queue_enabled && (!state.auth.enabled || state.auth.authenticated)), request:rawRequest});
  const cloud = state.auth.enabled;
  $("account-panel").hidden = !activation && !registration && !cloud && state.auth.ready;
  $('activation-form').hidden = !registration && !activation;
  $('activation-code-label').hidden = Boolean(activation);
  $('activation-code').required = !activation;
  $("login-form").hidden = (Boolean(activation) || registration) || !cloud || state.auth.authenticated;
  $("signed-in").hidden = !cloud || !state.auth.authenticated;
  $("archive-panel").hidden = !cloud || !state.auth.authenticated;
  $("account-email").textContent = state.auth.email || "";
  $("account-status").textContent = registration || activation ? "REGISTRIERUNG" : state.auth.authenticated ? "ANGEMELDET" : cloud ? "ANMELDUNG" : "";
  $("deployment-label").textContent = !state.auth.ready ? "Verbindung wird geprüft" : cloud ? "Privater Forschungsbereich" : "Lokale Auswertung";
  $("privacy-note").textContent = !state.auth.ready ? "Server- und Speichermodus konnten noch nicht geprüft werden. Datei-Uploads bleiben gesperrt." : cloud ? "Upload insgesamt maximal 250 MB. Anonymisierung im Browser vor der Übertragung. Privat gespeichert werden nur Kennwerte, DVHs und Targetgeometrie. DICOM-Downloads stehen vorübergehend für 10 Minuten bereit; keine DICOM-Dateien im Cloud-Archiv." : "Upload insgesamt maximal 250 MB. Anonymisierung im Browser vor der Übertragung. Verarbeitung im Arbeitsspeicher des Servers; keine kontobasierte Cloud-Speicherung.";
  $("anonymize-toggle").checked = true;
  $("anonymize-note").textContent = 'DICOM-Kennungen werden vor jeder Übertragung im Browser entfernt. Die Geometrie bleibt erhalten. Originalkennungen bleiben nur im Arbeitsspeicher dieses Tabs. Kein dauerhafter lokaler DICOM-Cache. Die Zuordnung im Arbeitsspeicher wird beim Wechsel der Dateien oder Schließen des Tabs verworfen.';
  updateButton();
}
async function loadSession() {
  state.authBusy = true; $("session-retry").hidden = true; updateButton();
  try {
    const session = await request("/api/auth/session"); state.auth = {...session, ready: true};
    $("auth-message").textContent = session.enabled && !session.authenticated ? "Bitte vor dem Datei-Upload anmelden." : "";
    renderSession(); if (session.authenticated) await loadRuns();
  } catch (error) {
    state.auth = {ready: false, enabled: false, authenticated: false}; renderSession();
    $("auth-message").textContent = `Kontostatus nicht verfügbar: ${errorMessage(error)}`; $("session-retry").hidden = false;
  } finally { state.authBusy = false; updateButton(); }
}
$("session-retry").addEventListener("click", loadSession);
$("login-form").addEventListener("submit", async event => {
  event.preventDefault(); if (state.authBusy || state.busy) return;
  state.authBusy = true; updateButton(); $("auth-message").textContent = "Anmeldung wird geprüft …";
  try {
    const session = await request("/api/auth/login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email: $("login-email").value.trim(), password: $("login-password").value})});
    state.auth = {...session, ready: true}; resetUploadState(); renderSession();
    $("auth-message").textContent = session.authenticated ? "Angemeldet. Neue Analysen werden privat gespeichert." : "Anmeldung nicht bestätigt.";
    if (session.authenticated) await loadRuns();
  } catch (error) { $("auth-message").textContent = errorMessage(error); }
  finally { $("login-password").value = ""; state.authBusy = false; updateButton(); }
});
$("logout-button").addEventListener("click", async () => {
  if (state.authBusy || state.busy) return;
  state.authBusy = true; updateButton(); $("auth-message").textContent = "Abmeldung …";
  try {
    await request("/api/auth/logout", {method: "POST"}); state.runsGeneration++;
    state.auth = {...state.auth, authenticated: false, email: null}; resetUploadState();
    ["structure", "dose"].forEach(kind => { $(kind + "-file").value = ""; $(kind + "-name").textContent = "Einzelne DICOM-Datei · max. 250 MB"; });
    $("archive-list").replaceChildren(); $("archive-count").textContent = ""; renderSession(); $("auth-message").textContent = "Abgemeldet.";
  } catch (error) { $("auth-message").textContent = `Abmeldung nicht bestätigt: ${errorMessage(error)}`; }
  finally { state.authBusy = false; updateButton(); }
});
async function loadRuns() {
  if (!state.auth.authenticated) return;
  const generation = ++state.runsGeneration; $("archive-message").textContent = "Gespeicherte Analysen werden geladen …";
  try {
    const data = await request("/api/runs"); if (generation !== state.runsGeneration || !state.auth.authenticated) return;
    $("archive-list").replaceChildren(); const runs = data.runs || []; $("archive-count").textContent = `(${runs.length})`;
    runs.forEach(run => {
      const button = el("button", undefined, "archive-item"); button.type = "button";
      const date = new Date(run.created_at), title = Number.isFinite(date.getTime()) ? date.toLocaleString(window.DoseI18n.language === "en" ? "en-GB" : "de-DE", {dateStyle: "medium", timeStyle: "short"}) : "Gespeicherte Analyse";
      button.append(el("strong", title), el("span", `${prescriptionSummary(run.summary)} Gy · ${fmt(run.summary?.target_count ?? run.summary?.targets_count, 0)} Targets`));
      button.addEventListener("click", () => openRun(run.id)); $("archive-list").append(button);
    });
    $("archive-message").textContent = runs.length ? "Nur Analysen Ihres Kontos." : "Noch keine gespeicherten Analysen."; updateButton();
  } catch (error) { if (generation === state.runsGeneration) $("archive-message").textContent = `Archiv nicht verfügbar: ${errorMessage(error)}`; }
}
$("archive-refresh").addEventListener("click", loadRuns);
async function openRun(id) {
  if (state.busy || state.authBusy || !state.auth.authenticated) return;
  resetUploadState(); const generation = state.generation; state.controller = new AbortController(); busy(true, "Gespeicherte Analyse wird geöffnet …");
  try {
    const data = await request(`/api/runs/${encodeURIComponent(id)}`, {signal: state.controller.signal});
    if (generation !== state.generation) return;
    const synthetic = data.demo === true || ["synthetic", "synthetic_demo"].includes(data.source);
    renderResult({...data, saved_run_id: data.saved_run_id || id}, synthetic);
    $("result-badge").textContent = synthetic ? "GESPEICHERTE SYNTHETISCHE DEMO" : "GESPEICHERTE FORSCHUNGSANALYSE";
  } catch (error) { if (generation === state.generation) showError(errorMessage(error)); }
  finally { if (generation === state.generation) busy(false); }
}
function renderRingBenchmark() {
  $("ring-benchmark").open=false;
  const benchmark = state.result?.ring_benchmark, panel = $("ring-benchmark");
  panel.hidden = !benchmark; $("ring-body").replaceChildren(); if (!benchmark) return;
  const width = Number(benchmark.ring_width_mm) || 10;
  const interval = benchmark.relative_dose_interval || [0.5, 0.8], low = Number(interval[0]), high = Number(interval[1]);
  $("ring-description").textContent = `${fmt(width, 0)}-mm-Außenring je Target · alle ausgewählten Targets ausgespart · Ring-DVH von ${fmt(low * 100, 0)}–${fmt(high * 100, 0)} % Rx`;
  $("ring-definition").textContent = typeof benchmark.definition === "string" ? benchmark.definition : "Außenring aus geometrischem Abstand zum jeweiligen Target; alle ausgewählten Targets werden ausgeschlossen. Keine Einschränkung auf Hirngewebe.";
  $("ring-method-code").textContent = `Rᵢ = {Voxel außerhalb Targetᵢ mit Abstand ≤ ${fmt(width, 0)} mm}, ohne alle ausgewählten Targets
u = Dosis / Rx
AUC(D, Rᵢ) = ∫[${fmt(low, 2)}, ${fmt(high, 2)}] Volumen{v ∈ Rᵢ : D(v) ≥ u × Rx} du
           = Σ[v ∈ Rᵢ] Voxelvolumen × clip(D(v) / Rx − ${fmt(low, 2)}, 0, ${fmt(high - low, 2)})
Verhältnis = AUC(Vorhersage, Rᵢ) / AUC(Referenzplan, Rᵢ)
Nur bei vollständiger Dosisunterstützung und positivem Nenner.`;
  (benchmark.rows || []).forEach(item => {
    const row = el("tr"), name = el("td", item.roi), ratio = el("td", fmt(item.ratio, 3));
    if (item.reason) name.append(el("small", item.reason));
    ratio.className = "ring-ratio";
    row.append(name, el("td", fmt(item.ring_volume_cc, 2)), el("td", fmt(item.coverage_pct, 1)), el("td", fmt(item.predicted_auc_cc, 3)), el("td", fmt(item.reference_auc_cc, 3)), ratio, el("td", `${fmt(item.predicted_D98_Gy)} / ${fmt(item.reference_D98_Gy)}`), el("td", fmt(item.reference_V100_pct)));
    $("ring-body").append(row);
  });
  if (!benchmark.rows?.length) { const row = el("tr"), cell = el("td", "Für diese Antwort liegen keine auswertbaren Ringdaten vor."); cell.colSpan = 8; row.append(cell); $("ring-body").append(row); }
}
function comparisonValues(predicted, reference, unit='') {
  const group=el('span',undefined,'comparison-values');
  group.append(el('span',`${fmt(predicted,2)}${unit}`,'predicted-value'));
  if (reference !== undefined) group.append(el('span',`${fmt(reference,2)}${unit}`,'reference-value'));
  return group;
}
function renderPlanMetrics(summary) {
  const container=$('plan-metrics');container.replaceChildren();container.hidden=!summary.predicted;
  if(!summary.predicted)return;
  const legend=el('p',undefined,'comparison-key');legend.append(el('span',tr('Vorhersage'),'predicted-value'),el('span',tr('Originalplan / Referenz'),'reference-value'));container.append(legend);
  const grid=el('div',undefined,'plan-metrics-grid');
  [['Global 1/CI','inverse_CI',''],['Global GI','GI',''],['V12 · ausgewertete Region','V12_domain_cc',' cm³'],['V12 · außerhalb Targets','V12_outside_targets_domain_cc',' cm³'],['Mittelwert 1/CI · lokal','mean_local_inverse_CI',''],['Mittelwert GI · lokal','mean_local_GI','']].forEach(([label,key,unit])=>{
    const item=el('div');item.append(el('span',tr(label)));
    const value=kind=>key==='inverse_CI' ? (summary[kind]?.Paddick_CI>0 ? 1/summary[kind].Paddick_CI : null) : summary[kind]?.[key];
    item.append(comparisonValues(value('predicted'),summary.reference ? value('reference') : undefined,unit));
    if(key.startsWith('mean_')) item.append(el('small',`${summary.predicted[key+'_n'] ?? 0} / ${summary.target_count ?? 0} ${tr('Targets auswertbar')}`));
    grid.append(item);
  });container.append(grid);
}
function renderLocalMetrics() {
  const body=$('local-metrics-body');body.replaceChildren();
  const available=state.result.summary?.local_metrics_version===1;
  $('local-metrics-card').hidden=!available;
  if(!available)return;
  $('ci-model-note').textContent=tr(state.result.summary.prediction_calibration_scale>1
    ? 'Empirisch kalibriertes Dosisfeld: Konformitätsbias reduziert, nicht beseitigt. Die Korrektur wurde innerhalb der 10-Fall-Serie mit getrennter Parameterwahl geprüft. Kein Nachweis eines erreichbaren Optimums; individuelle D98-Abweichungen können zunehmen.'
    : 'Unkalibrierte Vorhersage: 1/CI ist systematisch zu niedrig (10-Fall-Test: Mittelwert 1,07 statt 1,28). Ein nahezu idealer Modellwert belegt keinen erreichbaren besseren Plan.');
  (state.result.metrics || []).filter(m=>m.role==='target').forEach(m=>{
    const row=el('tr'),name=el('td',m.roi);name.append(el('small',`Rx ${fmt(m.prescription_Gy)} Gy`));row.append(name);
    ['local_inverse_CI','local_GI','local_V12_cc'].forEach(key=>{const td=el('td');td.append(comparisonValues(m.predicted?.[key],m.reference ? m.reference[key] : undefined));row.append(td)});
    row.append(el('td',`${fmt(m.local_coverage_pct,1)} %`));body.append(row);
  });
}

document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => { if (!state.result) return; state.mode = button.dataset.mode; document.querySelectorAll("[data-mode]").forEach(item => item.setAttribute("aria-pressed", String(item === button))); drawSlice(); }));
$("slice-slider").addEventListener("input", event => { setSlice(Number(event.target.value)); });
$("outline-toggle").addEventListener("change", drawSlice);
const heatStops = [[16,36,45], [26,102,110], [41,168,148], [238,229,135], [225,154,85], [214,91,89]];
function interpolate(stops, fraction) { const x = Math.max(0, Math.min(1, fraction)) * (stops.length - 1); const index = Math.min(stops.length - 2, Math.floor(x)); const weight = x - index; return stops[index].map((value, i) => Math.round(value * (1 - weight) + stops[index + 1][i] * weight)); }
function differenceMaximum() { return Number($('difference-range').value) || 5; }
function differenceColor(value, maximum=differenceMaximum()) { return interpolate([[33,102,172],[247,247,247],[178,24,43]],(value/maximum+1)/2); }
function displayMaximum() { const summary = state.result.summary || {}; return Number(summary.max_display_Gy) > 0 ? Number(summary.max_display_Gy) : Math.max(Number(summary.prescription_Gy) * 1.5, 1); }
function setupCanvas(canvas) { const box = canvas.getBoundingClientRect(); const ratio = window.devicePixelRatio || 1; canvas.width = Math.round(box.width * ratio); canvas.height = Math.round(box.height * ratio); const context = canvas.getContext("2d"); context.setTransform(ratio, 0, 0, ratio, 0, 0); return {context, width: box.width, height: box.height}; }
const isoLevels = [5, 10, 12, 18, 20, 24];
const isoColors = ["#689ee7", "#6ed8d2", "#b1dd70", "#eee679", "#f2a164", "#f37a9a"];
isoLevels.forEach((level, index) => {
  const label = el("label"), input = el("input"); input.type = "checkbox"; input.value = level; input.checked = true; input.style.accentColor = isoColors[index];
  input.addEventListener("change", drawSlice); const swatch = el("i"); swatch.style.background = isoColors[index]; label.append(input, swatch, document.createTextNode(`${level} Gy`)); $("isodose-levels").append(label);
});
$("isodose-levels").append(el("span", "— Vorhersage (blau)   ╌ Originalplan (orange)", "line-key"));
function spacingXY() { const raw = state.result?.slices?.spacing_mm; return Array.isArray(raw) ? [Number(raw[2]) || 1, Number(raw[1]) || 1] : [Number(raw) || 1, Number(raw) || 1]; }
function isoSegments(values, level) {
  // Marching squares on sample centers; unknown dose never creates a contour.
  const segments = [], lookup = {1:[[3,0]],2:[[0,1]],3:[[3,1]],4:[[1,2]],6:[[0,2]],7:[[3,2]],8:[[2,3]],9:[[0,2]],11:[[1,2]],12:[[1,3]],13:[[0,1]],14:[[3,0]]};
  for (let y = 0; y < values.length - 1; y++) for (let x = 0; x < values[y].length - 1; x++) {
    const v = [values[y][x], values[y][x+1], values[y+1][x+1], values[y+1][x]];
    if (!v.every(Number.isFinite)) continue;
    const code = v.reduce((bits, value, i) => bits | (value >= level ? 1 << i : 0), 0); if (!code || code === 15) continue;
    const corners = [[x,y],[x+1,y],[x+1,y+1],[x,y+1]], edges = [[0,1],[1,2],[2,3],[3,0]];
    const point = edge => { const [a,b] = edges[edge], t = (level-v[a])/(v[b]-v[a]); return [corners[a][0]+t*(corners[b][0]-corners[a][0]),corners[a][1]+t*(corners[b][1]-corners[a][1])]; };
    let pairs = lookup[code];
    if (code === 5 || code === 10) { const centerHigh = v.reduce((sum,val) => sum+val,0)/4 >= level; pairs = (code === 5) === centerHigh ? [[0,1],[2,3]] : [[3,0],[1,2]]; }
    for (const [a,b] of pairs) segments.push([point(a),point(b)]);
  }
  return segments;
}
function drawSlice() {
  if (!state.result?.slices?.predicted?.length) return;
  const slices = state.result.slices, predicted = slices.predicted[state.slice], reference = slices.reference?.[state.slice], target = slices.target?.[state.slice];
  if (!predicted?.length || !predicted[0]?.length) return;
  const rows = predicted.length, columns = predicted[0].length, maximum = state.mode === "difference" ? differenceMaximum() : displayMaximum();
  const {context, width, height} = setupCanvas($("slice-canvas")); if (!width || !height) return;
  context.fillStyle = "#111e34"; context.fillRect(0,0,width,height);
  const [spacingX,spacingY] = spacingXY(), baseScale = Math.min((width-50)/Math.max(columns*spacingX,headEnabled()?syntheticHead().radii[0]*2.3:0),(height-55)/Math.max(rows*spacingY,headEnabled()?syntheticHead().radii[1]*2.3:0)), scale = baseScale*state.zoom;
  const pw = scale*spacingX, ph = scale*spacingY, origin = slices.origin_lps_mm || [0,0,0];
  const viewCenter=state.focus || (headEnabled()?syntheticHead().center:null);
  const centerX = viewCenter ? (viewCenter[0]-origin[0])/spacingX : (columns-1)/2, centerY = viewCenter ? (viewCenter[1]-origin[1])/spacingY : (rows-1)/2;
  const left = width/2-centerX*pw, top = height/2-centerY*ph;
  state.sliceTransform={left,top,pw,ph,rows,columns,width,height};
  context.save(); context.beginPath(); context.rect(24,30,width-48,height-55); context.clip();
  if ($("background-toggle").checked) {
    const gradient = context.createRadialGradient(width/2,height/2,0,width/2,height/2,Math.max(width,height)*.65); gradient.addColorStop(0,"#34425a"); gradient.addColorStop(1,"#17263f"); context.fillStyle = gradient; context.fillRect(0,0,width,height);
    // A regular millimetre grid, deliberately without anatomical structures or HU.
    context.strokeStyle = "#89979c22"; context.lineWidth = 1; context.beginPath();
    const step = 20*scale;
    for (let x = ((left-origin[0]*scale)%step+step)%step; x < width; x += step) { context.moveTo(x,0); context.lineTo(x,height); }
    for (let y = ((top-origin[1]*scale)%step+step)%step; y < height; y += step) { context.moveTo(0,y); context.lineTo(width,y); } context.stroke();
  }
  const offscreen = document.createElement("canvas"); offscreen.width = columns; offscreen.height = rows; const offContext = offscreen.getContext("2d"), pixels = offContext.createImageData(columns,rows);
  const heat = $("heatmap-toggle").checked || state.mode === "difference";
  for (let y=0;y<rows;y++) for (let x=0;x<columns;x++) {
    const p = predicted[y][x], r = reference?.[y]?.[x], pOK = Number.isFinite(p), rOK = Number.isFinite(r);
    const unsupported = state.mode === "predicted" ? !pOK : state.mode === "reference" ? !rOK : state.mode === "difference" ? !pOK || !rOK : !pOK && !rOK;
    let rgb, alpha;
    if (unsupported) { rgb = (Math.floor(x/4)+Math.floor(y/4))%2 ? [66,75,80] : [39,49,54]; alpha = 180; }
    else if (heat) { const value = state.mode === "reference" ? r : state.mode === "difference" ? p-r : p; rgb = Number.isFinite(value) ? state.mode === "difference" ? differenceColor(value,maximum) : interpolate(heatStops,value/maximum) : [0,0,0]; alpha = Number.isFinite(value) ? (state.mode === "difference" ? 255 : 175) : 0; }
    else { rgb=[0,0,0]; alpha=0; }
    pixels.data.set([...rgb,alpha],(y*columns+x)*4);
  }
  offContext.putImageData(pixels,0,0); context.imageSmoothingEnabled=false; context.drawImage(offscreen,left-pw/2,top-ph/2,columns*pw,rows*ph);
  if ($("isodose-toggle").checked && state.mode !== "difference") {
    const selected = new Set([...$("isodose-levels").querySelectorAll("input:checked")].map(input=>Number(input.value)));
    const kinds = state.mode === "overlay" ? ["predicted","reference"] : [state.mode];
    kinds.forEach(kind => { const values = slices[kind]?.[state.slice]; if (!values) return;
      isoLevels.forEach((level,index) => { if (!selected.has(level)) return;
        context.strokeStyle=isoColors[index]; context.lineWidth=kind === "reference" ? 1.5 : 1.8; context.setLineDash(kind === "reference" ? [5,4] : []); context.beginPath();
        const levelPaths=slices.isodose_paths?.[kind]?.[state.slice];
        const pathKey=levelPaths ? Object.keys(levelPaths).find(key=>Number(key)===level) : undefined;
        const paths=pathKey === undefined ? undefined : levelPaths[pathKey];
        if (Array.isArray(paths)) {
          // Full calculation-grid polylines in LPS. Preserve open boundaries and
          // supplied vertices exactly; never spline-fit or close an open path.
          for (const path of paths) {
            if (!path?.length || !path.every(p=>p?.length>=3 && p.every(Number.isFinite))) continue;
            path.forEach((p,index)=>{const x=left+(p[0]-origin[0])*scale,y=top+(p[1]-origin[1])*scale;if(index)context.lineTo(x,y);else context.moveTo(x,y);});
          }
        } else {
          const key = `${kind}:${state.slice}:${level}`; if (!state.contourCache.has(key)) state.contourCache.set(key,isoSegments(values,level));
          if (state.contourCache.size > 240) state.contourCache.delete(state.contourCache.keys().next().value);
          for (const [a,b] of state.contourCache.get(key)) { context.moveTo(left+a[0]*pw,top+a[1]*ph); context.lineTo(left+b[0]*pw,top+b[1]*ph); }
        }
        if(state.mode === 'overlay') {
          context.save();context.strokeStyle=kind === 'predicted' ? '#75bcff' : '#ffc078';context.lineWidth=kind === 'predicted' ? 4.5 : 4;context.stroke();context.restore();
        }
        context.stroke();
      });
    }); context.setLineDash([]);
  }
  if ($("outline-toggle").checked) {
    context.strokeStyle="#f5fff0"; context.lineWidth=1.25; context.beginPath();
    const loops = slices.target_contours?.[state.slice];
    if (Array.isArray(loops)) {
      // Native LPS polylines; never curve-fit or smooth supplied geometry.
      for (const loop of loops) {
        if (!loop?.length || !loop.every(p => p?.length >= 3 && p.every(Number.isFinite))) continue;
        loop.forEach((p,index) => { const x=left+(p[0]-origin[0])*scale, y=top+(p[1]-origin[1])*scale; if(index) context.lineTo(x,y); else context.moveTo(x,y); });
        context.closePath();
      }
    } else if (target) {
      // Legacy responses: a half-level boundary on padded binary sample centres.
      const mask=[Array(columns+2).fill(0), ...target.map(row=>[0,...row.map(v=>v?1:0),0]), Array(columns+2).fill(0)];
      for (const [a,b] of isoSegments(mask,.5)) {context.moveTo(left+(a[0]-1)*pw,top+(a[1]-1)*ph);context.lineTo(left+(b[0]-1)*pw,top+(b[1]-1)*ph);}
    }
    context.stroke();
  }
  if (headEnabled()) {
    const head=syntheticHead(), z=slices.z_mm[state.slice], q=(z-head.center[2])/head.radii[2];
    if (Math.abs(q)<1) {
      const fraction=Math.sqrt(1-q*q),x=left+(head.center[0]-origin[0])*scale,y=top+(head.center[1]-origin[1])*scale,rx=head.radii[0]*fraction*scale,ry=head.radii[1]*fraction*scale;
      context.save();context.strokeStyle='#9bb9e6';context.lineWidth=1.5;context.setLineDash([7,4]);context.beginPath();context.ellipse(x,y,rx,ry,0,0,Math.PI*2);context.stroke();context.setLineDash([]);
      context.beginPath();context.moveTo(x-rx*.09,y-ry);context.lineTo(x,y-ry-10*scale*fraction);context.lineTo(x+rx*.09,y-ry);context.stroke();
      for (const side of [-1,1]) {context.beginPath();context.ellipse(x+side*rx,y,5*scale*fraction,13*scale*fraction,0,0,Math.PI*2);context.stroke();}context.restore();
    }
  }
  if (state.focus) { context.strokeStyle="#ffffff66"; context.lineWidth=1; context.beginPath(); context.moveTo(width/2-11,height/2); context.lineTo(width/2+11,height/2); context.moveTo(width/2,height/2-11); context.lineTo(width/2,height/2+11); context.stroke(); }
  context.restore();
  if(headEnabled()){context.fillStyle='#c2d6f3';context.font='10px Segoe UI';context.textAlign='left';context.fillText(tr('Synthetischer Kopf · Lage aus Targets geschätzt · keine Anatomie'),30,height-27);}
  context.fillStyle="#aabdc2"; context.font="9px Segoe UI"; const barMm = state.zoom > 2 ? 10 : 20, bar = barMm*scale; context.fillRect(width-38-bar,height-22,bar,1); context.textAlign="right"; context.fillText(`${barMm} mm`,width-38,height-27);
  $("slice-number").textContent=`${state.slice+1} / ${slices.predicted.length}`; $("slice-slider").value=state.slice;
  const z=slices.z_mm?.[state.slice]; $("slice-meta").textContent=`Axial · LPS${z !== undefined ? ` · z = ${fmt(z)} mm` : ""} · ${fmt(spacingX)} mm Raster`;
  $("zoom-label").textContent=`${fmt(state.zoom*100,0)} %`;
  $("view-mode-label").textContent={predicted:"VORHERSAGE",reference:"REFERENZ",overlay:"VORHERSAGE + REFERENZ",difference:"VORHERSAGE − REFERENZ"}[state.mode];
  ['heatmap-toggle','isodose-toggle'].forEach(id=>$(id).parentElement.hidden=state.mode==='difference');
  $('isodose-levels').hidden=state.mode==='difference';
  $('difference-controls').hidden=state.mode!=='difference'; $('scale-zero').hidden=state.mode!=='difference';
  $('difference-probe').textContent=tr('Maus über das Dosisbild bewegen.');
  $("color-scale").classList.toggle("difference",state.mode === "difference"); $("scale-min").textContent=state.mode === "difference" ? `−${fmt(maximum)} Gy` : "0 Gy"; $("scale-max").textContent=`${state.mode === "difference" ? "+" : ""}${fmt(maximum)} Gy`;
  $("view-note").textContent=state.mode === "difference" ? (window.DoseI18n.language==='en' ? `Prediction − reference in Gy; not a gamma analysis. Colours saturate at ±${fmt(maximum)} Gy. Fixed scale across slices. Checkerboard: no shared dose coverage. Display grid, not full calculation resolution.` : `Vorhersage − Referenz in Gy; keine Gamma-Analyse. Farben ab ±${fmt(maximum)} Gy gesättigt. Gleiche Skala über alle Schichten. Kariert: keine gemeinsame Dosisabdeckung. Darstellungsraster, nicht volle Berechnungsauflösung.`) : "Isodosen: Vorhersage durchgezogen, Referenz gestrichelt. Gemeinsame Gy-Skala; grau kariert: keine Dosisunterstützung. Mausrad: Schichten; Strg + Mausrad: Zoom. Begrenzte Targetregion, kein CT.";
  $('view-grid-note').hidden=!slices.isodose_paths || state.mode==='difference';
  $('view-grid-note').textContent='Isodosen aus dem vollständigen Berechnungsraster. Die Farbdosis verwendet das gröbere Darstellungsraster; Konturlinien werden nicht geglättet.';
  document.querySelector(".background-label").hidden=!$("background-toggle").checked;
}
function renderTargets() {
  $("target-list").replaceChildren(); $("focus-label").textContent=state.result.slices?.predicted?.length ? "Alle Targets · auswählen zum Fokussieren in 2D + 3D" : "Alle Targets · auswählen zum Fokussieren in 3D";
  (state.result.targets3d || []).forEach((target,index) => {
    const button=el("button",undefined,"target-chip"); button.type="button"; button.dataset.target=target.number; button.setAttribute("aria-pressed","false");
    const dot=el("i"); dot.style.background=colors[index%colors.length]; button.append(dot,el("span",target.name || `Target ${target.number}`)); button.append(el("small",`${validRx(Number(target.prescription_Gy)) ? `${fmt(target.prescription_Gy)} Gy · ` : ""}z ${fmt(target.center_lps_mm?.[2])} mm`)); button.addEventListener("click",()=>focusTarget(target)); $("target-list").append(button);
  });
  if (!state.result.targets3d?.length) $("target-list").append(el("p","Für diese Antwort ist keine räumliche Targetgeometrie verfügbar.","hint"));
}
function focusTarget(target) {
  if (state.focusTargetNumber === Number(target.number)) { resetTargetFocus(); return; }
  state.focusTargetNumber = Number(target.number);
  const curveIndex=(state.result.dvhs || []).findIndex(curve => Number(curve.roi_number ?? curve.number)===Number(target.number) || curve.roi===target.name);
  if (curveIndex >= 0) {state.dvhSelection=String(curveIndex);$('dvh-select').value=state.dvhSelection;renderLegend();drawDVH();}
  if (!target.center_lps_mm?.every(Number.isFinite)) return;
  state.focus=target.center_lps_mm.slice(); state.orbit.center=state.focus.slice();
  const z=state.result.slices?.z_mm || []; if (z.length) state.slice=z.reduce((best,value,index)=>Math.abs(value-state.focus[2])<Math.abs(z[best]-state.focus[2]) ? index : best,0);
  const [sx,sy]=spacingXY(), values=state.result.slices?.predicted?.[0], span=values ? Math.max(values[0].length*sx,values.length*sy) : sceneGeometry().span, radius=Number(target.radius_mm)||5;
  state.zoom=Math.max(1,Math.min(8,span/Math.max(35,radius*7))); state.orbit.zoom=Math.max(1.4,Math.min(5,sceneGeometry().span/Math.max(35,radius*8)));
  $("focus-label").textContent=`Fokus: ${target.name || `Target ${target.number}`} · ${values ? "nächste dargestellte Schicht" : "3D-Targetgeometrie"}`;
  $("target-list").querySelectorAll("button").forEach(button=>button.setAttribute("aria-pressed",String(Number(button.dataset.target)===Number(target.number)))); drawSlice(); drawSpatial();
}
function sceneGeometry() {
  const targets=state.result?.targets3d || [], points=targets.flatMap(t=>[t.bounds_min_lps_mm || t.center_lps_mm,t.bounds_max_lps_mm || t.center_lps_mm]).filter(p=>p?.every(Number.isFinite));
  if (!points.length) return {center:[0,0,0],span:100,min:[-50,-50,-50],max:[50,50,50]};
  const min=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),max=[0,1,2].map(i=>Math.max(...points.map(p=>p[i]))); return {min,max,center:min.map((v,i)=>(v+max[i])/2),span:Math.max(...max.map((v,i)=>v-min[i]),30)};
}
function headEnabled() { return $('head-toggle').checked; }
function syntheticHead() {
  const g=sceneGeometry();
  return {center:g.center,radii:[Math.max(85,(g.max[0]-g.min[0])/2+20),Math.max(105,(g.max[1]-g.min[1])/2+20),Math.max(115,(g.max[2]-g.min[2])/2+20)]};
}
['head-toggle','head-toggle-3d'].forEach(id=>$(id).addEventListener('change',event=>{
  ['head-toggle','head-toggle-3d'].forEach(other=>$(other).checked=event.target.checked);drawSlice();drawSpatial();
}));
function drawSpatial() {
  if (!state.result) return;
  const {context,width,height}=setupCanvas($("spatial-canvas")); if (!width || !height) return;
  context.fillStyle="#14243e"; context.fillRect(0,0,width,height);
  const geometry=sceneGeometry(), center=state.orbit.center || geometry.center, scale=Math.min(width,height)*(headEnabled()?.82:.62)/(headEnabled()?Math.max(...syntheticHead().radii)*2.25:geometry.span)*state.orbit.zoom, cy=Math.cos(state.orbit.yaw),sy=Math.sin(state.orbit.yaw),cp=Math.cos(state.orbit.pitch),sp=Math.sin(state.orbit.pitch);
  const project=p=>{const a=p.map((v,i)=>v-center[i]),x=a[0]*cy+a[2]*sy,z=-a[0]*sy+a[2]*cy,y=a[1]*cp-z*sp;return [width/2+x*scale,height/2+y*scale,a[1]*sp+z*cp];};
  if(headEnabled()) {
    const head=syntheticHead(),c=head.center,r=head.radii;
    const line=points=>{context.beginPath();points.map(project).forEach((p,i)=>i?context.lineTo(p[0],p[1]):context.moveTo(p[0],p[1]));context.stroke();};
    context.save();context.strokeStyle='#9bb9e64d';context.lineWidth=1;
    for(let lat=-60;lat<=60;lat+=30){const phi=lat*Math.PI/180;line(Array.from({length:73},(_,i)=>{const a=i*Math.PI/36;return[c[0]+r[0]*Math.cos(phi)*Math.cos(a),c[1]+r[1]*Math.cos(phi)*Math.sin(a),c[2]+r[2]*Math.sin(phi)]}));}
    for(let lon=0;lon<180;lon+=30){const a=lon*Math.PI/180;line(Array.from({length:73},(_,i)=>{const p=i*Math.PI/36;return[c[0]+r[0]*Math.cos(p)*Math.cos(a),c[1]+r[1]*Math.cos(p)*Math.sin(a),c[2]+r[2]*Math.sin(p)]}));}
    context.strokeStyle='#b1caf0';line([[c[0]-8,c[1]-r[1],c[2]],[c[0],c[1]-r[1]-18,c[2]-10],[c[0]+8,c[1]-r[1],c[2]]]);
    for(const side of [-1,1])line(Array.from({length:37},(_,i)=>{const a=i*Math.PI/18;return[c[0]+side*(r[0]+5*Math.cos(a)),c[1],c[2]+15*Math.sin(a)]}));
    context.restore();context.fillStyle='#c2d6f3';context.font='10px Segoe UI';context.fillText(tr('Synthetischer Kopf · Lage aus Targets geschätzt · keine Anatomie'),15,height-32);
  }
  // Orthographic LPS box and current displayed axial plane.
  const min=geometry.min.map(v=>v-8),max=geometry.max.map(v=>v+8),corners=[]; for(let i=0;i<8;i++) corners.push([i&1?max[0]:min[0],i&2?max[1]:min[1],i&4?max[2]:min[2]]);
  context.strokeStyle="#6b98a12d";context.lineWidth=1;context.beginPath(); corners.forEach((p,i)=>{[1,2,4].forEach(bit=>{if(i&bit)return;const a=project(p),b=project(corners[i|bit]);context.moveTo(a[0],a[1]);context.lineTo(b[0],b[1]);});});context.stroke();
  const z=state.result.slices?.z_mm?.[state.slice];
  if (Number.isFinite(z)) { const plane=[[min[0],min[1],z],[max[0],min[1],z],[max[0],max[1],z],[min[0],max[1],z]].map(project); context.fillStyle="#75bcff16";context.strokeStyle="#75bcff6b";context.beginPath();plane.forEach((p,i)=>i?context.lineTo(p[0],p[1]):context.moveTo(p[0],p[1]));context.closePath();context.fill();context.stroke(); }
  const targets=(state.result.targets3d || []).map((target,index)=>({target,index,position:project(target.center_lps_mm)})).sort((a,b)=>a.position[2]-b.position[2]);
  targets.forEach(({target,index,position})=>{
    const color=colors[index%colors.length],radius=Math.max(3,(Number(target.radius_mm)||1)*scale),selected=state.focus && target.center_lps_mm.every((v,i)=>v===state.focus[i]);
    context.fillStyle=color+"20";context.strokeStyle=color+"60";context.lineWidth=selected?2:1;
    // True contours below; translucent sphere indicates equivalent target radius only.
    context.beginPath();context.arc(position[0],position[1],radius,0,Math.PI*2);context.fill();context.stroke();
    context.strokeStyle=color+(selected?"f0":"a0");context.lineWidth=selected?1.5:.8;context.beginPath();
    (target.contours || []).forEach(contour=>{let started=false;contour.forEach(p=>{if(!p?.every(Number.isFinite))return;const q=project(p);if(started)context.lineTo(q[0],q[1]);else {context.moveTo(q[0],q[1]);started=true;}});if(started)context.closePath();});context.stroke();
    context.fillStyle=color;context.beginPath();context.arc(position[0],position[1],2.5,0,Math.PI*2);context.fill();
    context.font=selected?"600 11px Segoe UI":"10px Segoe UI";context.fillStyle=selected?"#fff":color;context.fillText(target.name || `Target ${target.number}`,position[0]+radius+5,position[1]+3);
  });
  const anchor=[40,height-42],length=23;context.font="9px Segoe UI";
  [[1,0,0,"L"],[0,1,0,"P"],[0,0,1,"S"]].forEach(axis=>{const p=project(center.map((v,i)=>v+axis[i]*length/scale)),base=project(center);context.strokeStyle="#b8c7c8";context.beginPath();context.moveTo(...anchor);context.lineTo(anchor[0]+p[0]-base[0],anchor[1]+p[1]-base[1]);context.stroke();context.fillStyle="#b8c7c8";context.fillText(axis[3],anchor[0]+p[0]-base[0]+3,anchor[1]+p[1]-base[1]);});
  context.fillStyle="#8fa9ae";context.font="9px Segoe UI";context.textAlign="right";context.fillText(tr("Konturen + äquivalente Radiusmarker · LPS"),width-12,height-15);
}
function setSlice(index) { if (!state.result?.slices?.predicted?.length) return; state.slice=Math.max(0,Math.min(state.result.slices.predicted.length-1,index));drawSlice();drawSpatial(); }
function zoom2d(factor) { if (!state.result?.slices?.predicted?.length)return;state.zoom=Math.max(.5,Math.min(12,state.zoom*factor));drawSlice(); }
function zoom3d(factor) { if (!state.result)return;state.orbit.zoom=Math.max(.4,Math.min(12,state.orbit.zoom*factor));drawSpatial(); }
["heatmap-toggle","isodose-toggle","background-toggle"].forEach(id=>$(id).addEventListener("change",drawSlice));
$("zoom-in").addEventListener("click",()=>zoom2d(1.25));$("zoom-out").addEventListener("click",()=>zoom2d(.8));
$("spatial-in").addEventListener("click",()=>zoom3d(1.25));$("spatial-out").addEventListener("click",()=>zoom3d(.8));
function resetTargetFocus() {
  state.focus=null; state.focusTargetNumber=null; state.zoom=1; state.orbit={yaw:-.45,pitch:.3,zoom:1,center:null};
  if(!state.result) return;
  state.dvhSelection=(state.result.dvhs || []).some(isTargetCurve) ? 'all-targets' : '0'; $('dvh-select').value=state.dvhSelection;
  // Keep target buttons in place so keyboard focus is preserved.
  $('target-list').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed','false'));
  $('focus-label').textContent=state.result.slices?.predicted?.length ? 'Alle Targets · auswählen zum Fokussieren in 2D + 3D' : 'Alle Targets · auswählen zum Fokussieren in 3D';
  renderLegend(); drawSlice(); drawSpatial(); drawDVH();
}
$('reset-view').addEventListener('click',resetTargetFocus);
$('show-all-targets').addEventListener('click',resetTargetFocus);
$("slice-canvas").addEventListener("wheel",event=>{if(!state.result)return;event.preventDefault();if(event.ctrlKey)zoom2d(event.deltaY<0?1.12:1/1.12);else setSlice(state.slice+(event.deltaY>0?1:-1));},{passive:false});
$("slice-canvas").tabIndex=0;
$("spatial-canvas").tabIndex=0;$("spatial-canvas").addEventListener("keydown",event=>{if(!state.result)return;if(["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key)){event.preventDefault();state.orbit.yaw+=(event.key==="ArrowLeft"?-.12:event.key==="ArrowRight"?.12:0);state.orbit.pitch=Math.max(-1.5,Math.min(1.5,state.orbit.pitch+(event.key==="ArrowUp"?-.12:event.key==="ArrowDown"?.12:0)));drawSpatial();}});$("slice-canvas").addEventListener("keydown",event=>{if(["ArrowUp","ArrowDown"].includes(event.key)){event.preventDefault();setSlice(state.slice+(event.key==="ArrowUp"?1:-1));}});
$("spatial-canvas").addEventListener("wheel",event=>{if(event.shiftKey){event.preventDefault();setSlice(state.slice+(event.deltaY>0?1:-1));return;}if(!state.result)return;event.preventDefault();zoom3d(event.deltaY<0?1.12:1/1.12);},{passive:false});
let orbitDrag=null;
$("spatial-canvas").addEventListener("pointerdown",event=>{if(!state.result)return;orbitDrag=[event.clientX,event.clientY];event.currentTarget.setPointerCapture(event.pointerId);});
$("spatial-canvas").addEventListener("pointermove",event=>{if(!orbitDrag)return;state.orbit.yaw+=(event.clientX-orbitDrag[0])*.009;state.orbit.pitch=Math.max(-1.5,Math.min(1.5,state.orbit.pitch+(event.clientY-orbitDrag[1])*.009));orbitDrag=[event.clientX,event.clientY];drawSpatial();});
["pointerup","pointercancel","lostpointercapture"].forEach(name=>$("spatial-canvas").addEventListener(name,()=>{orbitDrag=null;}));
function isTargetCurve(curve) {
  return (state.result?.targets3d || []).some(target => Number(target.number) === Number(curve.roi_number ?? curve.number) || target.name === curve.roi) || (state.result?.metrics || []).some(metric => metric.roi === curve.roi && /target|ptv|gtv/i.test(metric.role || ''));
}
function visibleDVHs() {
  const curves=state.result?.dvhs || [];
  return state.dvhSelection === 'all-targets' ? curves.filter(isTargetCurve) : curves.filter((curve,index)=>String(index)===state.dvhSelection);
}
function renderDVHSelector() {
  const selector=$('dvh-select'), curves=state.result?.dvhs || []; selector.replaceChildren();
  state.dvhSelection=curves.some(isTargetCurve) ? 'all-targets' : '0';
  curves.forEach((curve,index)=>{const option=el('option',curve.roi);option.value=String(index);selector.append(option);});
  if (curves.some(isTargetCurve)) {const option=el('option','Alle Targets');option.value='all-targets';selector.append(option);}
  selector.value=state.dvhSelection; selector.disabled=!curves.length;
}
$('dvh-select').addEventListener('change',()=>{state.dvhSelection=$('dvh-select').value;renderLegend();drawDVH();});
function drawDVH() {
  if (!state.result) return;
  const {context, width, height} = setupCanvas($("dvh-canvas")); const margin = {left: 45, right: 22, top: 15, bottom: 39}; const plotWidth = width - margin.left - margin.right, plotHeight = height - margin.top - margin.bottom;
  const curves = visibleDVHs(); const maxDose = 1.03 * curves.reduce((maximum,curve)=>["predicted","reference"].reduce((current,kind)=>(curve[kind] || []).reduce((value,point)=>Number.isFinite(point[0]) ? Math.max(value,point[0]) : value,current),maximum),Math.max(1,...curves.map(curve=>Number(curve.prescription_Gy) || 0)));
  const x = value => margin.left + value / maxDose * plotWidth, y = value => margin.top + (100 - value) / 100 * plotHeight;
  context.clearRect(0, 0, width, height); context.font = "10px Segoe UI, sans-serif"; context.lineWidth = 1;
  for (let value = 0; value <= 100; value += 25) { context.strokeStyle = document.documentElement.dataset.theme === "dark" ? "#354851" : "#e6e9df"; context.beginPath(); context.moveTo(margin.left, y(value)); context.lineTo(width - margin.right, y(value)); context.stroke(); context.fillStyle = document.documentElement.dataset.theme === "dark" ? "#b0c1c6" : "#6a7b7e"; context.textAlign = "right"; context.fillText(String(value), margin.left - 10, y(value) + 3); }
  for (let tick = 0; tick <= 5; tick++) { const dose = maxDose * tick / 5; context.fillStyle = document.documentElement.dataset.theme === "dark" ? "#b0c1c6" : "#6a7b7e"; context.textAlign = "center"; context.fillText(fmt(dose, 0), x(dose), height - 19); }
  context.textAlign = "right"; context.fillText(tr("Dosis / Gy"), width - margin.right, height - 3);
  context.save(); context.beginPath(); context.rect(margin.left, margin.top, plotWidth, plotHeight); context.clip();
  const selected = curves.length === 1 ? curves[0] : null;
  const rx = selected ? Number(Object.hasOwn(selected, "prescription_Gy") ? selected.prescription_Gy : state.result.summary?.prescription_Gy) : NaN;
  $("dvh-rx-label").textContent = validRx(rx) ? `Rx: ${fmt(rx)} Gy · ${selected.roi}` : selected ? "Keine Targetverschreibung für diese Struktur" : "Alle Targets · individuelle Verschreibungen";
  if (rx > 0) { context.strokeStyle = "#b5c3b5"; context.setLineDash([3,4]); context.beginPath(); context.moveTo(x(rx), margin.top); context.lineTo(x(rx), margin.top + plotHeight); context.stroke(); }
  curves.forEach((curve, index) => { ["predicted", "reference"].forEach(kind => { if (!curve[kind]?.length) return; context.strokeStyle = comparisonColor(kind); context.lineWidth = kind === "predicted" ? 2 : 1.7; context.setLineDash(kind === "reference" ? [5, 4] : []); context.beginPath(); let started = false, previousVolume; curve[kind].forEach(point => { if (!point.every(Number.isFinite)) return; if (!started) { context.moveTo(x(point[0]), y(point[1])); started = true; } else { context.lineTo(x(point[0]), y(previousVolume)); context.lineTo(x(point[0]), y(point[1])); } previousVolume=point[1]; }); context.stroke(); }); });
  context.restore(); context.setLineDash([]);
}
function renderLegend() {
  $('dvh-legend').replaceChildren();
  visibleDVHs().forEach(curve => {
    const index=(state.result.dvhs || []).indexOf(curve);
    ['predicted','reference'].forEach(kind => {
      if (!curve[kind]?.length) return;
      const item=el('span',undefined,'legend-item'), swatch=el('i');
      swatch.style.borderTop=`${kind === 'predicted' ? 3 : 2}px ${kind === 'predicted' ? 'solid' : 'dashed'} ${comparisonColor(kind)}`;
      swatch.style.background='none'; item.append(swatch,el('span',`${curve.roi} · ${kind === 'predicted' ? tr('Vorhersage') : tr('Referenz')}`)); $('dvh-legend').append(item);
    });
  });
}
function renderMetrics() {
  const body = $("metrics-body"); body.replaceChildren();
  (state.result.metrics || []).forEach(metric => { const row = el("tr"), name = el("td", metric.roi); name.append(el("small", `${metric.role || "Struktur"}${validRx(Number(metric.prescription_Gy)) ? ` · Rx ${fmt(metric.prescription_Gy)} Gy` : ""}`)); row.append(name, el("td", fmt(metric.volume_cc, 2)));
    ["D98_Gy", "D95_Gy", "Dmean_Gy", "D2_Gy", "V100_pct"].forEach(key => { const cell = el("td"); cell.append(comparisonValues(metric.predicted?.[key], metric.reference ? metric.reference[key] : undefined)); row.append(cell); }); body.append(row);
  });
}
async function loadModel() {
  const container = $("model-evidence");
  try { const model = await request("/api/model"); container.replaceChildren(); const list = el("dl");
    if (model.prescription_range_Gy?.length === 2) { $("prescription").placeholder = `${fmt(model.prescription_range_Gy[0], 0)}–${fmt(model.prescription_range_Gy[1], 0)}`; $("prescription").title = `Beobachteter Modellbereich: ${model.prescription_range_Gy.join("–")} Gy. Andere Verschreibungen sind unvalidierte Extrapolation.`; $("rx-range").textContent = `Beobachteter Modellbereich: ${model.prescription_range_Gy.map(value => fmt(value, 0)).join("–")} Gy. Andere Verschreibungen sind unvalidierte Extrapolation.`; }
    [["Modell", model.name], ["Status", model.status], ["Trainingsfälle", model.training_cases], ["Verschriebene Targets im Training", model.training_targets], ["Beobachteter Verschreibungsbereich", model.prescription_range_Gy ? `${model.prescription_range_Gy.map(value => fmt(value, 0)).join("–")} Gy in einer Fraktion` : null], ["Validierung", model.validation], ["Grenzen", model.limitations]].forEach(([label, value]) => { if (value === undefined || value === null) return; list.append(el("dt", label === "Validierung" && model.spatial_regularization ? "Validierung des Basismodells" : label)); const detail = el("dd"); if (label === "Validierung" && typeof value === "object" && !Array.isArray(value)) renderValidation(detail, value); else if (Array.isArray(value)) { const items = el("ul"); value.forEach(item => items.append(el("li", typeof item === "string" ? item : JSON.stringify(item)))); detail.append(items); } else if (typeof value === "object") detail.append(el("pre", JSON.stringify(value, null, 2))); else detail.textContent = String(value); list.append(detail); });
    if(model.dose_calibration){
      list.append(el('dt','Empirische Dosiskalibrierung'));
      const detail=el('dd');detail.append(el('p',tr('Kalibrierung passt das Dosisniveau anhand der Testserie an; Verschreibung bleibt gleich. Bisherige Vorhersage zum Vergleich wählbar. Keine externe Validierung.')));
      detail.append(el('p',`× ${fmt(model.dose_calibration.scale,2)}`));
      const table=el('table'),head=el('tr');['Kennwert','Bisher','Kalibriert','Referenz'].forEach(x=>head.append(el('th',x)));table.append(head);
      const a=model.dose_calibration.validation;
      for(const [label,key] of [['Mittelwert 1/CI','inverse_CI'],['Mittelwert GI','GI'],['D98-MAE / Gy','D98_MAE_Gy'],['Lokales V12-MAE / cm³','V12_MAE_cc']]){
        const row=el('tr');row.append(el('td',label),el('td',fmt(a.before[key],3)),el('td',fmt(a.after[key],3)),el('td',fmt(a.reference[key],3)));table.append(row);
      }detail.append(table);list.append(detail);
    }
    if (model.spatial_regularization) {
      list.append(el('dt','Räumliche Regularisierung'));
      const detail=el('dd');
      detail.append(el('p','Räumliche Regularisierung reduziert Stufen, verändert die Dosis; keine zusätzliche klinische Validierung. Basismodell zum Vergleich wählbar.'));
      detail.append(el('p','Die Validierung des Basismodells beschreibt das Modell vor der räumlichen Regularisierung.'));
      const regularization=model.spatial_regularization, aggregate=regularization.aggregate;
      if (aggregate) {
        detail.append(el('p','Explorativer Vergleich auf derselben Entwicklungskohorte; die Regularisierung wurde nach Sichtprüfung gewählt. Kein unabhängiger Validierungsnachweis.'));
        const table=el('table',undefined,'validation-table'),head=el('thead'),heading=el('tr');
        ['Kennwert','Basismodell','Regularisiert'].forEach(label=>heading.append(el('th',label)));head.append(heading);table.append(head);
        const body=el('tbody');
        [['Target-Voxel-MAE / Gy',aggregate.bands?.target?.raw_MAE_Gy,aggregate.bands?.target?.regularized_MAE_Gy],['Target-D98-MAE / Gy',aggregate.raw_D98_case_macro_MAE_Gy,aggregate.regularized_D98_case_macro_MAE_Gy],['V12-MAE außerhalb Targets / cm³',aggregate.raw_V12_outside_case_MAE_cc,aggregate.regularized_V12_outside_case_MAE_cc]].forEach(([label,raw,regularized])=>{
          if(!Number.isFinite(raw)||!Number.isFinite(regularized))return;
          const row=el('tr');row.append(el('td',label),el('td',fmt(raw,2)),el('td',fmt(regularized,2)));body.append(row);
        });table.append(body);detail.append(table);
        detail.append(el('p','Kleinere Fehlerwerte sind besser. Verbesserte D98 kann mit schlechterer voxelweiser Übereinstimmung einhergehen.'));
      }

      list.append(detail);
    }
    updateButton(); container.append(list); if (!list.children.length) container.append(el("p", "Der Server stellt derzeit keine Modellkarte bereit."));
    if (model.resolution_check) { const r = model.resolution_check; container.append(el("p", `Rasterprüfung an ${r.targets} kleinen Zielen (1 → 0,5 mm): mediane absolute D98-Änderung ${fmt(r.median_abs_D98_change_Gy, 2)} Gy, maximal ${fmt(r.max_abs_D98_change_Gy, 2)} Gy. Gleiche Konturinterpretation; kein Nachweis nativer TPS-Gleichheit.`, "variability-note")); }
  } catch (error) { container.replaceChildren(el("p", `Modellkarte nicht verfügbar: ${errorMessage(error)}`)); }
}
function renderValidation(container, validation) {
  container.append(el("p", "Explorative interne Validierung mit patientenweise getrennten Folds. Kein unabhängiger klinischer Validierungsnachweis."));
  if (validation.case_macro_MAE) {
    const labels = {target: "Innerhalb der Targets", "0_5mm": "0–5 mm außerhalb", "5_15mm": "5–15 mm außerhalb", "15_35mm": "15–35 mm außerhalb"};
    const table = el("table", undefined, "validation-table"); const head = el("thead"); const heading = el("tr"); ["Region", "MAE / Gy", "Baseline / Gy"].forEach(label => heading.append(el("th", label))); head.append(heading); table.append(head); const body = el("tbody");
    Object.entries(validation.case_macro_MAE).forEach(([region, values]) => { const row = el("tr"); row.append(el("td", labels[region] || region), el("td", fmt(values.MAE_Gy, 2)), el("td", fmt(values.baseline_MAE_Gy, 2))); body.append(row); }); table.append(body); container.append(table);
    container.append(el("p", "MAE: mittlerer absoluter Fehler, über Fälle gleich gewichtet. Baseline: ausschließlich auf den Trainingsfällen gelernter monotoner Dosis-Abstands-Verlauf.", "hint"));
  }
  if (validation.target_D98_case_macro_MAE_Gy !== undefined) container.append(el("p", `Target-D98: mittlerer absoluter Fehler ${fmt(validation.target_D98_case_macro_MAE_Gy, 2)} Gy.`));
  if (validation.V12_outside_targets_domain_case_MAE_cc !== undefined) container.append(el("p", `V12 außerhalb Targets: mittlerer absoluter Fehler ${fmt(validation.V12_outside_targets_domain_case_MAE_cc, 2)} cm³ (begrenzt auf die ausgewertete Region).`));
  if (Array.isArray(validation.folds)) {
    container.append(el("p", `${validation.folds.length} patientenweise getrennte Validierungsfolds.`, "hint"));
    const v12Errors = validation.folds.map(fold => { const a = fold.predicted?.V12_outside_targets_domain_cc, b = fold.reference?.V12_outside_targets_domain_cc; return Number.isFinite(a) && Number.isFinite(b) ? Math.abs(a - b) : NaN; }).filter(Number.isFinite);
    if (v12Errors.length) container.append(el("p", `Fallweise absolute V12-Fehler: ${fmt(Math.min(...v12Errors), 2)}–${fmt(Math.max(...v12Errors), 2)} cm³. Der Mittelwert verdeckt erhebliche Unterschiede zwischen Fällen; diese Werte sind keine individuellen Unsicherheitsintervalle.`, "variability-note"));
  }
  if (!validation.case_macro_MAE) { const compact = {...validation}; delete compact.folds; container.append(el("pre", JSON.stringify(compact, null, 2))); }
}
let resizeFrame;
window.addEventListener("resize", () => { cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(() => { drawSlice(); drawSpatial(); drawDVH(); }); });
updateButton();
loadSession();
loadModel();
loadTestCases();


$("activation-form").addEventListener("submit", async event => {
  event.preventDefault(); if (state.authBusy || state.busy) return;
  let invitation = activation;
  if (!invitation) {
    try {
      const url = new URL($('activation-code').value.trim());
      if (url.origin !== location.origin) throw new Error();
      const values = new URLSearchParams(url.hash.slice(1));
      if (!['invite','recovery'].includes(values.get('type')) || !/^[a-zA-Z0-9]{20,512}$/.test(values.get('token_hash') || '')) throw new Error();
      invitation = {token_hash:values.get('token_hash'),type:values.get('type')};
    } catch { $('auth-message').textContent = 'Bitte den vollständigen persönlichen Einladungslink eingeben.'; return; }
  }
  if ($('activation-password').value !== $('activation-confirm').value) { $('auth-message').textContent = 'Die Passwörter stimmen nicht überein.'; return; }
  state.authBusy = true; $('activation-button').disabled = true; updateButton();
  try {
    await request('/api/auth/activate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...invitation, email: $('activation-email').value.trim(), password: $('activation-password').value})});
    activation = null; registration = false; $('activation-code').value = ''; activationParams.delete('token_hash'); $('activation-form').hidden = true; resetUploadState();
    await loadSession(); $('auth-message').textContent = 'Passwort gespeichert. Ihr Kontostatus wurde aktualisiert.';
  } catch (error) { $('auth-message').textContent = errorMessage(error); }
  finally { $('activation-password').value = ''; $('activation-confirm').value = ''; state.authBusy = false; $('activation-button').disabled = false; updateButton(); }
});
window.addEventListener('dose-preferences', () => { updateTargetInputSummary(); drawSlice(); drawSpatial(); if(state.result) {renderLegend();renderDVHMethodNote();renderPlanMetrics(state.result.summary);} drawDVH(); });

$('original-export-toggle').addEventListener('change', () => { $('original-export-note').hidden = !$('original-export-toggle').checked; $('original-export-button').hidden = !$('original-export-toggle').checked; });
$('original-export-button').addEventListener('click', async () => {
  if (!$('original-export-toggle').checked || !preparedUpload?.context || state.busy) return;
  const item = (state.result?.downloads || []).find(item => /(?:^|\/)prediction\.dcm(?:[?#]|$)/.test(item.url || ''));
  if (!item) return;
  const url = new URL(item.url, location.href);
  if (url.origin !== location.origin) { showError('Originalexport benötigt einen Download vom selben Server.'); return; }
  const generation = state.generation, context = preparedUpload.context;
  $('original-export-button').disabled = true; $('original-export-note').textContent = 'Originalexport wird im Browser erstellt …';
  try {
    const response = await fetch(url.href, {credentials:'same-origin'});
    if (!response.ok) throw new Error('Download nicht verfügbar oder abgelaufen. Bitte erneut berechnen.');
    const blob = await window.DosePrivacy.restoreOriginalCase(await response.blob(), context);
    if (generation !== state.generation || preparedUpload?.context !== context) return;
    window.DosePrivacy.download(blob, `DoseAtlas_prediction_original_case_${new Date().toISOString().replace(/[-:]/g,'').replace(/\.\d+Z$/,'Z')}.dcm`);
    $('original-export-note').textContent = 'Originalexport erstellt. Vor Import im ursprünglichen Fall prüfen.';
  } catch(error) { showError(errorMessage(error)); }
  finally { $('original-export-button').disabled = false; }
});
window.addEventListener('pagehide', () => { pendingInspection = null; inspectionController?.abort(); state.generation++; state.controller?.abort(); state.controller = null; discardPreparedUpload(); busy(false); });

$('register-button').addEventListener('click', () => {
  registration = true; resetUploadState(); $('auth-message').textContent = '';
  $('activation-email').value = $('login-email').value.trim(); renderSession(); $('activation-email').focus();
});
$('activation-cancel').addEventListener('click', () => {
  if (state.authBusy) return;
  activation = null; registration = false; activationParams.delete('token_hash');
  $('activation-password').value = ''; $('activation-confirm').value = ''; $('activation-code').value = '';
  renderSession(); $('auth-message').textContent = '';
});

// Move the live controls with their canvas: listeners and selections remain intact.
let expandedView = null;
function closeExpandedView() {
  if (!expandedView) return;
  const {moves, trigger}=expandedView; expandedView=null;
  moves.forEach(({node,marker})=>marker.replaceWith(node));
  $('view-dialog').close(); document.body.classList.remove('view-expanded');
  if(trigger?.isConnected) trigger.focus();
  scheduleViewerResize();
}
function openExpandedView(kind,trigger) {
  closeExpandedView();
  const selectors=kind==='2d' ? ['.viewer-card>.viewer-header','.plan-controls','#difference-controls','.dose-scale','.slice-stage','#isodose-levels','.target-navigator','.slice-tools','#view-note','#view-grid-note'] : kind==='3d' ? ['.spatial-stage','.target-navigator'] : ['.dvh-card'];
  const moves=selectors.map(selector=>{const node=document.querySelector(selector),marker=document.createComment('expanded-view-slot');node.before(marker);$('view-dialog-content').append(node);return {node,marker};});
  expandedView={moves,trigger}; $('view-dialog').dataset.view=kind;
  $('view-dialog-title').textContent=kind==='2d' ? '2D · Axiale Dosisverteilung' : kind==='3d' ? '3D · Targetgeometrie' : 'Dosis-Volumen-Histogramm';
  document.body.classList.add('view-expanded'); $('view-dialog').showModal(); $('close-view').focus(); scheduleViewerResize();
}
document.querySelectorAll('[data-expand-view]').forEach(button=>button.addEventListener('click',()=>openExpandedView(button.dataset.expandView,button)));
$('close-view').addEventListener('click',closeExpandedView);
$('view-dialog').addEventListener('cancel',event=>{event.preventDefault();closeExpandedView();});
function scheduleViewerResize() {cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{drawSlice();drawSpatial();drawDVH();});}
const viewerResizeObserver=new ResizeObserver(scheduleViewerResize);
['slice-canvas','spatial-canvas','dvh-canvas'].forEach(id=>viewerResizeObserver.observe($(id)));

window.DoseComparisons?.configure({request,busy,
  canAdd:()=>canUpload() && state.uploadReady && !inspectionReadOnly && Boolean($('structure-file').files[0]),
  snapshot:()=>({structure:$('structure-file').files[0],targets:selectedTargets(),targetPrescriptions:selectedPrescriptionMap(),prescription:validRx(Number($('prescription').value))?Number($('prescription').value):effectiveTargetRx(selectedTargets()[0]),sigma:$('prediction-variant').value==='0'?0:1,calibrated:$('prediction-variant').value==='calibrated'}),
  activate:data=>renderResult(data,Boolean(data.summary?.synthetic_demo || data.demo))
});

$('metrics-excel-button').addEventListener('click', async()=>{
  if(state.busy || !state.result?.metrics?.length)return;
  $('metrics-excel-button').disabled=true;
  try {
    const comparisons=window.DoseComparisons?.getExportComparisons() || [];
    await window.DoseMetricsExport.download(comparisons.length?comparisons:[{label:'Basisanalyse',data:state.result}]);
    $('metrics-excel-note').textContent=tr('Excel-Datei lokal erstellt. Leere Zellen bedeuten nicht verfügbar, nicht null Gy.');
  } catch {showError(tr('Excel-Export konnte nicht erstellt werden.'));}
  finally{$('metrics-excel-button').disabled=state.busy || !state.result?.metrics?.length;}
});

window.addEventListener('dose-comparisons-change', updateButton);

$('difference-range').addEventListener('change',drawSlice);
$('slice-canvas').addEventListener('pointermove',event=>{
  if(state.mode!=='difference' || !state.sliceTransform || !state.result?.slices)return;
  const box=$('slice-canvas').getBoundingClientRect(),t=state.sliceTransform;
  const x=event.clientX-box.left,y=event.clientY-box.top;
  const col=Math.floor((x-t.left)/t.pw+.5),row=Math.floor((y-t.top)/t.ph+.5);
  const p=state.result.slices.predicted?.[state.slice]?.[row]?.[col],r=state.result.slices.reference?.[state.slice]?.[row]?.[col];
  if(x<24 || x>t.width-24 || y<30 || y>t.height-25 || col<0 || col>=t.columns || row<0 || row>=t.rows || !Number.isFinite(p) || !Number.isFinite(r)){$('difference-probe').textContent=tr('Keine gemeinsame Dosisabdeckung an dieser Position.');return;}
  $('difference-probe').textContent=`${tr('Anzeigevoxel')}: ${tr('Vorhersage')} ${fmt(p,2)} Gy · ${tr('Referenz')} ${fmt(r,2)} Gy · Δ ${p-r>0?'+':''}${fmt(p-r,2)} Gy`;
});
$('slice-canvas').addEventListener('pointerleave',()=>{$('difference-probe').textContent=tr('Maus über das Dosisbild bewegen.');});
