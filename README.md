# BCF Link Rescue

Recover reviewable **BCF 2.1 component links** when an IFC reexport changes `GlobalId` values. The tool keeps the original viewpoints and conversation, then appends new draft viewpoints for references supported by exact model evidence.

[Try the browser workspace](https://tianchaodaxing-beep.github.io/pandao-bcf-link-rescue/) · [Download source](https://github.com/tianchaodaxing-beep/pandao-bcf-link-rescue/archive/refs/heads/main.zip) · [Contact](mailto:tianchaodaxing@gmail.com)

BCF issues often identify model components by their IFC GUIDs. A changed GUID can leave the issue intact while breaking its component selection. BCF Link Rescue handles the specific case where stable Tags still identify the same physical elements in the old and new models. It does not repair the exporting application's GUID behavior.

## Quick start

Use Python 3.10–3.15 on a platform supported by IfcOpenShell 0.9.0. Python 3.11 or 3.12 is recommended. Download and extract the source, enter the project directory, and install it in a virtual environment:

```sh
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate
python -m pip install .
```

Run the complete recovery from real, uncompressed IFC files:

```sh
bcf-link-rescue repair old.ifc new.ifc issues.bcfzip --out review-draft
```

`review-draft` must be a new directory. The command writes:

| File | Purpose |
| --- | --- |
| `rebound.bcfzip` | Original BCF content with appended recovery draft viewpoints |
| `review.html` | Standalone review table with actionable outcomes |
| `review.csv` | Every reference node, with spreadsheet formula protection |
| `review.json` | Complete outcomes and input/output SHA-256 fingerprints |
| `old.inventory.json` | Versioned inventory exported from the original IFC |
| `new.inventory.json` | Versioned inventory exported from the updated IFC |

**Manually load the new IFC in your model viewer, then open the appended draft viewpoints.** Original BCF Header/File metadata remains intact and may still name the old IFC. Original comments continue to reference the original viewpoints. Their component IDs are deliberately preserved. A new viewpoint's higher Index and `rescue-<UUID>.bcfv` filename identify the draft.

Exit code `0` means a review draft was generated, including unresolved rows; it does not mean every reference was repaired. Invalid or unsupported input returns `2`. Cancellation returns `130`. Existing outputs and input bytes are never replaced. A failed filesystem write or cancellation can leave an incomplete new folder; discard that folder or choose another output directory before retrying.

## Browser workflow

The browser accepts **exported inventory JSON**, not raw IFC. The Python CLI performs the IFC parsing:

```sh
bcf-link-rescue inspect-model old.ifc --out old.inventory.json
bcf-link-rescue inspect-model new.ifc --out new.inventory.json
```

Choose both JSON inventories and the original BCF 2.1 archive in the browser workspace. Analyze the inputs, inspect the reference map and review list, then download the BCF draft or the complete review packet. Files are processed locally; there is no upload service, account, API key or model-content network request. The built-in sample fetches only bundled synthetic sample files from the site's origin.

For local use, serve `docs` over HTTP; secure-context hashing requires HTTPS or localhost:

```sh
python -m http.server 8080 --directory docs
```

Open `http://localhost:8080`. The sample button performs a real analysis and generates downloadable files. The reference map is a schematic of component links, not a model-geometry viewer. The table shows up to 200 matching rows; the exported CSV/JSON contains the full set. The map shows up to 16 unique references.

For an inventory-based command line run:

```sh
bcf-link-rescue repair-inventories old.inventory.json new.inventory.json issues.bcfzip --out inventory-review
```

Treat inventories as trusted exports under your control. Their model fingerprints record provenance; the browser cannot attest that an edited JSON file represents the original IFC.

## Matching rules

1. Compare model coordinate contexts, true north, site geographic metadata and IFC4 map conversion metadata conservatively. Changed frame metadata produces review outcomes and prevents automatic rebinding.
2. Retain an exact existing `GlobalId`. If its placement is unavailable/changed or its class changed, keep the reference and flag it for review.
3. For a changed ID, require a **nonempty exact Tag and equal IFC entity class**, unique in both inventories. Matching is case-sensitive and does not trim the Tag. Whitespace-only Tags are unusable.
4. Require equal product placement after nested `IfcLocalPlacement` transforms and SI length units are normalized. Translation tolerance is `0.000001` metre; every rotation matrix element uses tolerance `0.00000001`.
5. Reject an identity takeover where the proposed new ID belongs to another old component. The mapping is one-to-one.
6. Keep every unresolved original reference and report its specific reason. Apply a supported mapping consistently across Selection, Visibility exceptions and Coloring.

Tags must identify the **same physical element** across exports. Exact Tag, class and unchanged placement do not prove unchanged geometry, dimensions, material or design intent. Inspect every appended draft in your actual updated model. This is a link recovery tool, not a full BIM validator, camera repair tool, clash detector, geometry comparator, Revit linked-model fix or identity guarantee for arbitrary exports.

## History preservation

- Original `.bcfv` files remain byte-for-byte unchanged.
- Original snapshots, cameras, comments and comment viewpoint references are preserved.
- Original topic IDs, issue statuses and author records are preserved.
- A changed viewpoint is cloned under a fresh, deterministic UUID and local filename. The cloned reference is appended to its topic's markup.
- The clone changes its own viewpoint UUID and supported `Component/IfcGuid` attributes. Original per-component originating-system and authoring-tool metadata is kept for review provenance.
- Markup XML may be reserialized when a viewpoint entry is appended. Topic/comment values remain intact; markup bytes are not claimed to remain identical.
- The repaired archive is always a **review draft**. Appended viewpoints can still contain unresolved references, which remain in the review table.

## Supported inputs and limits

| Input | Support |
| --- | --- |
| IFC | Uncompressed STEP `.ifc`, IFC2X3 or IFC4; one project; one explicit SI metre length unit with optional prefix; up to 250 MiB and 200000 products |
| Placement | Nested `IfcLocalPlacement` with 2D/3D axis placements, normalized translation and orientation; unavailable/unsupported product placements are review outcomes |
| Georeferencing | IFC4 coordinate-operation metadata is compared conservatively; different raw map-conversion metadata prevents rebinding even when a human might determine equivalence |
| Property-set georeferencing | `ePSet_MapConversion` / `ePSet_ProjectedCRS` are explicitly rejected; they are not silently ignored |
| BCF | Version 2.1; local viewpoint and snapshot files; stored or deflated ZIP entries |
| BCF batch | 100 MiB compressed, 250 MiB expanded, 20000 entries, 20 MiB per entry, compression ratio up to 100, 200000 reference nodes |
| XML | 5 MiB per XML entry; at most 100 nesting levels and 100000 elements; topic titles up to 4096 characters; no DTD/entities or XML namespaces |
| XML encoding | CLI: UTF-8 or BOM-marked UTF-16/32. Browser: UTF-8 or BOM-marked UTF-16; use the CLI for UTF-32 |
| Inventory upload | Version `bcf-link-rescue-inventory/1`; 100 MiB per JSON file |

Invalid or duplicate IFC product GlobalIds block the run. Other explicit outcomes include absent old IDs, missing Tags, duplicate keys, removed elements, class changes, placement changes and identity conflicts. Coordinate contexts must be present. CRS and placement equivalence is intentionally conservative; no fuzzy name matching is used.

ZIP traversal paths, absolute paths, duplicate names, encrypted entries, symbolic links, unsupported compression, oversized entries and compression bombs are rejected. The browser checks local/central ZIP metadata, expansion sizes and CRCs. XML is parsed without remote fetching. BCF web references are never fetched or rendered. Review text is escaped, HTML reports have restrictive content-security policy, and spreadsheet formula prefixes are neutralized.

## Sample and verification

All bundled sample data is synthetic. `scripts/generate_samples.py` creates real IFC geometry with IfcOpenShell, then derives inventories and a valid BCF 2.1 archive. It generates a 9-product old IFC4, an 8-product new IFC4, 10 unique BCF references and 30 component reference nodes across all three reference groups. Expected outcomes are in `docs/samples/expected.json`:

| Outcome | Unique references | Reference nodes |
| --- | ---: | ---: |
| Rebound | 2 | 6 |
| Retained | 1 | 3 |
| Duplicate old Tag | 2 | 6 |
| Missing Tag | 1 | 3 |
| Removed | 1 | 3 |
| Placement changed | 1 | 3 |
| IFC class changed | 1 | 3 |
| Absent from old model | 1 | 3 |

The tests generate IFC2X3 and IFC4 fixtures independently, run IfcOpenShell schema/EXPRESS validation, check rotated nested placements and millimetre/metre conversions, georeferencing changes, matching limits, unsafe ZIP/XML, report escaping, input/output protection and deterministic repeat runs. Optional BCF client import and independently obtained official BCF 2.1 XSD checks are separate validations. No commercial CAD application compatibility is claimed.

```sh
python -m pip install pytest==8.4.2 bcf-client==0.9.0
python -m pytest -q
node tests/test_browser_core.cjs
python scripts/generate_samples.py --out regenerated-samples
```

For optional official XSD validation, install `lxml` and set `BCF_XSD_DIR` to your separately obtained buildingSMART BCF-XML `release_2_1/Schemas` directory. The official schemas are not redistributed here. CI is configured for Windows and Linux with Python 3.11 and 3.12; its status should be checked in the repository's Actions page after a push.

## Dependencies and license

Application code is MIT licensed. IFC parsing uses the official [`ifcopenshell==0.9.0`](https://pypi.org/project/ifcopenshell/0.9.0/) package, licensed LGPL-3.0-or-later. The application does not bundle an IfcOpenShell binary. The browser includes [`fflate` 0.8.3](https://www.npmjs.com/package/fflate/v/0.8.3), MIT licensed, with its license in `docs/vendor/LICENSE-fflate`. See [THIRD_PARTY.md](THIRD_PARTY.md) for source links and notices.

## Contact

Need project-specific migration rules, batch workflow integration or supervised recovery review? Contact PANDAO at [tianchaodaxing@gmail.com](mailto:tianchaodaxing@gmail.com).
