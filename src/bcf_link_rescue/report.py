"""Safe, standalone reports for the recovery review draft."""

import csv
import html
import io
import json

FIELDS = ["topic_id", "topic_title", "viewpoint_id", "group", "position", "status", "class", "tag", "old_guid", "new_guid", "detail"]


def csv_text(report):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in report["references"]:
        safe = {}
        for key in FIELDS:
            value = str(row.get(key, ""))
            if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return buffer.getvalue()


def html_text(report):
    esc = lambda value: html.escape(str(value), quote=True)
    rows = "".join("<tr>" + "".join(f"<td>{esc(row.get(k, ''))}</td>" for k in ("status", "tag", "class", "group", "old_guid", "new_guid", "detail")) + "</tr>" for row in report["references"])
    counts = " / ".join(f"{esc(k)}: {v}" for k, v in report["summary"]["status_counts"].items())
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>BCF Link Rescue review draft</title><style>body{{font:16px system-ui;background:#f4f5f0;color:#142b27;margin:32px}}h1{{font-size:36px}}.notice{{background:#fff0ca;padding:20px;border-radius:12px}}table{{border-collapse:collapse;background:white;width:100%;font-size:13px}}td,th{{text-align:left;vertical-align:top;padding:12px;border:1px solid #d7ded5;overflow-wrap:anywhere}}.scroll{{overflow:auto}}a{{color:#126c58}}</style><h1>BCF Link Rescue</h1><p class="notice">{esc(report['notice'])}<br>{esc(report['scope'])}<br>Manually load the new IFC in your model viewer. Original BCF model-file metadata may still name the old IFC.</p><p>{counts}</p><p>{report['summary']['reference_nodes']} reference nodes. {report['summary']['cloned_viewpoints']} cloned draft viewpoints. Original viewpoints and comment references remain in the archive.</p><div class="scroll"><table><thead><tr><th>Outcome</th><th>Tag</th><th>IFC class</th><th>Group</th><th>Original ID</th><th>Draft ID</th><th>Review detail</th></tr></thead><tbody>{rows}</tbody></table></div><p><a href="mailto:tianchaodaxing@gmail.com">Contact PANDAO</a></p></html>'''


def json_text(report):
    return json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n"
