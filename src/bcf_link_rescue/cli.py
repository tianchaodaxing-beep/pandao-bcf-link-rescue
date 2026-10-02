"""Command line entry point. Never replace inputs or existing outputs."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .archive import MAX_ARCHIVE_BYTES, repair_archive
from .model import InputError, inspect_model, validate_inventory
from .report import csv_text, html_text, json_text


def _load_inventory(path):
    path = Path(path)
    if path.stat().st_size > 100 * 1024 * 1024:
        raise InputError("Inventory exceeds the 100 MiB limit.")
    return validate_inventory(json.loads(path.read_text(encoding="utf-8")))


def _packet(old, new, archive_path, output_path):
    source = Path(archive_path)
    output = Path(output_path)
    if output.exists():
        raise InputError("Output directory already exists. Choose a new directory.")
    if not source.is_file() or source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise InputError("BCF input is missing or exceeds the 100 MiB limit.")
    revised, report = repair_archive(old, new, source.read_bytes())
    # Compute all results before creating any output directory.
    files = {"rebound.bcfzip": revised,
             "review.json": json_text(report).encode("utf-8"),
             "review.csv": csv_text(report).encode("utf-8"),
             "review.html": html_text(report).encode("utf-8"),
             "old.inventory.json": json_text(old).encode("utf-8"),
             "new.inventory.json": json_text(new).encode("utf-8")}
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        with (output / name).open("xb") as handle:
            handle.write(data)
    print(json.dumps({"result": "review_draft", "output": str(output), "summary": report["summary"]}, ensure_ascii=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recover BCF 2.1 component links locally; output a review draft.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect-model", help="Export a versioned inventory from a real IFC2X3 or IFC4 file.")
    inspect.add_argument("model")
    inspect.add_argument("--out", required=True, help="New inventory JSON file; existing files are never replaced.")
    repair = sub.add_parser("repair", help="Inspect two real IFC files and recover a BCF archive in one command.")
    repair.add_argument("old_model")
    repair.add_argument("new_model")
    repair.add_argument("issues")
    repair.add_argument("--out", required=True, help="New output directory.")
    inventories = sub.add_parser("repair-inventories", help="Use inventories previously exported by inspect-model.")
    inventories.add_argument("old_inventory")
    inventories.add_argument("new_inventory")
    inventories.add_argument("issues")
    inventories.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect-model":
            target = Path(args.out)
            if target.exists():
                raise InputError("Inventory output already exists. Choose a new file.")
            inventory = inspect_model(args.model)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json_text(inventory))
            print(json.dumps({"result": "inventory_exported", "products": len(inventory["products"]), "output": str(target)}, ensure_ascii=True))
            return 0
        if args.command == "repair":
            old, new = inspect_model(args.old_model), inspect_model(args.new_model)
        else:
            old, new = _load_inventory(args.old_inventory), _load_inventory(args.new_inventory)
        return _packet(old, new, args.issues, args.out)
    except (InputError, OSError, json.JSONDecodeError) as error:
        print(f"Input or output error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled. Discard any incomplete output directory before retrying.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
