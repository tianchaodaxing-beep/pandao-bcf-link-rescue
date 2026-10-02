"""Extract conservative IFC identity and placement inventories."""

import hashlib
import json
import math
import re
from pathlib import Path

INVENTORY_VERSION = "bcf-link-rescue-inventory/1"
GUID_RE = re.compile(r"^[0-3][0-9A-Za-z_$]{21}$")
MAX_MODEL_BYTES = 250 * 1024 * 1024
MAX_PRODUCTS = 200000
TRANSLATION_TOLERANCE_M = 0.000001
ROTATION_TOLERANCE = 0.00000001


class InputError(ValueError):
    """An unsupported or unsafe input, with no generated success result."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _numbers(values):
    result = [float(v) for v in values]
    if not all(math.isfinite(v) for v in result):
        raise InputError("Non-finite placement or coordinate values are unsupported.")
    return [0.0 if abs(v) < 1e-12 else v for v in result]


def _placement(placement, scale):
    import ifcopenshell.util.placement

    if placement is None:
        return None, "Missing object placement."
    seen = set()
    cursor = placement
    depth = 0
    while cursor is not None:
        if cursor.id() in seen or depth > 100:
            return None, "Cyclic or overly deep object placement."
        seen.add(cursor.id())
        if not cursor.is_a("IfcLocalPlacement"):
            return None, "Only IfcLocalPlacement is supported."
        relative = cursor.RelativePlacement
        if relative is None or relative.is_a() not in ("IfcAxis2Placement2D", "IfcAxis2Placement3D"):
            return None, "Unsupported relative placement."
        directions = [getattr(relative, name, None) for name in ("Axis", "RefDirection")]
        for direction in directions:
            if direction is not None and sum(float(v) ** 2 for v in direction.DirectionRatios) < 1e-20:
                return None, "Zero-length placement direction."
        if all(d is not None for d in directions):
            a, b = (list(d.DirectionRatios) for d in directions)
            if len(a) == len(b) == 3:
                cross = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
                if sum(v*v for v in cross) < 1e-20:
                    return None, "Parallel placement directions."
        cursor = cursor.PlacementRelTo
        depth += 1
    try:
        matrix = ifcopenshell.util.placement.get_local_placement(placement)
        return {"translation_m": _numbers(matrix[:3, 3] * scale),
                "rotation": _numbers(matrix[:3, :3].flatten())}, None
    except (ValueError, TypeError, IndexError, RecursionError) as error:
        return None, f"Invalid object placement: {error}"


def _frame(model, scale):
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit

    for pset in model.by_type("IfcPropertySet"):
        if (pset.Name or "").casefold() in ("epset_mapconversion", "epset_projectedcrs"):
            raise InputError("Property-set georeferencing (ePSet_MapConversion/ePSet_ProjectedCRS) is unsupported. No links were recovered.")
    contexts = []
    for context in model.by_type("IfcGeometricRepresentationContext", include_subtypes=False):
        matrix = ifcopenshell.util.placement.get_axis2placement(context.WorldCoordinateSystem)
        contexts.append({"type": context.ContextType or "", "dimension": context.CoordinateSpaceDimension,
                         "translation_m": _numbers(matrix[:3, 3] * scale),
                         "rotation": _numbers(matrix[:3, :3].flatten()),
                         "true_north": _numbers(context.TrueNorth.DirectionRatios) if context.TrueNorth else None})
    contexts.sort(key=lambda v: json.dumps(v, sort_keys=True))
    if not contexts:
        raise InputError("At least one geometric representation context is required.")
    sites = []
    for site in model.by_type("IfcSite"):
        sites.append({"latitude": list(site.RefLatitude) if site.RefLatitude else None,
                      "longitude": list(site.RefLongitude) if site.RefLongitude else None,
                      "elevation_m": float(site.RefElevation) * scale if site.RefElevation is not None else None})
    sites.sort(key=lambda v: json.dumps(v, sort_keys=True))
    maps = []
    if model.schema == "IFC4":
        for conversion in model.by_type("IfcCoordinateOperation"):
            if not conversion.is_a("IfcMapConversion"):
                raise InputError("Unsupported coordinate operation.")
            crs = conversion.TargetCRS
            maps.append({"class": conversion.is_a(),
                         "eastings": conversion.Eastings, "northings": conversion.Northings,
                         "height": conversion.OrthogonalHeight,
                         "axis_abscissa": conversion.XAxisAbscissa, "axis_ordinate": conversion.XAxisOrdinate,
                         "scale": conversion.Scale,
                         "crs": {k: getattr(crs, k, None) for k in
                                 ("Name", "Description", "GeodeticDatum", "VerticalDatum", "MapProjection", "MapZone")},
                         "map_unit": str(crs.MapUnit) if getattr(crs, "MapUnit", None) else None})
        for value in maps:
            if value["map_unit"]:
                unit = next(c.TargetCRS.MapUnit for c in model.by_type("IfcMapConversion")
                            if str(c.TargetCRS.MapUnit) == value["map_unit"])
                value["map_unit"] = {"class": unit.is_a(), "type": unit.UnitType,
                                     "name": getattr(unit, "Name", None), "prefix": getattr(unit, "Prefix", None)}
    maps.sort(key=lambda v: json.dumps(v, sort_keys=True))
    return {"contexts": contexts, "sites": sites, "map_conversions": maps}


def inspect_model(path):
    import ifcopenshell
    import ifcopenshell.util.unit

    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_MODEL_BYTES:
        raise InputError("IFC input is missing or exceeds the 250 MiB limit.")
    if path.suffix.lower() != ".ifc":
        raise InputError("Use an uncompressed .ifc STEP file.")
    raw = path.read_bytes()
    if b"ISO-10303-21;" not in raw[:4096]:
        raise InputError("Input is not an IFC STEP file.")
    try:
        model = ifcopenshell.open(str(path))
    except (RuntimeError, ValueError, OSError) as error:
        raise InputError(f"IFC parsing failed: {error}") from error
    if model.schema not in ("IFC2X3", "IFC4"):
        raise InputError("Only IFC2X3 and IFC4 are supported.")
    projects = model.by_type("IfcProject")
    if len(projects) != 1 or projects[0].UnitsInContext is None:
        raise InputError("One IFC project with explicit length units is required.")
    length_units = [u for u in projects[0].UnitsInContext.Units if getattr(u, "UnitType", None) == "LENGTHUNIT"]
    if len(length_units) != 1 or not length_units[0].is_a("IfcSIUnit") or length_units[0].Name != "METRE":
        raise InputError("One SI metre length unit (with optional prefix) is required.")
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    if not math.isfinite(scale) or scale <= 0:
        raise InputError("Invalid length unit scale.")
    products = model.by_type("IfcProduct")
    if len(products) > MAX_PRODUCTS:
        raise InputError("Model exceeds the 200000 product limit.")
    items = []
    for product in products:
        placement, problem = _placement(product.ObjectPlacement, scale)
        items.append({"global_id": product.GlobalId, "class": product.is_a(),
                      "tag": getattr(product, "Tag", None), "placement": placement, "placement_error": problem})
    inventory = {"format": INVENTORY_VERSION, "schema": model.schema,
                 "source": {"name": path.name, "sha256": sha256(raw)},
                 "length_unit_m": scale, "frame": _frame(model, scale),
                 "products": sorted(items, key=lambda v: str(v["global_id"]))}
    validate_inventory(inventory)
    return inventory


def validate_inventory(inventory):
    if not isinstance(inventory, dict) or inventory.get("format") != INVENTORY_VERSION:
        raise InputError("Unsupported inventory format. Export inventories with inspect-model.")
    if inventory.get("schema") not in ("IFC2X3", "IFC4"):
        raise InputError("Unsupported inventory IFC schema.")
    source = inventory.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("name"), str) or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
        raise InputError("Inventory source fingerprint is invalid.")
    if not isinstance(inventory.get("frame"), dict) or not inventory["frame"].get("contexts"):
        raise InputError("Inventory coordinate frame is missing.")
    try:
        json.dumps(inventory["frame"], allow_nan=False)
    except (ValueError, TypeError) as error:
        raise InputError("Invalid inventory coordinate frame.") from error
    products = inventory.get("products")
    if not isinstance(products, list) or len(products) > MAX_PRODUCTS:
        raise InputError("Inventory product list is missing or too large.")
    seen = set()
    for product in products:
        if not isinstance(product, dict):
            raise InputError("Invalid inventory product.")
        guid = product.get("global_id")
        if not isinstance(guid, str) or not GUID_RE.fullmatch(guid):
            raise InputError("Inventory contains an invalid GlobalId.")
        if guid in seen:
            raise InputError("Inventory contains duplicate GlobalIds.")
        seen.add(guid)
        if not isinstance(product.get("class"), str) or not re.fullmatch(r"Ifc[A-Za-z0-9]+", product["class"]):
            raise InputError("Invalid inventory entity class.")
        tag = product.get("tag")
        if tag is not None and (not isinstance(tag, str) or len(tag) > 4096):
            raise InputError("Invalid inventory Tag.")
        placement = product.get("placement")
        if placement is not None:
            if not isinstance(placement, dict):
                raise InputError("Invalid inventory placement.")
            for key, length in (("translation_m", 3), ("rotation", 9)):
                values = placement.get(key)
                if not isinstance(values, list) or len(values) != length or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    raise InputError("Invalid inventory placement values.")
            r = placement["rotation"]
            for row in range(3):
                for other in range(3):
                    dot = sum(r[row*3+k]*r[other*3+k] for k in range(3))
                    if abs(dot - (1 if row == other else 0)) > 1e-6:
                        raise InputError("Inventory orientation is not orthonormal.")
            determinant = r[0]*(r[4]*r[8]-r[5]*r[7])-r[1]*(r[3]*r[8]-r[5]*r[6])+r[2]*(r[3]*r[7]-r[4]*r[6])
            if abs(determinant - 1) > 1e-6:
                raise InputError("Inventory orientation must be a proper rotation.")
    return inventory


def placements_equal(a, b):
    return a is not None and b is not None and all(
        abs(x-y) <= tolerance for key, tolerance in
        (("translation_m", TRANSLATION_TOLERANCE_M), ("rotation", ROTATION_TOLERANCE))
        for x, y in zip(a[key], b[key]))


def frames_equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(frames_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(frames_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return abs(a-b) <= 1e-9
    return a == b


def match_components(old, new):
    validate_inventory(old)
    validate_inventory(new)
    old_ids = {p["global_id"]: p for p in old["products"]}
    new_ids = {p["global_id"]: p for p in new["products"]}
    old_keys, new_keys, new_tags = {}, {}, {}
    for inventory, index in ((old, old_keys), (new, new_keys)):
        for product in inventory["products"]:
            if product.get("tag") and product["tag"].strip():
                index.setdefault((product["class"], product["tag"]), []).append(product)
    for product in new["products"]:
        new_tags.setdefault(product.get("tag"), []).append(product)
    frame_ok = frames_equal(old["frame"], new["frame"])

    def resolve(guid):
        item = old_ids.get(guid)
        result = {"old_guid": guid, "new_guid": guid, "class": item["class"] if item else "",
                  "tag": (item.get("tag") or "") if item else ""}
        def outcome(status, detail, target=None):
            result.update(status=status, detail=detail)
            if target is not None:
                result["new_guid"] = target["global_id"]
            return result
        if not GUID_RE.fullmatch(guid):
            return outcome("invalid_reference", "BCF reference is not a valid IFC GlobalId.")
        if item is None:
            return outcome("absent_old", "Reference is absent from the old IFC inventory.")
        if not frame_ok:
            return outcome("frame_changed", "Coordinate frame or georeferencing changed. Review the model frame first.")
        if guid in new_ids:
            if not placements_equal(item["placement"], new_ids[guid]["placement"]):
                return outcome("retained_review", "GlobalId still exists, but placement is changed or unavailable.")
            if item["class"] != new_ids[guid]["class"]:
                return outcome("retained_review", "GlobalId still exists, but entity class changed.")
            return outcome("retained", "GlobalId still exists in the new IFC.")
        if not item.get("tag") or not item["tag"].strip():
            return outcome("missing_tag", "No nonempty exact Tag is available.")
        key = (item["class"], item["tag"])
        if len(old_keys[key]) != 1:
            return outcome("ambiguous_old", "Class + Tag is duplicated in the old model.")
        candidates = new_keys.get(key, [])
        if not candidates:
            if new_tags.get(item["tag"]):
                return outcome("class_changed", "Tag exists in the new model under a different IFC class.")
            return outcome("removed", "No component with this class + Tag exists in the new model.")
        if len(candidates) != 1:
            return outcome("ambiguous_new", "Class + Tag is duplicated in the new model.")
        target = candidates[0]
        if target["global_id"] in old_ids and target["global_id"] != guid:
            return outcome("identity_conflict", "Candidate GlobalId belongs to another old component.")
        if item["placement"] is None or target["placement"] is None:
            return outcome("placement_unavailable", "A supported placement is required in both models.")
        if not placements_equal(item["placement"], target["placement"]):
            return outcome("moved", "Translation or orientation changed beyond the strict tolerance.")
        return outcome("rebound", "Unique exact class + Tag and unchanged normalized placement.", target)
    return resolve, frame_ok
