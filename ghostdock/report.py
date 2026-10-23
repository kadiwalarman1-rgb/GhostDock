"""Standalone, escaped HTML report. No CDN, scripts or external requests."""

from html import escape
from pathlib import Path

from .evidence import verify_receipt


def render_report(receipt_path, output):
    state, events = verify_receipt(receipt_path)
    mode = (
        "SIMULATION · VIRTUAL TIME · NO AWS RESOURCES"
        if state["simulation"]
        else "LIVE OBSERVATION · SEE VALIDATION SCOPE"
    )
    seconds = state["recovery_seconds"]
    duration = "Not recovered" if seconds is None else f"{seconds:.2f}s"
    rows = "".join(
        f'<tr><td>{e["elapsed_seconds"]:.2f}s</td><td>{escape(e["state"]["status"])}</td>'
        f'<td>{escape(e["detail"])}</td></tr>'
        for e in events
    )
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>GhostDock · Recovery receipt</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0b1018;color:#e7edf5}
body{max-width:1050px;margin:0 auto;padding:48px 24px}header{border-bottom:1px solid #2b3646;padding-bottom:30px}
.eyebrow{color:#70e2c1;letter-spacing:.13em;font-size:12px;font-weight:700}h1{font-size:48px;margin:12px 0}
p{color:#a9b9cd;line-height:1.7}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:28px 0}
.card{background:#121c29;border:1px solid #29384a;border-radius:12px;padding:22px;overflow-wrap:anywhere}
.value{font-size:22px;font-weight:700;color:#f6f9fc}.label{font-size:12px;letter-spacing:.06em;color:#a9b9cd;margin-top:8px}
.notice{border-left:3px solid #70e2c1;padding:12px 20px;background:#112222;margin:26px 0}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:14px 12px;border-bottom:1px solid #29384a;text-align:left;vertical-align:top}
th{color:#8ca0b8}td:first-child{white-space:nowrap;color:#70e2c1}code{overflow-wrap:anywhere;font-size:12px;color:#b6c9df}
footer{margin-top:32px;font-size:13px;color:#8ca0b8}@media(max-width:680px){.cards{grid-template-columns:1fr}h1{font-size:36px}td,th{padding:12px 6px;font-size:12px}}
</style></head><body><header><div class="eyebrow">GHOSTDOCK / RECOVERY RECEIPT</div>
<h1>Recovery, with evidence.</h1><p>@@MODE@@</p><code>Incident @@INCIDENT@@</code></header>
<div class="cards"><div class="card"><div class="value">@@STATUS@@</div><div class="label">FINAL STATE</div></div>
<div class="card"><div class="value">@@DURATION@@</div><div class="label">FIRST FAILED PROBE → VERIFIED RECOVERY</div></div>
<div class="card"><div class="value">Not switched</div><div class="label">USER TRAFFIC</div></div></div>
<div class="notice">The event chain and state snapshot are consistent. This is an integrity check, not a digital signature or independent proof of AWS execution. Simulation timings are not performance benchmarks.</div>
<h2>Incident timeline</h2><table><thead><tr><th>Elapsed</th><th>State</th><th>Observation</th></tr></thead><tbody>@@ROWS@@</tbody></table>
<footer>Workload: @@WORKLOAD@@ · Instance: @@INSTANCE@@<br>Receipt head: <code>@@HASH@@</code><br>
GhostDock 0.1.0 · Stateless application recovery prototype. No zero-downtime guarantee.</footer></body></html>"""
    values = {
        "MODE": escape(mode),
        "INCIDENT": escape(state["incident_id"]),
        "STATUS": escape(state["status"]),
        "DURATION": escape(duration),
        "ROWS": rows,
        "WORKLOAD": escape(state["workload"]),
        "INSTANCE": escape(state["instance_id"] or "none"),
        "HASH": escape(events[-1]["hash"]),
    }
    for key, value in values.items():
        html = html.replace(f"@@{key}@@", value)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    return output
