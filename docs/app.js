/* Local workspace UI. User-controlled text is inserted with textContent only. */
'use strict';
const $ = id => document.getElementById(id);
const files = {old: null, updated: null, bcf: null};
let current = null, currentOld = null, currentNew = null, synthetic = false, busy = false;
const labels = {rebound: 'Rebound', retained: 'Retained', ambiguous_old: 'Duplicate old Tag', ambiguous_new: 'Duplicate new Tag', missing_tag: 'Missing Tag', removed: 'Missing in new model', moved: 'Placement changed', class_changed: 'IFC class changed', absent_old: 'Absent from old model', invalid_reference: 'Invalid reference', frame_changed: 'Coordinate frame changed', placement_unavailable: 'Placement unavailable', identity_conflict: 'Identity conflict', retained_review: 'Retained; needs review'};
function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
function setBusy(value) { busy = value; for (const id of ['analyze', 'demo', 'hero-demo', 'download-bcf', 'download-packet']) $(id).disabled = value; }
for (const [inputId, key, labelId] of [['old-file', 'old', 'old-name'], ['new-file', 'updated', 'new-name'], ['bcf-file', 'bcf', 'bcf-name']]) {
  $(inputId).addEventListener('change', () => {
    files[key] = $(inputId).files[0] || null;
    $(labelId).textContent = files[key]?.name || (key === 'bcf' ? 'Choose BCF archive' : 'Choose inventory JSON');
    current = null; $('results').hidden = true; synthetic = false;
    status('Inputs updated. Analyze all three files to build a new review draft.');
  });
}
async function loadJson(file) {
  if (file.size > 100 * 1024 * 1024) throw new Error('Inventory exceeds the 100 MiB limit.');
  return BCFRescue.validateInventory(JSON.parse(await file.text()));
}
async function run(old, updated, raw, isSample) {
  status('Analyzing local component links…');
  const result = await BCFRescue.repair(old, updated, raw, (number, total) => status('Reviewing topic ' + number + ' of ' + total + '…'));
  current = result; currentOld = old; currentNew = updated; synthetic = isSample;
  const summary = result.report.summary;
  $('count-rebound').textContent = summary.status_counts.rebound || 0;
  $('count-retained').textContent = summary.status_counts.retained || 0;
  $('count-review').textContent = summary.review_nodes;
  $('count-clones').textContent = summary.cloned_viewpoints;
  $('sample-badge').hidden = !isSample;
  $('results').hidden = false;
  $('outcome-filter').value = 'all'; $('group-filter').value = 'all'; $('search').value = '';
  renderTable(); drawMap();
  status((isSample ? 'Synthetic sample complete. ' : 'Review draft ready. ') + summary.reference_nodes + ' reference nodes inspected; ' + summary.review_nodes + ' need review. Inspect all cloned viewpoints in your model viewer.');
}
$('analyze').addEventListener('click', async () => {
  if (busy) return;
  if (!files.old || !files.updated || !files.bcf) { status('Choose the old inventory, new inventory and original BCF archive first.', true); return; }
  setBusy(true); $('results').hidden = true; current = null;
  try {
    if (files.bcf.size > BCFRescue.LIMITS.archive) throw new Error('BCF archive exceeds the 100 MiB compressed limit.');
    await run(await loadJson(files.old), await loadJson(files.updated), new Uint8Array(await files.bcf.arrayBuffer()), false);
  } catch (error) { status(error.message || 'Recovery could not complete.', true); }
  finally { setBusy(false); }
});
async function sample() {
  if (busy) return; setBusy(true); current = null; $('results').hidden = true;
  try {
    status('Loading bundled synthetic IFC inventories and BCF archive…');
    const names = ['old.inventory.json', 'new.inventory.json', 'issues.bcfzip'];
    const responses = await Promise.all(names.map(name => fetch('samples/' + name)));
    if (responses.some(response => !response.ok)) throw new Error('Sample files could not load. Serve this folder over HTTP or use the published site.');
    await run(await responses[0].json(), await responses[1].json(), new Uint8Array(await responses[2].arrayBuffer()), true);
    $('workspace').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  } catch (error) { status(error.message || 'Sample could not load.', true); }
  finally { setBusy(false); }
}
$('demo').addEventListener('click', sample); $('hero-demo').addEventListener('click', sample);
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function renderTable() {
  if (!current) return;
  const outcome = $('outcome-filter').value, group = $('group-filter').value, query = $('search').value.toLowerCase();
  const filtered = current.report.references.filter(row => (outcome === 'all' || (outcome === 'review' ? !['retained', 'rebound'].includes(row.status) : row.status === outcome)) && (group === 'all' || row.group === group) && (!query || [row.tag, row.old_guid, row.new_guid, row.topic_title, row.class].some(value => value.toLowerCase().includes(query))));
  $('reference-rows').replaceChildren();
  for (const row of filtered.slice(0, 200)) {
    const tr = element('tr'), outcomeCell = element('td'); outcomeCell.append(element('span', labels[row.status] || row.status, 'outcome ' + row.status)); tr.append(outcomeCell);
    const tag = element('td'); tag.append(element('strong', row.tag || '(no Tag)'), element('small', row.class || '(not in old model)')); tr.append(tag);
    tr.append(element('td', row.group));
    const ids = element('td'); ids.append(element('code', row.old_guid), element('small', row.old_guid === row.new_guid ? 'Original reference kept' : '↓ New draft reference'), element('code', row.new_guid)); tr.append(ids);
    const detail = element('td'); detail.append(element('span', row.detail), element('small', 'Topic: ' + row.topic_title)); tr.append(detail);
    $('reference-rows').append(tr);
  }
  if (!filtered.length) { const row = element('tr'), cell = element('td', 'No references match these filters.'); cell.colSpan = 5; row.append(cell); $('reference-rows').append(row); }
  $('table-count').textContent = filtered.length > 200 ? 'Showing 200 of ' + filtered.length + ' matching nodes. Download the packet for all rows.' : filtered.length + ' matching nodes';
}
for (const id of ['outcome-filter', 'group-filter', 'search']) $(id).addEventListener('input', renderTable);
function drawMap() {
  if (!current) return;
  const unique = [...new Map(current.report.references.map(row => [row.old_guid, row])).values()], rows = unique.slice(0, 16);
  const canvas = $('reference-map'), width = Math.max(580, canvas.parentElement.clientWidth - 32), height = rows.length * 43 + 65, ratio = devicePixelRatio || 1;
  canvas.style.minWidth = '580px'; canvas.style.height = height + 'px'; canvas.width = width * ratio; canvas.height = height * ratio;
  const context = canvas.getContext('2d'); context.scale(ratio, ratio); context.clearRect(0, 0, width, height);
  context.fillStyle = '#5a705c'; context.font = '11px system-ui'; context.fillText('ORIGINAL BCF REFERENCE', 15, 19); context.fillText('REVIEW DRAFT REFERENCE', width * 0.57, 19);
  rows.forEach((row, index) => {
    const y = 42 + index * 43, color = row.status === 'rebound' ? '#287456' : row.status === 'retained' ? '#809284' : '#b67f34';
    context.strokeStyle = color; context.lineWidth = 1.5; context.setLineDash(['rebound', 'retained'].includes(row.status) ? [] : [4, 4]);
    context.beginPath(); context.moveTo(width * 0.37, y + 9); context.lineTo(width * 0.55, y + 9); context.stroke(); context.setLineDash([]);
    context.beginPath(); context.moveTo(width * 0.55 - 5, y + 5); context.lineTo(width * 0.55, y + 9); context.lineTo(width * 0.55 - 5, y + 13); context.stroke();
    context.fillStyle = '#f8faf4'; context.fillRect(8, y - 7, width * 0.36, 33); context.fillRect(width * 0.56, y - 7, width * 0.43, 33);
    context.fillStyle = '#284b38'; context.font = '11px ui-monospace, monospace'; context.fillText(row.old_guid, 15, y + 5); context.fillText(row.new_guid, width * 0.57, y + 5);
    context.font = '9px system-ui'; context.fillStyle = '#6b7c66'; context.fillText((row.tag || 'No Tag') + ' · ' + (row.class || 'Unknown'), 15, y + 18);
    context.fillStyle = color; context.fillText(labels[row.status] || row.status, width * 0.57, y + 18);
  });
  $('map-limit').textContent = unique.length > 16 ? 'Showing 16 of ' + unique.length + ' unique references' : unique.length + ' unique references';
}
window.addEventListener('resize', drawMap);
function download(bytes, name, type) {
  const url = URL.createObjectURL(new Blob([bytes], {type})), anchor = document.createElement('a');
  anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000);
}
$('download-bcf').addEventListener('click', () => { if (current) download(current.fixed, 'rebound-review-draft.bcfzip', 'application/zip'); });
$('download-packet').addEventListener('click', () => { if (current) download(BCFRescue.packet(current, currentOld, currentNew), 'bcf-link-rescue-review-packet.zip', 'application/zip'); });
