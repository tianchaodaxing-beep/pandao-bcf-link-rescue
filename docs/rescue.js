/* BCF Link Rescue: local inventory matching and bounded BCF cloning. MIT. */
(function (global) {
  'use strict';
  const encoder = new TextEncoder();
  const LIMITS = {archive: 100 * 1024 * 1024, entry: 20 * 1024 * 1024, total: 250 * 1024 * 1024, xml: 5 * 1024 * 1024, entries: 20000, references: 200000};
  const guidPattern = /^[0-3][0-9A-Za-z_$]{21}$/;
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const hex = bytes => Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, '0')).join('');
  async function digest(bytes) { return hex(await crypto.subtle.digest('SHA-256', bytes)); }
  function safeName(name) {
    assert(name && !/[\\:\x00]/.test(name) && !name.startsWith('/'), 'Unsafe ZIP entry or local viewpoint name.');
    assert(name.replace(/\/$/, '').split('/').every(part => part && part !== '.' && part !== '..'), 'Unsafe ZIP path segments.');
    return name;
  }
  function crc32(bytes) {
    let crc = -1;
    for (const byte of bytes) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    return (crc ^ -1) >>> 0;
  }
  function readZip(bytes) {
    assert(bytes.length <= LIMITS.archive, 'BCF archive exceeds the 100 MiB compressed limit.');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let end = -1;
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
      if (view.getUint32(i, true) === 0x06054b50 && i + 22 + view.getUint16(i + 20, true) === bytes.length) { end = i; break; }
    }
    assert(end >= 0, 'Invalid ZIP end directory.');
    assert(view.getUint16(end + 4, true) === 0 && view.getUint16(end + 6, true) === 0, 'Multi-volume ZIP is unsupported.');
    const count = view.getUint16(end + 10, true), centralSize = view.getUint32(end + 12, true), start = view.getUint32(end + 16, true);
    assert(count === view.getUint16(end + 8, true) && count < 65535 && count <= LIMITS.entries, 'ZIP entry count exceeds limits.');
    assert(start + centralSize === end, 'Invalid or ZIP64 central directory.');
    const decoder = new TextDecoder('utf-8', {fatal: true});
    const records = [], seen = new Set();
    let offset = start, total = 0;
    for (let entry = 0; entry < count; entry++) {
      assert(offset + 46 <= end && view.getUint32(offset, true) === 0x02014b50, 'Malformed ZIP central entry.');
      const flags = view.getUint16(offset + 8, true), method = view.getUint16(offset + 10, true);
      const crc = view.getUint32(offset + 16, true), packed = view.getUint32(offset + 20, true), size = view.getUint32(offset + 24, true);
      const length = view.getUint16(offset + 28, true), extra = view.getUint16(offset + 30, true), comment = view.getUint16(offset + 32, true);
      const local = view.getUint32(offset + 42, true), attrs = view.getUint32(offset + 38, true);
      assert(offset + 46 + length + extra + comment <= end, 'Truncated ZIP central entry.');
      const name = safeName(decoder.decode(bytes.subarray(offset + 46, offset + 46 + length)));
      const key = name.replace(/\/$/, '').toLowerCase();
      assert(!seen.has(key), 'Duplicate ZIP entry names are not allowed.'); seen.add(key);
      assert(!(flags & 1), 'Encrypted ZIP entries are unsupported.');
      assert(method === 0 || method === 8, 'Only stored and deflated ZIP entries are supported.');
      assert(((attrs >>> 16) & 0xf000) !== 0xa000, 'ZIP symbolic links are unsupported.');
      assert(size <= LIMITS.entry && size <= Math.max(1, packed) * 100, 'ZIP entry exceeds size or compression ratio limits.');
      total += size;
      assert(total <= LIMITS.total, 'BCF archive exceeds the 250 MiB expanded limit.');
      assert(local + 30 <= start && view.getUint32(local, true) === 0x04034b50, 'Invalid ZIP local header.');
      assert(view.getUint16(local + 6, true) === flags && view.getUint16(local + 8, true) === method, 'ZIP local and central headers disagree.');
      const localNameLength = view.getUint16(local + 26, true), localExtra = view.getUint16(local + 28, true);
      const dataStart = local + 30 + localNameLength + localExtra;
      assert(dataStart + packed <= start, 'ZIP entry overlaps the central directory.');
      assert(decoder.decode(bytes.subarray(local + 30, local + 30 + localNameLength)) === name, 'ZIP local and central filenames disagree.');
      if (!(flags & 8)) assert(view.getUint32(local + 18, true) === packed && view.getUint32(local + 22, true) === size, 'ZIP sizes disagree.');
      records.push({name, method, crc, packed, size, dataStart, local});
      offset += 46 + length + extra + comment;
    }
    assert(offset === end, 'Unexpected ZIP directory data.');
    const ranges = records.map(r => [r.local, r.dataStart + r.packed]).sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < ranges.length; i++) assert(ranges[i][0] >= ranges[i - 1][1], 'Overlapping ZIP entries are unsupported.');
    const result = Object.create(null);
    for (const record of records) {
      const compressed = bytes.subarray(record.dataStart, record.dataStart + record.packed);
      let data;
      if (record.method === 0) {
        assert(record.size === compressed.length, 'Stored ZIP size mismatch.');
        data = compressed.slice();
      } else {
        const chunks = []; let expanded = 0, finished = false;
        const inflator = new fflate.Inflate((chunk, final) => {
          expanded += chunk.length;
          assert(expanded <= record.size && expanded <= LIMITS.entry, 'ZIP stream expands beyond its declared size.');
          chunks.push(chunk); finished = final;
        });
        if (!compressed.length) inflator.push(new Uint8Array(), true);
        for (let i = 0; i < compressed.length; i += 1024) inflator.push(compressed.subarray(i, i + 1024), i + 1024 >= compressed.length);
        assert(finished && expanded === record.size, 'ZIP expanded size mismatch.');
        data = new Uint8Array(expanded); let cursor = 0;
        for (const chunk of chunks) { data.set(chunk, cursor); cursor += chunk.length; }
      }
      assert(crc32(data) === record.crc, 'ZIP CRC mismatch.');
      if (!record.name.endsWith('/')) result[record.name] = data;
    }
    return result;
  }
  function writeZip(entries) {
    const sorted = Object.create(null);
    for (const name of Object.keys(entries).sort()) sorted[name] = [entries[name], {mtime: new Date(2020, 0, 1), level: 6}];
    return fflate.zipSync(sorted);
  }
  function parseXml(bytes, name) {
    assert(bytes.length <= LIMITS.xml, 'XML entry exceeds the 5 MiB limit: ' + name);
    let encoding = 'utf-8';
    if (bytes[0] === 255 && bytes[1] === 254) encoding = 'utf-16le';
    else if (bytes[0] === 254 && bytes[1] === 255) encoding = 'utf-16be';
    assert(!(bytes[0] === 0 && bytes[1] === 0) && !(bytes[0] === 255 && bytes[1] === 254 && bytes[2] === 0 && bytes[3] === 0), 'UTF-32 XML is supported by the CLI only.');
    const source = new TextDecoder(encoding, {fatal: true}).decode(bytes);
    assert(!/<!\s*(DOCTYPE|ENTITY)\b/i.test(source), 'DTD and entity declarations are forbidden: ' + name);
    const document = new DOMParser().parseFromString(source, 'application/xml');
    assert(!document.querySelector('parsererror'), 'Malformed XML: ' + name);
    assert([...document.querySelectorAll('*')].every(node => !node.namespaceURI), 'Namespaced BCF XML is unsupported: ' + name);
    const stack = [[document.documentElement, 0]]; let count = 0;
    while (stack.length) {
      const [node, depth] = stack.pop(); count++;
      assert(depth <= 100 && count <= 100000, 'XML nesting or element count exceeds limits: ' + name);
      for (const child of node.children) stack.push([child, depth + 1]);
    }
    return document;
  }
  function serialize(document) { return encoder.encode('<?xml version="1.0" encoding="utf-8"?>\n' + new XMLSerializer().serializeToString(document.documentElement)); }
  const children = (node, tag) => [...node.children].filter(child => child.tagName === tag);
  const child = (node, tag) => children(node, tag)[0];
  const text = (node, tag) => child(node, tag)?.textContent ?? '';
  function validateInventory(inventory) {
    assert(inventory?.format === 'bcf-link-rescue-inventory/1', 'Export inventories with inspect-model before using this browser tool.');
    assert(['IFC2X3', 'IFC4'].includes(inventory.schema), 'Unsupported inventory IFC schema.');
    assert(typeof inventory.source?.name === 'string' && /^[0-9a-f]{64}$/.test(inventory.source?.sha256 || ''), 'Inventory source fingerprint is invalid.');
    assert(inventory.frame && typeof inventory.frame === 'object' && Array.isArray(inventory.frame.contexts) && inventory.frame.contexts.length > 0, 'Inventory coordinate frame is missing.');
    assert(Array.isArray(inventory.products) && inventory.products.length <= 200000, 'Inventory product list is missing or too large.');
    const seen = new Set();
    for (const product of inventory.products) {
      assert(product && guidPattern.test(product.global_id), 'Inventory contains an invalid GlobalId.');
      assert(!seen.has(product.global_id), 'Inventory contains duplicate GlobalIds.'); seen.add(product.global_id);
      assert(typeof product.class === 'string' && /^Ifc[A-Za-z0-9]+$/.test(product.class), 'Invalid inventory entity class.');
      assert(product.tag === null || typeof product.tag === 'string' && product.tag.length <= 4096, 'Invalid inventory Tag.');
      if (product.placement !== null) {
        for (const [key, length] of [['translation_m', 3], ['rotation', 9]]) assert(Array.isArray(product.placement?.[key]) && product.placement[key].length === length && product.placement[key].every(Number.isFinite), 'Invalid inventory placement values.');
        const r = product.placement.rotation;
        for (let row = 0; row < 3; row++) for (let other = 0; other < 3; other++) {
          const dot = [0, 1, 2].reduce((sum, k) => sum + r[row * 3 + k] * r[other * 3 + k], 0);
          assert(Math.abs(dot - (row === other ? 1 : 0)) <= 0.000001, 'Inventory orientation is not orthonormal.');
        }
        const determinant = r[0]*(r[4]*r[8]-r[5]*r[7])-r[1]*(r[3]*r[8]-r[5]*r[6])+r[2]*(r[3]*r[7]-r[4]*r[6]);
        assert(Math.abs(determinant - 1) <= 0.000001, 'Inventory orientation must be a proper rotation.');
      }
    }
    return inventory;
  }
  function framesEqual(a, b) {
    if (typeof a !== typeof b || (a === null) !== (b === null) || Array.isArray(a) !== Array.isArray(b)) return false;
    if (typeof a === 'number') return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= 1e-9;
    if (a && typeof a === 'object') {
      const keys = Object.keys(a).sort();
      return JSON.stringify(keys) === JSON.stringify(Object.keys(b).sort()) && keys.every(key => framesEqual(a[key], b[key]));
    }
    return a === b;
  }
  function placementsEqual(a, b) {
    return a !== null && b !== null && ['translation_m', 'rotation'].every(key => a[key].every((value, index) => Math.abs(value - b[key][index]) <= (key === 'translation_m' ? 0.000001 : 0.00000001)));
  }
  function matcher(old, updated) {
    validateInventory(old); validateInventory(updated);
    const oldIds = new Map(old.products.map(p => [p.global_id, p])), newIds = new Map(updated.products.map(p => [p.global_id, p]));
    const oldKeys = new Map(), newKeys = new Map(), newTags = new Map();
    const key = p => JSON.stringify([p.class, p.tag]);
    for (const [inventory, index] of [[old, oldKeys], [updated, newKeys]]) for (const p of inventory.products) {
      if (p.tag?.trim()) { const k = key(p); index.set(k, [...(index.get(k) || []), p]); }
    }
    for (const p of updated.products) newTags.set(p.tag, [...(newTags.get(p.tag) || []), p]);
    const frameCompatible = framesEqual(old.frame, updated.frame);
    function resolve(guid) {
      const item = oldIds.get(guid), result = {old_guid: guid, new_guid: guid, class: item?.class || '', tag: item?.tag || ''};
      const outcome = (status, detail, target) => ({...result, status, detail, new_guid: target?.global_id || guid});
      if (!guidPattern.test(guid)) return outcome('invalid_reference', 'BCF reference is not a valid IFC GlobalId.');
      if (!item) return outcome('absent_old', 'Reference is absent from the old IFC inventory.');
      if (!frameCompatible) return outcome('frame_changed', 'Coordinate frame or georeferencing changed. Review the model frame first.');
      if (newIds.has(guid)) {
        if (!placementsEqual(item.placement, newIds.get(guid).placement)) return outcome('retained_review', 'GlobalId still exists, but placement is changed or unavailable.');
        if (item.class !== newIds.get(guid).class) return outcome('retained_review', 'GlobalId still exists, but entity class changed.');
        return outcome('retained', 'GlobalId still exists in the new IFC.');
      }
      if (!item.tag?.trim()) return outcome('missing_tag', 'No nonempty exact Tag is available.');
      if (oldKeys.get(key(item)).length !== 1) return outcome('ambiguous_old', 'Class + Tag is duplicated in the old model.');
      const candidates = newKeys.get(key(item)) || [];
      if (!candidates.length) return outcome(newTags.has(item.tag) ? 'class_changed' : 'removed', newTags.has(item.tag) ? 'Tag exists in the new model under a different IFC class.' : 'No component with this class + Tag exists in the new model.');
      if (candidates.length !== 1) return outcome('ambiguous_new', 'Class + Tag is duplicated in the new model.');
      const target = candidates[0];
      if (oldIds.has(target.global_id) && target.global_id !== guid) return outcome('identity_conflict', 'Candidate GlobalId belongs to another old component.');
      if (item.placement === null || target.placement === null) return outcome('placement_unavailable', 'A supported placement is required in both models.');
      if (!placementsEqual(item.placement, target.placement)) return outcome('moved', 'Translation or orientation changed beyond the strict tolerance.');
      return outcome('rebound', 'Unique exact class + Tag and unchanged normalized placement.', target);
    }
    return {resolve, frameCompatible};
  }
  async function uuid5(seed) {
    const namespace = '6ba7b8119dad11d180b400c04fd430c8';
    const input = new Uint8Array(16 + encoder.encode(seed).length);
    input.set(namespace.match(/../g).map(value => parseInt(value, 16))); input.set(encoder.encode(seed), 16);
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-1', input)).slice(0, 16);
    hash[6] = (hash[6] & 15) | 0x50; hash[8] = (hash[8] & 63) | 0x80;
    const h = hex(hash); return [h.slice(0, 8), h.slice(8, 12), h.slice(12, 16), h.slice(16, 20), h.slice(20)].join('-');
  }
  function componentNodes(root) {
    const components = child(root, 'Components');
    if (!components) return [];
    const selection = child(components, 'Selection'), visibility = child(child(components, 'Visibility') || document.createElement('empty'), 'Exceptions'), coloring = child(components, 'Coloring');
    const rows = [...(selection ? children(selection, 'Component').map(node => ['selection', node]) : []), ...(visibility ? children(visibility, 'Component').map(node => ['visibility', node]) : []), ...(coloring ? children(coloring, 'Color').flatMap(color => children(color, 'Component').map(node => ['coloring', node])) : [])];
    assert(rows.length === components.getElementsByTagName('Component').length, 'Unsupported BCF component reference location.');
    return rows;
  }
  async function repair(old, updated, raw, progress = () => {}) {
    const {resolve, frameCompatible} = matcher(old, updated), entries = readZip(raw);
    assert(entries['bcf.version'], 'BCF archive must contain bcf.version.');
    const version = parseXml(entries['bcf.version'], 'bcf.version').documentElement;
    assert(version.tagName === 'Version' && version.getAttribute('VersionId') === '2.1', 'Only BCF 2.1 archives are supported.');
    const parsed = new Map();
    for (const [name, data] of Object.entries(entries)) if (/\.(bcf|bcfv|version|bcfp)$/.test(name)) parsed.set(name, parseXml(data, name));
    const markups = Object.keys(entries).filter(name => name.split('/').pop() === 'markup.bcf').sort();
    assert(markups.length, 'No BCF topic markup was found.');
    const output = {...entries}, references = [], viewpoints = [], topicIds = new Set(), viewIds = new Set();
    for (const [topicIndex, markupName] of markups.entries()) {
      progress(topicIndex + 1, markups.length);
      const markup = parsed.get(markupName).cloneNode(true), root = markup.documentElement;
      assert(root.tagName === 'Markup', 'Expected BCF Markup root.');
      const topic = child(root, 'Topic'), topicId = topic?.getAttribute('Guid') || '';
      assert(uuidPattern.test(topicId) && !topicIds.has(topicId), 'Invalid or duplicate BCF topic UUID.'); topicIds.add(topicId);
      assert(text(topic, 'Title').length <= 4096, 'BCF topic Title exceeds the 4096 character limit.');
      const prefix = markupName.slice(0, -'markup.bcf'.length), originalRefs = children(root, 'Viewpoints'), topicFiles = new Set();
      let nextIndex = Math.max(0, ...originalRefs.filter(r => child(r, 'Index')).map(r => {
        const number = Number(text(r, 'Index')); assert(Number.isInteger(number), 'Invalid BCF viewpoint Index.'); return number;
      })) + 1, changed = false;
      for (const reference of originalRefs) {
        const filename = text(reference, 'Viewpoint'); if (!filename) continue;
        safeName(filename); assert(!filename.includes('/'), 'Viewpoint references must be local filenames within their topic.');
        const viewName = prefix + filename;
        assert(!topicFiles.has(viewName), 'Duplicate BCF viewpoint file references.'); topicFiles.add(viewName);
        assert(parsed.has(viewName), 'Missing referenced viewpoint: ' + viewName);
        const original = parsed.get(viewName), viewId = original.documentElement.getAttribute('Guid') || '';
        assert(uuidPattern.test(viewId) && !viewIds.has(viewId), 'Invalid or duplicate BCF viewpoint UUID.'); viewIds.add(viewId);
        assert(original.documentElement.tagName === 'VisualizationInfo' && reference.getAttribute('Guid') === viewId, 'BCF viewpoint UUID differs from its markup reference.');
        const snapshot = text(reference, 'Snapshot');
        if (snapshot) { safeName(snapshot); assert(!snapshot.includes('/') && entries[prefix + snapshot], 'Missing or nonlocal BCF snapshot reference.'); }
        const revised = original.cloneNode(true); let changes = 0;
        for (const [position, [group, component]] of componentNodes(revised.documentElement).entries()) {
          const result = resolve(component.getAttribute('IfcGuid') || '');
          references.push({...result, topic_id: topicId, topic_title: text(topic, 'Title'), viewpoint_id: viewId, group, position});
          assert(references.length <= LIMITS.references, 'BCF archive exceeds the 200000 component reference limit.');
          if (result.status === 'rebound') { component.setAttribute('IfcGuid', result.new_guid); changes++; }
        }
        if (changes) {
          const seed = 'bcf-link-rescue/1:' + topicId + ':' + viewId + ':' + await digest(entries[viewName]) + ':' + old.source.sha256 + ':' + updated.source.sha256;
          const cloneId = await uuid5(seed), cloneFile = 'rescue-' + cloneId + '.bcfv';
          assert(!output[prefix + cloneFile], 'Generated viewpoint already exists. Start with the original BCF archive.');
          revised.documentElement.setAttribute('Guid', cloneId); output[prefix + cloneFile] = serialize(revised);
          const ref = reference.cloneNode(true); ref.setAttribute('Guid', cloneId); child(ref, 'Viewpoint').textContent = cloneFile;
          if (child(ref, 'Index')) child(ref, 'Index').textContent = String(nextIndex); nextIndex++;
          root.appendChild(ref); changed = true;
          viewpoints.push({topic_id: topicId, original_id: viewId, draft_id: cloneId, original_file: viewName, draft_file: prefix + cloneFile, rebound_reference_nodes: changes});
        }
      }
      if (changed) output[markupName] = serialize(markup);
      if (topicIndex % 20 === 0) await new Promise(resolve => setTimeout(resolve, 0));
    }
    const count = rows => rows.reduce((result, row) => (result[row.status] = (result[row.status] || 0) + 1, result), {});
    const unique = [...new Map(references.map(row => [row.old_guid, row])).values()], counts = count(references);
    const fixed = writeZip(output), report = {format: 'bcf-link-rescue-report/1', draft: true, frame_compatible: frameCompatible,
      notice: 'Review draft. Inspect cloned viewpoints against your actual updated model before using or sharing.', scope: 'Component links only. Unchanged placement does not prove unchanged geometry.',
      inputs: {old_model: old.source, new_model: updated.source, bcf_sha256: await digest(raw)},
      summary: {topics: markups.length, reference_nodes: references.length, unique_references: unique.length, status_counts: counts, unique_status_counts: count(unique), review_nodes: references.filter(row => !['retained', 'rebound'].includes(row.status)).length, cloned_viewpoints: viewpoints.length},
      viewpoints, references, preserved_files: [], output_files: [], output_bcf_sha256: await digest(fixed)};
    for (const [name, data] of Object.entries(entries).sort()) if (!markups.includes(name)) report.preserved_files.push({name, sha256: await digest(data)});
    for (const [name, data] of Object.entries(output).sort()) report.output_files.push({name, sha256: await digest(data)});
    return {fixed, report};
  }
  const escape = value => String(value).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  function csv(report) {
    const fields = ['topic_id', 'topic_title', 'viewpoint_id', 'group', 'position', 'status', 'class', 'tag', 'old_guid', 'new_guid', 'detail'];
    const value = input => { let text = String(input ?? ''); if (/^[\t\r\n]/.test(text) || /^[=+\-@]/.test(text.trimStart())) text = "'" + text; return '"' + text.replace(/"/g, '""') + '"'; };
    return fields.join(',') + '\n' + report.references.map(row => fields.map(key => value(row[key])).join(',')).join('\n') + '\n';
  }
  function reportHtml(report) {
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>BCF Link Rescue review draft</title><style>body{font:15px system-ui;margin:32px;color:#163e33}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:10px;border:1px solid #ccc;text-align:left;overflow-wrap:anywhere}.notice{background:#fff0cb;padding:20px}.scroll{overflow:auto}</style><h1>BCF Link Rescue</h1><p class="notice">' + escape(report.notice) + '<br>' + escape(report.scope) + '<br>Manually load the new IFC in your model viewer. Original BCF model-file metadata may still name the old IFC.</p><p>' + report.summary.reference_nodes + ' reference nodes; ' + report.summary.review_nodes + ' need review; ' + report.summary.cloned_viewpoints + ' appended draft viewpoints.</p><div class="scroll"><table><tr><th>Outcome</th><th>Tag</th><th>Class</th><th>Group</th><th>Original ID</th><th>Draft ID</th><th>Detail</th></tr>' + report.references.map(row => '<tr>' + ['status', 'tag', 'class', 'group', 'old_guid', 'new_guid', 'detail'].map(key => '<td>' + escape(row[key]) + '</td>').join('') + '</tr>').join('') + '</table></div><a href="mailto:tianchaodaxing@gmail.com">Contact PANDAO</a></html>';
  }
  function packet(result, old, updated) {
    return writeZip({'rebound.bcfzip': result.fixed, 'review.json': encoder.encode(JSON.stringify(result.report, null, 2)), 'review.csv': encoder.encode(csv(result.report)), 'review.html': encoder.encode(reportHtml(result.report)), 'old.inventory.json': encoder.encode(JSON.stringify(old, null, 2)), 'new.inventory.json': encoder.encode(JSON.stringify(updated, null, 2))});
  }
  global.BCFRescue = {LIMITS, digest, readZip, writeZip, validateInventory, matcher, repair, packet, csv, reportHtml};
})(globalThis);
