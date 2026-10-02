"""Meaningful IFC, archive preservation, matching and unsafe input regressions."""

import copy
import io
import json
import os
import struct
import uuid
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import ifcopenshell
import ifcopenshell.validate
import pytest

from generate_samples import create_bcf, create_model, generate, gid, TOPIC_ID, VIEW_ID
from bcf_link_rescue.archive import read_archive, repair_archive, serialize, write_zip, xml, MAX_ENTRY_BYTES
from bcf_link_rescue.cli import main
from bcf_link_rescue.model import InputError, inspect_model, match_components, validate_inventory
from bcf_link_rescue.report import csv_text, html_text


@pytest.fixture()
def sample(tmp_path):
    expected = generate(tmp_path)
    return tmp_path, expected, inspect_model(tmp_path / "old.ifc"), inspect_model(tmp_path / "new.ifc")


def test_main_sample_counts_and_one_to_one(sample):
    folder, expected, old, new = sample
    _, report = repair_archive(old, new, (folder / "issues.bcfzip").read_bytes())
    assert len(old["products"]) == expected["old_products"]
    assert len(new["products"]) == expected["new_products"]
    assert report["summary"]["status_counts"] == expected["status_counts"]
    assert report["summary"]["unique_status_counts"] == expected["unique_status_counts"]
    actual = {r["old_guid"]: r["new_guid"] for r in report["references"] if r["status"] == "rebound"}
    assert actual == expected["mapping"]
    assert len(set(actual.values())) == len(actual)
    assert report["draft"] is True


def test_generated_text_and_nested_archives_use_lf(sample):
    folder, _, _, _ = sample
    def inspect(name, raw):
        if name.endswith((".zip", ".bcfzip")):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for entry in archive.infolist():
                    if not entry.is_dir():
                        inspect(name + "/" + entry.filename, archive.read(entry))
        elif name.endswith((".ifc", ".json", ".html", ".csv", ".bcf", ".bcfv", ".version")):
            assert b"\r" not in raw, name
    for path in folder.rglob("*"):
        if path.is_file():
            inspect(path.name, path.read_bytes())


@pytest.mark.parametrize("schema", ["IFC2X3", "IFC4"])
def test_real_ifc_schema_valid_nested_rotation_units_and_repair(tmp_path, schema):
    old_ids = create_model(tmp_path / "old.ifc", schema=schema)
    create_model(tmp_path / "new.ifc", schema=schema, new=True)
    for filename in ("old.ifc", "new.ifc"):
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(ifcopenshell.open(str(tmp_path / filename)), logger, express_rules=True)
        assert not logger.statements, logger.statements
    old, new = inspect_model(tmp_path / "old.ifc"), inspect_model(tmp_path / "new.ifc")
    assert old["length_unit_m"] == 0.001 and new["length_unit_m"] == 1
    resolver, frame_ok = match_components(old, new)
    assert frame_ok and resolver(old_ids["nested"])["status"] == "rebound"
    product = next(p for p in old["products"] if p["tag"] == "C-102")
    assert product["placement"]["translation_m"] == pytest.approx([5, 3, 0])
    assert product["placement"]["rotation"] == pytest.approx([0, -1, 0, 1, 0, 0, 0, 0, 1], abs=1e-12)


def test_preserve_immutable_viewpoint_comment_camera_snapshot_and_unresolved(sample):
    folder, expected, old, new = sample
    raw = (folder / "issues.bcfzip").read_bytes()
    before = read_archive(raw)
    fixed, report = repair_archive(old, new, raw)
    after = read_archive(fixed)
    for name in before:
        if not name.endswith("markup.bcf"):
            assert before[name] == after[name]
    original_markup = xml(before[f"{TOPIC_ID}/markup.bcf"], "original")
    new_markup = xml(after[f"{TOPIC_ID}/markup.bcf"], "updated")
    assert serialize(original_markup.find("Topic")) == serialize(new_markup.find("Topic"))
    assert serialize(original_markup.find("Comment")) == serialize(new_markup.find("Comment"))
    assert new_markup.find("Comment/Viewpoint").get("Guid") == VIEW_ID
    assert len(new_markup.findall("Viewpoints")) == 2
    original = xml(before[f"{TOPIC_ID}/viewpoint.bcfv"], "original")
    clone = xml(after[report["viewpoints"][0]["draft_file"]], "clone")
    assert clone.get("Guid") != VIEW_ID
    assert serialize(original.find("PerspectiveCamera")) == serialize(clone.find("PerspectiveCamera"))
    old_refs = [n.get("IfcGuid") for n in original.findall(".//Component")]
    new_refs = [n.get("IfcGuid") for n in clone.findall(".//Component")]
    assert new_refs == [expected["mapping"].get(guid, guid) for guid in old_refs]
    for a, b in zip(original.findall(".//Component"), clone.findall(".//Component")):
        assert a.findtext("AuthoringToolId") == b.findtext("AuthoringToolId")
        assert a.findtext("OriginatingSystem") == b.findtext("OriginatingSystem")


def test_deterministic_same_inputs_no_mutation(sample):
    folder, _, old, new = sample
    raw = (folder / "issues.bcfzip").read_bytes()
    saved_old, saved_new = copy.deepcopy(old), copy.deepcopy(new)
    assert repair_archive(old, new, raw) == repair_archive(old, new, raw)
    assert old == saved_old and new == saved_new
    assert raw == (folder / "issues.bcfzip").read_bytes()


def test_model_frame_shift_and_georef_shift_stop_rebinding(tmp_path):
    ids = create_model(tmp_path / "old.ifc")
    create_model(tmp_path / "shifted.ifc", new=True, context_shift=100)
    old = inspect_model(tmp_path / "old.ifc")
    resolve, compatible = match_components(old, inspect_model(tmp_path / "shifted.ifc"))
    assert compatible is False and resolve(ids["wall"])["status"] == "frame_changed"
    create_model(tmp_path / "old-map.ifc", new=True, georef_shift=0)
    create_model(tmp_path / "new-map.ifc", new=True, georef_shift=100)
    resolve, compatible = match_components(inspect_model(tmp_path / "old-map.ifc"), inspect_model(tmp_path / "new-map.ifc"))
    assert not compatible and resolve(gid("wall-new"))["status"] == "frame_changed"


@pytest.mark.parametrize("pset_name", ["ePSet_MapConversion", "ePSet_ProjectedCRS"])
def test_unsupported_ifc2x3_georef_rejected(tmp_path, pset_name):
    path = tmp_path / "model.ifc"
    create_model(path, schema="IFC2X3")
    model = ifcopenshell.open(str(path))
    model.create_entity("IfcPropertySet", GlobalId=gid(pset_name), OwnerHistory=model.by_type("IfcOwnerHistory")[0], Name=pset_name, HasProperties=[model.create_entity("IfcPropertySingleValue", Name="Eastings", NominalValue=model.create_entity("IfcReal", 100.0))])
    model.write(str(path))
    with pytest.raises(InputError, match="Property-set georeferencing"):
        inspect_model(path)


@pytest.mark.parametrize("change,status", [("new_duplicate", "ambiguous_new"), ("orientation", "moved"), ("missing_placement", "placement_unavailable"), ("near", "rebound"), ("case_tag", "removed"), ("space_tag", "removed")])
def test_strict_matching_new_duplicate_orientation_and_exact_tag(sample, change, status):
    _, _, old, new = sample
    source = next(p for p in old["products"] if p["tag"] == "W-101")
    target = next(p for p in new["products"] if p["tag"] == "W-101")
    if change == "new_duplicate":
        duplicate = copy.deepcopy(target); duplicate["global_id"] = gid("extra"); new["products"].append(duplicate)
    elif change == "orientation":
        target["placement"]["rotation"] = [0, -1, 0, 1, 0, 0, 0, 0, 1]
    elif change == "missing_placement":
        target["placement"] = None
    elif change == "near":
        target["placement"]["translation_m"][0] += 0.0000005
    elif change == "case_tag":
        target["tag"] = "w-101"
    elif change == "space_tag":
        target["tag"] = " W-101 "
    resolve, _ = match_components(old, new)
    assert resolve(source["global_id"])["status"] == status


def test_globalid_retained_but_moved_is_review(sample):
    _, _, old, new = sample
    same = next(p for p in new["products"] if p["tag"] == "STAY-001")
    same["placement"]["translation_m"][0] += 1
    resolve, _ = match_components(old, new)
    result = resolve(same["global_id"])
    assert result["status"] == "retained_review" and result["new_guid"] == same["global_id"]


@pytest.mark.parametrize("problem", ["duplicate", "invalid", "rotation", "nan", "version"])
def test_invalid_inventory_no_recovery(sample, problem):
    _, _, old, _ = sample
    if problem == "duplicate": old["products"].append(copy.deepcopy(old["products"][0]))
    if problem == "invalid": old["products"][0]["global_id"] = "bad-guid"
    if problem == "rotation": old["products"][0]["placement"]["rotation"][0] = 2
    if problem == "nan": old["products"][0]["placement"]["translation_m"][0] = float("nan")
    if problem == "version": old["format"] = "unknown"
    with pytest.raises(InputError): validate_inventory(old)


@pytest.mark.parametrize("problem", ["duplicate", "invalid"])
def test_invalid_real_ifc_globalids(tmp_path, problem):
    path = tmp_path / "input.ifc"; create_model(path)
    model = ifcopenshell.open(str(path)); products = model.by_type("IfcWall")
    products[1].GlobalId = products[0].GlobalId if problem == "duplicate" else "INVALID"
    model.write(str(path))
    with pytest.raises(InputError, match="GlobalId"):
        inspect_model(path)


@pytest.mark.parametrize("name", ["../bad", "/root", "C:/bad", "topic/../bad", "topic\\bad", "./bad", "a//b"])
def test_unsafe_zip_names_rejected(name):
    normalized = name.replace("\\", "/")
    raw = write_zip({normalized: b"x"})
    if name != normalized:
        raw = raw.replace(normalized.encode(), name.encode())
    with pytest.raises(InputError, match="Unsafe ZIP"):
        read_archive(raw)


def test_duplicate_casefold_zip_names_rejected():
    with pytest.raises(InputError, match="Duplicate ZIP"):
        read_archive(write_zip({"a.xml": b"x", "A.xml": b"y"}))


def test_duplicate_zip_names_rejected():
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("same", "a"); archive.writestr("same", "b")
    with pytest.raises(InputError, match="Duplicate ZIP"): read_archive(buffer.getvalue())


def test_bomb_encryption_oversize_badzip_rejected():
    with pytest.raises(InputError, match="ratio"):
        read_archive(write_zip({"bomb": b"a" * 1000000}))
    raw = bytearray(write_zip({"entry": b"value"}))
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, central + 8, 1)
    with pytest.raises(InputError, match="Encrypted"): read_archive(bytes(raw))
    raw = bytearray(write_zip({"entry": b"value"})); central = raw.index(b"PK\x01\x02")
    struct.pack_into("<I", raw, central + 24, MAX_ENTRY_BYTES + 1)
    with pytest.raises(InputError, match="size"): read_archive(bytes(raw))
    with pytest.raises(InputError, match="Invalid BCF ZIP"): read_archive(b"not a ZIP")


@pytest.mark.parametrize("raw", [b'<!DOCTYPE x [<!ENTITY a "EXPAND">]><x>&a;</x>', '<!DOCTYPE x [<!ENTITY a "EXPAND">]><x>&a;</x>'.encode('utf-16'), b'<x><y></x>', ('<x>'*102 + '</x>'*102).encode()])
def test_xml_entities_malformed_depth_rejected(raw):
    with pytest.raises(InputError): xml(raw, "unsafe.xml")


@pytest.mark.parametrize("problem", ["version", "missing_view", "remote_view", "mismatch_id", "missing_snapshot"])
def test_unsupported_or_broken_bcf_rejected(sample, problem):
    folder, _, old, new = sample
    entries = read_archive((folder / "issues.bcfzip").read_bytes())
    if problem == "version": entries["bcf.version"] = b'<Version VersionId="3.0"/>'
    if problem == "missing_view": del entries[f"{TOPIC_ID}/viewpoint.bcfv"]
    if problem == "missing_snapshot": del entries[f"{TOPIC_ID}/snapshot.png"]
    if problem in ("remote_view", "mismatch_id"):
        root = xml(entries[f"{TOPIC_ID}/markup.bcf"], "test")
        if problem == "remote_view": root.find("Viewpoints/Viewpoint").text = "https://example.invalid/view.bcfv"
        else: root.find("Viewpoints").set("Guid", str(uuid.uuid4()))
        entries[f"{TOPIC_ID}/markup.bcf"] = serialize(root)
    with pytest.raises(InputError): repair_archive(old, new, write_zip(entries))


def test_report_escaping_csv_formula_and_no_active_user_html(sample):
    folder, _, old, new = sample
    _, report = repair_archive(old, new, (folder / "issues.bcfzip").read_bytes())
    report["references"][0]["tag"] = '<script>alert(1)</script>'
    report["references"][0]["topic_title"] = ' =HYPERLINK("https://example.invalid")'
    markup = html_text(report)
    assert '<script>' not in markup and '&lt;script&gt;' in markup
    assert "' =HYPERLINK" in csv_text(report)
    assert 'SHA-256' not in markup


def test_cli_real_ifc_end_to_end_and_protect_outputs(sample, capsys):
    folder, _, _, _ = sample
    output = folder / "cli-output"
    command = ["repair", str(folder / "old.ifc"), str(folder / "new.ifc"), str(folder / "issues.bcfzip"), "--out", str(output)]
    before = {name: (folder / name).read_bytes() for name in ("old.ifc", "new.ifc", "issues.bcfzip")}
    assert main(command) == 0
    assert {p.name for p in output.iterdir()} == {"rebound.bcfzip", "review.json", "review.csv", "review.html", "old.inventory.json", "new.inventory.json"}
    saved = {p.name: p.read_bytes() for p in output.iterdir()}
    assert main(command) == 2
    assert saved == {p.name: p.read_bytes() for p in output.iterdir()}
    assert before == {name: (folder / name).read_bytes() for name in before}
    capsys.readouterr()


def test_cli_export_and_inventory_repair_protection(sample, capsys):
    folder, _, _, _ = sample
    out = folder / "export.json"
    command = ["inspect-model", str(folder / "old.ifc"), "--out", str(out)]
    assert main(command) == 0
    saved = out.read_bytes(); assert main(command) == 2; assert saved == out.read_bytes()
    assert main(["repair-inventories", str(out), str(folder / "new.inventory.json"), str(folder / "issues.bcfzip"), "--out", str(folder / "from-inventory")]) == 0
    capsys.readouterr()


def test_failed_input_creates_no_output(sample, capsys):
    folder, _, _, _ = sample
    bad = folder / "bad.zip"; bad.write_bytes(b"bad")
    out = folder / "failure-output"
    assert main(["repair", str(folder / "old.ifc"), str(folder / "new.ifc"), str(bad), "--out", str(out)]) == 2
    assert not out.exists()
    capsys.readouterr()


def test_bcf_client_import_if_installed(sample, tmp_path):
    bcf = pytest.importorskip("bcf.v2.bcfxml")
    folder, _, old, new = sample
    fixed, report = repair_archive(old, new, (folder / "issues.bcfzip").read_bytes())
    output = tmp_path / "import.bcfzip"; output.write_bytes(fixed)
    loaded = bcf.BcfXml.load(output)
    assert loaded is not None and len(loaded.topics) == 1
    topic = next(iter(loaded.topics.values()))
    assert len(topic.viewpoints) == 2


def test_official_xsd_if_private_schemas_provided(sample):
    directory = os.environ.get("BCF_XSD_DIR")
    if not directory: pytest.skip("Set BCF_XSD_DIR to an independently obtained official BCF 2.1 Schemas folder.")
    etree = pytest.importorskip("lxml.etree")
    folder, _, old, new = sample
    fixed, _ = repair_archive(old, new, (folder / "issues.bcfzip").read_bytes())
    schemas = {suffix: etree.XMLSchema(etree.parse(str(Path(directory) / filename))) for suffix, filename in ((".bcf", "markup.xsd"), (".bcfv", "visinfo.xsd"), (".version", "version.xsd"))}
    for archive in ((folder / "issues.bcfzip").read_bytes(), fixed):
        for name, data in read_archive(archive).items():
            for suffix, schema in schemas.items():
                if name.endswith(suffix): schema.assertValid(etree.fromstring(data))
