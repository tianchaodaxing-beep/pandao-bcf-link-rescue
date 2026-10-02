"""Generate real, schema-valid synthetic IFC/BCF fixtures, not hand-written inventories."""

import argparse
import base64
import json
import math
import sys
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

import ifcopenshell
import ifcopenshell.guid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bcf_link_rescue.archive import serialize, write_zip, repair_archive
from bcf_link_rescue.model import inspect_model
from bcf_link_rescue.report import csv_text, html_text, json_text

TOPIC_ID = "196790c8-a390-50bd-af2c-3b8f17de314a"
VIEW_ID = "4931e1ef-3a68-5572-a154-f323a749a531"


def gid(label):
    return ifcopenshell.guid.compress(uuid.uuid5(uuid.NAMESPACE_URL, "bcf-link-rescue-sample:" + label).hex)


def create_model(path, schema="IFC4", new=False, context_shift=0, georef_shift=None):
    model = ifcopenshell.file(schema=schema)
    model.header.file_name.time_stamp = "2024-01-01T00:00:00"
    person = model.create_entity("IfcPerson", **{("Id" if schema == "IFC2X3" else "Identification"): "sample"}, FamilyName="Sample")
    organization = model.create_entity("IfcOrganization", Name="Synthetic fixtures")
    user = model.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=organization)
    application = model.create_entity("IfcApplication", ApplicationDeveloper=organization, Version="0.1.0", ApplicationFullName="BCF Link Rescue fixtures", ApplicationIdentifier="BCFLR")
    history = model.create_entity("IfcOwnerHistory", OwningUser=user, OwningApplication=application, ChangeAction="ADDED", LastModifiedDate=1704067200, LastModifyingUser=user, LastModifyingApplication=application, CreationDate=1704067200)
    unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix=None if new else "MILLI", Name="METRE")
    units = model.create_entity("IfcUnitAssignment", Units=[unit])
    scale = 1.0 if new else 1000.0

    def axis(xyz=(0, 0, 0), angle=0):
        point = model.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in xyz))
        z = model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
        x = model.create_entity("IfcDirection", DirectionRatios=(math.cos(angle), math.sin(angle), 0.0))
        return model.create_entity("IfcAxis2Placement3D", Location=point, Axis=z, RefDirection=x)

    context = model.create_entity("IfcGeometricRepresentationContext", ContextIdentifier="Model", ContextType="Model", CoordinateSpaceDimension=3, Precision=0.000001*scale, WorldCoordinateSystem=axis((context_shift*scale, 0, 0)))
    project = model.create_entity("IfcProject", GlobalId=gid("project-new" if new else "project-old"), OwnerHistory=history, Name="Synthetic coordination model", RepresentationContexts=[context], UnitsInContext=units)
    if georef_shift is not None:
        if schema != "IFC4":
            raise ValueError("IFC4 is required for map conversion fixtures.")
        target_unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
        crs = model.create_entity("IfcProjectedCRS", Name="EPSG:32632", MapUnit=target_unit)
        model.create_entity("IfcMapConversion", SourceCRS=context, TargetCRS=crs, Eastings=500000.0+georef_shift, Northings=0.0, OrthogonalHeight=0.0, XAxisAbscissa=1.0, XAxisOrdinate=0.0, Scale=1.0/scale)
    product_specs = [
        ("stay", "IfcWall", "STAY-001", (0, 0, 0)),
        ("wall", "IfcWall", "W-101", (2, 0, 0)),
        ("nested", "IfcColumn", "C-102", (5, 3, 0)),
        ("dup-a", "IfcWall", "D-103", (3, 0, 0)),
        ("dup-b", "IfcWall", "D-103", (4, 0, 0)),
        ("no-tag", "IfcWall", None, (6, 0, 0)),
        ("removed", "IfcWall", "D-104", (7, 0, 0)),
        ("moved", "IfcWall", "M-105", (8 if not new else 8.1, 0, 0)),
        ("changed-class", "IfcWall" if not new else "IfcBeam", "T-106", (9, 0, 0)),
    ]
    ids = {}
    for label, entity_class, tag, position in product_specs:
        if new and label == "removed":
            continue
        identifier = gid(label + ("-new" if new and label != "stay" else "-old"))
        if label == "nested":
            parent = model.create_entity("IfcLocalPlacement", RelativePlacement=axis((5*scale, 2*scale, 0), math.pi/2))
            if schema == "IFC2X3":
                building = model.create_entity("IfcBuilding", GlobalId=gid("nested-building"), OwnerHistory=history, Name="Nested placement parent", ObjectPlacement=parent, CompositionType="ELEMENT")
                model.create_entity("IfcRelAggregates", GlobalId=gid("project-building"), OwnerHistory=history, RelatingObject=project, RelatedObjects=[building])
            placement = model.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=axis((scale, 0, 0)))
        else:
            placement = model.create_entity("IfcLocalPlacement", RelativePlacement=axis(tuple(v*scale for v in position)))
        origin2 = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0))
        profile_position = model.create_entity("IfcAxis2Placement2D", Location=origin2)
        profile = model.create_entity("IfcRectangleProfileDef", ProfileType="AREA", Position=profile_position, XDim=0.2*scale, YDim=1.0*scale)
        solid = model.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=axis(), ExtrudedDirection=model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)), Depth=3.0*scale)
        shape = model.create_entity("IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=[solid])
        definition = model.create_entity("IfcProductDefinitionShape", Representations=[shape])
        model.create_entity(entity_class, GlobalId=identifier, OwnerHistory=history, Name=label, ObjectPlacement=placement, Representation=definition, Tag=tag)
        ids[label] = identifier
    model.write(str(path))
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    return ids


def create_bcf(path, ids, title="Coordinate wall opening", topic_id=TOPIC_ID, view_id=VIEW_ID):
    ids = list(ids.values()) + [gid("unknown-old")]
    viewpoint = ET.Element("VisualizationInfo", Guid=view_id)
    components = ET.SubElement(viewpoint, "Components")
    selection = ET.SubElement(components, "Selection")
    visibility = ET.SubElement(components, "Visibility", DefaultVisibility="true")
    exceptions = ET.SubElement(visibility, "Exceptions")
    coloring = ET.SubElement(components, "Coloring")
    color = ET.SubElement(coloring, "Color", Color="FF9B51")
    for group in (selection, exceptions, color):
        for identifier in ids:
            component = ET.SubElement(group, "Component", IfcGuid=identifier)
            ET.SubElement(component, "OriginatingSystem").text = "Synthetic fixture"
            ET.SubElement(component, "AuthoringToolId").text = identifier
    camera = ET.SubElement(viewpoint, "PerspectiveCamera")
    for name, values in (("CameraViewPoint", (15, 15, 10)), ("CameraDirection", (-0.6, -0.6, -0.5291502622)), ("CameraUpVector", (-0.3741657387, -0.3741657387, 0.8485281374))):
        point = ET.SubElement(camera, name)
        for coordinate, value in zip(("X", "Y", "Z"), values):
            ET.SubElement(point, coordinate).text = str(value)
    ET.SubElement(camera, "FieldOfView").text = "60"
    markup = ET.Element("Markup")
    topic = ET.SubElement(markup, "Topic", Guid=topic_id, TopicType="Issue", TopicStatus="Open")
    ET.SubElement(topic, "Title").text = title
    ET.SubElement(topic, "CreationDate").text = "2024-01-01T00:00:00Z"
    ET.SubElement(topic, "CreationAuthor").text = "fixture@example.invalid"
    ET.SubElement(topic, "Description").text = "Synthetic test only. Validate the recovery draft in the updated model."
    comment = ET.SubElement(markup, "Comment", Guid="46ab3e3c-e80f-51d2-aec9-35e1fab3d413")
    ET.SubElement(comment, "Date").text = "2024-01-01T00:01:00Z"
    ET.SubElement(comment, "Author").text = "fixture@example.invalid"
    ET.SubElement(comment, "Comment").text = "Original comment must continue to reference the original viewpoint."
    ET.SubElement(comment, "Viewpoint", Guid=view_id)
    reference = ET.SubElement(markup, "Viewpoints", Guid=view_id)
    ET.SubElement(reference, "Viewpoint").text = "viewpoint.bcfv"
    ET.SubElement(reference, "Snapshot").text = "snapshot.png"
    ET.SubElement(reference, "Index").text = "1"
    version = ET.Element("Version", VersionId="2.1")
    ET.SubElement(version, "DetailedVersion").text = "2.1"
    snapshot = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    entries = {"bcf.version": serialize(version), f"{topic_id}/markup.bcf": serialize(markup),
               f"{topic_id}/viewpoint.bcfv": serialize(viewpoint), f"{topic_id}/snapshot.png": snapshot}
    path.write_bytes(write_zip(entries))
    return entries


def generate(destination):
    destination.mkdir(parents=True, exist_ok=True)
    ids = create_model(destination / "old.ifc")
    create_model(destination / "new.ifc", new=True)
    create_bcf(destination / "issues.bcfzip", ids)
    old, new = inspect_model(destination / "old.ifc"), inspect_model(destination / "new.ifc")
    (destination / "old.inventory.json").write_text(json_text(old), encoding="utf-8", newline="\n")
    (destination / "new.inventory.json").write_text(json_text(new), encoding="utf-8", newline="\n")
    bcf, report = repair_archive(old, new, (destination / "issues.bcfzip").read_bytes())
    expected = {"format": "bcf-link-rescue-expected/1", "provenance": "Generated from real IFC files by generate_samples.py using IfcOpenShell 0.9.0. All data is synthetic.",
                "old_products": 9, "new_products": 8, "unique_references": 10, "reference_nodes": 30,
                "groups": {"selection": 10, "visibility": 10, "coloring": 10},
                "unique_status_counts": {"retained": 1, "rebound": 2, "ambiguous_old": 2, "missing_tag": 1, "removed": 1, "moved": 1, "class_changed": 1, "absent_old": 1},
                "status_counts": {"retained": 3, "rebound": 6, "ambiguous_old": 6, "missing_tag": 3, "removed": 3, "moved": 3, "class_changed": 3, "absent_old": 3},
                "cloned_viewpoints": 1,
                "mapping": {ids["wall"]: gid("wall-new"), ids["nested"]: gid("nested-new")},
                "original_topic_id": TOPIC_ID, "original_viewpoint_id": VIEW_ID,
                "review_draft": True}
    (destination / "expected.json").write_text(json_text(expected), encoding="utf-8", newline="\n")
    packet = destination / "example-output"
    packet.mkdir(exist_ok=True)
    (packet / "rebound.bcfzip").write_bytes(bcf)
    (packet / "review.json").write_text(json_text(report), encoding="utf-8", newline="\n")
    (packet / "review.csv").write_text(csv_text(report), encoding="utf-8", newline="\n")
    (packet / "review.html").write_text(html_text(report), encoding="utf-8", newline="\n")
    packet_entries = {p.name: p.read_bytes() for p in packet.iterdir() if p.is_file()}
    (destination / "example-review-packet.zip").write_bytes(write_zip(packet_entries))
    (destination / "sample-inputs.zip").write_bytes(write_zip({name: (destination / name).read_bytes() for name in ("old.ifc", "new.ifc", "old.inventory.json", "new.inventory.json", "issues.bcfzip", "expected.json")}))
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "samples")
    print(json.dumps(generate(parser.parse_args().out), indent=2))
