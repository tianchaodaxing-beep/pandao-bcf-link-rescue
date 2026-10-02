"""Bounded ZIP/XML parsing and immutable BCF viewpoint cloning."""

import copy
import io
import json
import re
import stat
import uuid
import zipfile
from collections import Counter
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from .model import InputError, match_components, sha256

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_REFERENCES = 200000
MAX_XML_BYTES = 5 * 1024 * 1024
MAX_RATIO = 100
FIXED_ZIP_DATE = (2020, 1, 1, 0, 0, 0)


def safe_name(name):
    if not name or "\\" in name or ":" in name or "\x00" in name or name.startswith("/"):
        raise InputError("Unsafe ZIP entry or local viewpoint name.")
    parts = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise InputError("Unsafe ZIP path segments.")
    return name


def read_archive(raw):
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise InputError("BCF archive exceeds the 100 MiB compressed limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise InputError("BCF archive exceeds the 20000 entry limit.")
            seen, total = set(), 0
            for info in infos:
                safe_name(info.orig_filename)
                safe_name(info.filename)
                key = info.filename.rstrip("/").casefold()
                if key in seen:
                    raise InputError("Duplicate ZIP entry names are not allowed.")
                seen.add(key)
                if info.flag_bits & 1:
                    raise InputError("Encrypted ZIP entries are unsupported.")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise InputError("Only stored and deflated ZIP entries are supported.")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise InputError("ZIP symbolic links are unsupported.")
                if info.file_size > MAX_ENTRY_BYTES or info.file_size > max(1, info.compress_size) * MAX_RATIO:
                    raise InputError("ZIP entry exceeds the size or compression ratio limit.")
                total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise InputError("BCF archive exceeds the 250 MiB expanded limit.")
            result = {}
            for info in infos:
                if not info.is_dir():
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        raise InputError("ZIP expanded size does not match its directory.")
                    result[info.filename] = data
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
        raise InputError(f"Invalid BCF ZIP archive: {error}") from error


def xml(raw, name):
    if len(raw) > MAX_XML_BYTES:
        raise InputError(f"XML entry exceeds the 5 MiB limit: {name}")
    # Decode first so UTF-16/32 cannot bypass the DTD/entity check.
    try:
        if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            source = raw.decode("utf-32")
        elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            source = raw.decode("utf-16")
        elif b"\x00" in raw[:128]:
            raise InputError(f"XML without a supported encoding BOM: {name}")
        else:
            source = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise InputError(f"XML must use UTF-8 or BOM-marked UTF-16/32: {name}") from error
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source, re.I):
        raise InputError(f"DTD and entity declarations are forbidden: {name}")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as error:
        raise InputError(f"Malformed XML in {name}: {error}") from error
    if any("}" in e.tag for e in root.iter() if isinstance(e.tag, str)):
        raise InputError(f"Namespaced BCF XML is unsupported: {name}")
    stack, count = [(root, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 100 or count > 100000:
            raise InputError(f"XML nesting or element count exceeds limits: {name}")
        stack.extend((child, depth + 1) for child in node)
    return root


def serialize(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)


def write_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def component_nodes(root):
    components = root.find("Components")
    if components is None:
        return []
    allowed = []
    for group, query in (("selection", "Selection/Component"),
                         ("visibility", "Visibility/Exceptions/Component"),
                         ("coloring", "Coloring/Color/Component")):
        allowed.extend((group, node) for node in components.findall(query))
    if len(allowed) != len(components.findall(".//Component")):
        raise InputError("Unsupported BCF component reference location.")
    return allowed


def repair_archive(old, new, raw):
    resolve, frame_ok = match_components(old, new)
    entries = read_archive(raw)
    if "bcf.version" not in entries:
        raise InputError("BCF archive must contain bcf.version.")
    version = xml(entries["bcf.version"], "bcf.version")
    if version.tag != "Version" or version.get("VersionId") != "2.1":
        raise InputError("Only BCF 2.1 archives are supported.")
    # Parse every XML entry, including untouched originals, without network access.
    parsed = {name: xml(data, name) for name, data in entries.items()
              if name.endswith((".bcf", ".bcfv", ".version", ".bcfp"))}
    markups = [name for name in entries if PurePosixPath(name).name == "markup.bcf"]
    if not markups:
        raise InputError("No BCF topic markup was found.")
    rows, clones, topics = [], [], set()
    output = dict(entries)
    seen_viewpoint_ids = set()
    for markup_name in sorted(markups):
        markup = parsed[markup_name]
        if markup.tag != "Markup":
            raise InputError("Expected BCF Markup root.")
        topic = markup.find("Topic")
        topic_id = topic.get("Guid", "") if topic is not None else ""
        try:
            uuid.UUID(topic_id)
        except ValueError as error:
            raise InputError("BCF topic must have a valid UUID.") from error
        if topic_id in topics:
            raise InputError("Duplicate BCF topic UUID.")
        topics.add(topic_id)
        title = topic.findtext("Title", default="")
        if len(title) > 4096:
            raise InputError("BCF topic Title exceeds the 4096 character limit.")
        parent = str(PurePosixPath(markup_name).parent)
        prefix = "" if parent == "." else parent + "/"
        existing_refs = list(markup.findall("Viewpoints"))
        indices = []
        for ref in existing_refs:
            index = ref.findtext("Index")
            if index is not None:
                try:
                    indices.append(int(index))
                except ValueError as error:
                    raise InputError("Invalid BCF viewpoint Index.") from error
        next_index = max(indices, default=0) + 1
        changed_markup = False
        topic_files = set()
        for reference in existing_refs:
            filename = reference.findtext("Viewpoint")
            if filename is None:
                continue
            safe_name(filename)
            if "/" in filename:
                raise InputError("Viewpoint references must be local filenames within their topic.")
            view_name = prefix + filename
            if view_name in topic_files:
                raise InputError("Duplicate BCF viewpoint file references.")
            topic_files.add(view_name)
            if view_name not in parsed:
                raise InputError(f"Missing referenced viewpoint: {view_name}")
            original = parsed[view_name]
            view_id = original.get("Guid", "")
            try:
                uuid.UUID(view_id)
            except ValueError as error:
                raise InputError("BCF viewpoint must have a valid UUID.") from error
            if original.tag != "VisualizationInfo" or reference.get("Guid") != view_id:
                raise InputError("BCF viewpoint UUID differs from its markup reference.")
            if view_id in seen_viewpoint_ids:
                raise InputError("Duplicate BCF viewpoint UUID.")
            seen_viewpoint_ids.add(view_id)
            snapshot = reference.findtext("Snapshot")
            if snapshot:
                safe_name(snapshot)
                if "/" in snapshot or prefix + snapshot not in entries:
                    raise InputError("Missing or nonlocal BCF snapshot reference.")
            revised = copy.deepcopy(original)
            changes = 0
            for position, (group, component) in enumerate(component_nodes(revised)):
                guid = component.get("IfcGuid", "")
                row = resolve(guid)
                row.update(topic_id=topic_id, topic_title=title, viewpoint_id=view_id,
                           group=group, position=position)
                rows.append(row)
                if len(rows) > MAX_REFERENCES:
                    raise InputError("BCF archive exceeds the 200000 component reference limit.")
                if row["status"] == "rebound":
                    component.set("IfcGuid", row["new_guid"])
                    changes += 1
            if changes:
                seed = "bcf-link-rescue/1:" + topic_id + ":" + view_id + ":" + sha256(entries[view_name]) + ":" + old["source"]["sha256"] + ":" + new["source"]["sha256"]
                cloned_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
                cloned_file = "rescue-" + cloned_id + ".bcfv"
                if prefix + cloned_file in output:
                    raise InputError("Generated viewpoint already exists. Start with the original BCF archive.")
                revised.set("Guid", cloned_id)
                output[prefix + cloned_file] = serialize(revised)
                cloned_ref = copy.deepcopy(reference)
                cloned_ref.set("Guid", cloned_id)
                cloned_ref.find("Viewpoint").text = cloned_file
                cloned_index = cloned_ref.find("Index")
                if cloned_index is not None:
                    cloned_index.text = str(next_index)
                next_index += 1
                markup.append(cloned_ref)
                changed_markup = True
                clones.append({"topic_id": topic_id, "original_id": view_id, "draft_id": cloned_id,
                               "original_file": view_name, "draft_file": prefix + cloned_file,
                               "rebound_reference_nodes": changes})
        if changed_markup:
            output[markup_name] = serialize(markup)
    counts = dict(sorted(Counter(r["status"] for r in rows).items()))
    unique = {r["old_guid"]: r for r in rows}
    review_count = sum(v for k, v in counts.items() if k not in ("retained", "rebound"))
    report = {"format": "bcf-link-rescue-report/1", "draft": True, "frame_compatible": frame_ok,
              "notice": "Review draft. Inspect cloned viewpoints against your actual updated model before using or sharing.",
              "scope": "Component links only. Unchanged placement does not prove unchanged geometry.",
              "inputs": {"old_model": old["source"], "new_model": new["source"], "bcf_sha256": sha256(raw)},
              "summary": {"topics": len(topics), "reference_nodes": len(rows), "unique_references": len(unique),
                          "status_counts": counts, "unique_status_counts": dict(sorted(Counter(r["status"] for r in unique.values()).items())),
                          "review_nodes": review_count, "cloned_viewpoints": len(clones)},
              "viewpoints": clones, "references": rows,
              "preserved_files": [{"name": name, "sha256": sha256(data)} for name, data in sorted(entries.items()) if name not in markups],
              "output_files": [{"name": name, "sha256": sha256(data)} for name, data in sorted(output.items())]}
    repaired = write_zip(output)
    report["output_bcf_sha256"] = sha256(repaired)
    return repaired, report
