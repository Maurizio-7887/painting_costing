/* ENOROSSI Paint Optimizer — main.js */

// ── CLOCK ──
function updateClock() {
  const el = document.getElementById('clock');
  if (el) {
    const now = new Date();
    el.textContent = now.toLocaleTimeString('it-IT', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  }
}
setInterval(updateClock, 1000);
updateClock();

// ── SIDEBAR TOGGLE ──
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

// ── UPLOAD 3D ──
async function upload3D(codice) {
  const fileInput = document.getElementById('file3d_' + codice);
  const btn = document.getElementById('btn3d_' + codice);
  const resultDiv = document.getElementById('result3d_' + codice);
  if (!fileInput || !fileInput.files[0]) {
    alert('Seleziona un file 3D prima.');
    return;
  }
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Analisi in corso...';
  const formData = new FormData();
  formData.append('file_3d', fileInput.files[0]);
  formData.append('codice', codice);
  try {
    const r = await fetch('/api/analizza_3d', { method: 'POST', body: formData });
    const d = await r.json();
    if (d.ok) {
      resultDiv.innerHTML = `
        <div class="alert alert-success py-2 mt-2 small">
          <strong>✅ Analisi completata:</strong>
          ${d.lunghezza_mm}×${d.larghezza_mm}×${d.altezza_mm}mm ·
          Sup: <strong>${d.superficie_m2}m²</strong> ·
          Vol: ${d.volume_cm3}cm³ ·
          Peso: ${d.peso_kg}kg ·
          Passo: ${d.passo_gancio_m}m
          <br><a href="javascript:location.reload()" class="btn btn-sm btn-success mt-1">↻ Aggiorna pagina</a>
        </div>`;
    } else {
      resultDiv.innerHTML = `<div class="alert alert-warning py-2 mt-2 small">⚠️ ${d.error}</div>`;
    }
  } catch (e) {
    resultDiv.innerHTML = `<div class="alert alert-danger py-2 mt-2 small">❌ Errore: ${e}</div>`;
  }
  btn.disabled = false;
  btn.innerHTML = '<i class="bi bi-cpu me-1"></i>Analizza con Trimesh';
}

// ── CHART.JS DEFAULT ──
if (typeof Chart !== 'undefined') {
  Chart.defaults.color = '#7a9a7e';
  Chart.defaults.borderColor = 'rgba(28,110,46,.2)';
  Chart.defaults.backgroundColor = 'rgba(28,110,46,.1)';
}
