"""The single-page upload UI — self-contained HTML, no build step."""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCORM AI Analyzer</title>
<style>
:root { --navy:#003087; --navy2:#1a4fa3; --red:#c8102e; --bg:#f2f4f8; }
* { box-sizing:border-box; }
body { font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif; margin:0;
       background:var(--bg); color:#23272d; }
header { background:linear-gradient(135deg,var(--navy),var(--navy2) 70%,var(--red));
         color:#fff; padding:34px 6vw 26px; }
header h1 { margin:0 0 4px; font-size:28px; }
header p { margin:0; opacity:.85; font-size:14px; }
main { max-width:1100px; margin:0 auto; padding:26px 4vw 70px; }
.card { background:#fff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,.08);
        padding:24px 28px; margin-bottom:24px; }
#drop { border:2.5px dashed #aebadd; border-radius:10px; padding:42px 20px;
        text-align:center; color:#5a6377; cursor:pointer; transition:.15s;
        font-size:15px; }
#drop.drag { border-color:var(--navy); background:#eef3ff; color:var(--navy); }
#drop strong { color:var(--navy); }
.opts { display:flex; gap:26px; flex-wrap:wrap; margin-top:16px; font-size:14px;
        align-items:center; }
.opts label { display:flex; align-items:center; gap:7px; cursor:pointer; }
.opts .hint { color:#888; font-size:12px; }
button.primary { background:var(--navy); color:#fff; border:0; border-radius:8px;
                 padding:10px 22px; font-size:15px; font-weight:600; cursor:pointer; }
button.primary:disabled { opacity:.5; cursor:default; }
table { border-collapse:collapse; width:100%; font-size:13.5px; }
th, td { text-align:left; padding:9px 10px; border-bottom:1px solid #e8ebf1; }
th { background:#f0f3f9; font-size:12px; letter-spacing:.5px; text-transform:uppercase;
     color:#555; }
.score { font-weight:800; font-size:16px; }
.pill { color:#fff; border-radius:10px; padding:2px 10px; font-size:11.5px;
        font-weight:700; white-space:nowrap; }
.status-running { color:var(--navy); font-weight:600; }
.status-error { color:var(--red); font-weight:600; }
.spinner { display:inline-block; width:12px; height:12px; border:2px solid #c6d2ee;
           border-top-color:var(--navy); border-radius:50%; margin-right:6px;
           animation:spin .8s linear infinite; vertical-align:-2px; }
@keyframes spin { to { transform:rotate(360deg); } }
a { color:var(--navy); }
.topline { display:flex; justify-content:space-between; align-items:center;
           margin-bottom:10px; }
.topline h2 { margin:0; font-size:18px; color:var(--navy); }
footer { text-align:center; color:#889; font-size:12px; padding:16px; }
.empty { color:#778; text-align:center; padding:26px 0; }
</style></head>
<body>
<header>
  <h1>SCORM AI Analyzer</h1>
  <p>Drop a SCORM / xAPI / cmi5 / AICC package — get every data point, an
     AI-readiness score, and a full automated playthrough.</p>
</header>
<main>
  <div class="card">
    <div id="drop">
      <strong>Drop SCORM .zip files here</strong> or click to browse
      <div style="font-size:12px;margin-top:6px;" id="limit"></div>
      <input type="file" id="file" accept=".zip" multiple hidden>
    </div>
    <div class="opts">
      <label><input type="checkbox" id="opt-play" checked>
        Play course in headless browser</label>
      <label><input type="checkbox" id="opt-llm">
        AI enrichment <span class="hint" id="llm-hint"></span></label>
      <button class="primary" id="upload" disabled>Analyze</button>
      <span id="picked" class="hint"></span>
    </div>
  </div>

  <div class="card">
    <div class="topline">
      <h2>Analyses</h2>
      <a href="/report" target="_blank">Combined report ↗</a>
    </div>
    <table id="jobs-table">
      <thead><tr>
        <th>Package</th><th>Title</th><th>Standard</th><th>Tool</th>
        <th>Score</th><th>Tier</th><th>Playthrough</th><th>Status</th><th>Output</th>
      </tr></thead>
      <tbody id="jobs"></tbody>
    </table>
    <div class="empty" id="empty">No analyses yet.</div>
  </div>
</main>
<footer>SCORM AI Analyzer v2 — deterministic extraction · headless playthrough ·
optional AI enrichment</footer>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const uploadBtn = document.getElementById('upload');
const picked = document.getElementById('picked');
let files = [];

fetch('/api/config').then(r => r.json()).then(cfg => {
  document.getElementById('limit').textContent =
    'Up to ' + cfg.max_upload_mb + ' MB per file';
  const hint = document.getElementById('llm-hint');
  const llm = document.getElementById('opt-llm');
  if (cfg.llm_available) {
    hint.textContent = '(' + cfg.llm_provider + ' key detected)';
    llm.checked = true;
  } else {
    hint.textContent = '(no API key set — deterministic mode)';
    llm.disabled = true;
  }
});

drop.onclick = () => fileInput.click();
['dragover','dragenter'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.add('drag'); }));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.remove('drag'); }));
drop.addEventListener('drop', ev => setFiles([...ev.dataTransfer.files]));
fileInput.onchange = () => setFiles([...fileInput.files]);

function setFiles(list) {
  files = list.filter(f => f.name.toLowerCase().endsWith('.zip'));
  picked.textContent = files.length
    ? files.length + ' file(s): ' + files.map(f => f.name).join(', ').slice(0, 120)
    : 'No .zip files selected';
  uploadBtn.disabled = !files.length;
}

uploadBtn.onclick = async () => {
  uploadBtn.disabled = true;
  uploadBtn.textContent = 'Uploading…';
  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  fd.append('play', document.getElementById('opt-play').checked);
  fd.append('llm', document.getElementById('opt-llm').checked);
  try {
    const resp = await fetch('/api/analyze', { method:'POST', body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert('Upload failed: ' + (err.detail || resp.status));
    }
  } finally {
    files = []; picked.textContent = ''; fileInput.value = '';
    uploadBtn.textContent = 'Analyze';
    refresh();
  }
};

const tierColors = { 'AI Ready':'#1a7f37', 'Partially Ready':'#b08800',
                     'Not Ready':'#c8102e' };
function scoreColor(s) {
  if (s == null) return '#666';
  return s >= 70 ? '#1a7f37' : s >= 40 ? '#b08800' : '#c8102e';
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function refresh() {
  const data = await fetch('/api/jobs').then(r => r.json()).catch(() => ({jobs:[]}));
  const tbody = document.getElementById('jobs');
  document.getElementById('empty').style.display =
    data.jobs.length ? 'none' : 'block';
  tbody.innerHTML = data.jobs.map(j => {
    const s = j.summary || {};
    const status = j.status === 'running'
      ? '<span class="status-running"><span class="spinner"></span>analyzing…</span>'
      : j.status === 'queued' ? 'queued'
      : j.status === 'error'
      ? '<span class="status-error" title="' + esc(j.error) + '">error</span>'
      : 'done';
    const links = j.status === 'done'
      ? '<a href="' + j.report_url + '" target="_blank">Report</a> · ' +
        '<a href="' + j.json_url + '">JSON</a>'
      : '—';
    return '<tr>' +
      '<td>' + esc(j.filename) + '</td>' +
      '<td>' + esc(s.title || '—') + '</td>' +
      '<td>' + esc(s.package_type ? s.package_type + ' ' + (s.scorm_version||'') : '—') + '</td>' +
      '<td>' + esc(s.authoring_tool || '—') + '</td>' +
      '<td class="score" style="color:' + scoreColor(s.score) + ';">' +
        (s.score ?? '—') + '</td>' +
      '<td>' + (s.tier ? '<span class="pill" style="background:' +
        (tierColors[s.tier] || '#666') + ';">' + esc(s.tier) + '</span>' : '—') + '</td>' +
      '<td>' + esc(s.completion_status || '—') + '</td>' +
      '<td>' + status + '</td>' +
      '<td>' + links + '</td></tr>';
  }).join('');
}
refresh();
setInterval(refresh, 2500);
</script>
</body></html>
"""
