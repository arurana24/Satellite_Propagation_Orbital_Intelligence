"""Report/export generation from real simulation outputs: Markdown, JSON, CSV ephemeris, and PDF (reportlab)."""
from __future__ import annotations

import io
import json
from datetime import timedelta
import numpy as np

from . import __version__


def _fmt(v, nd=3):
    if v is None: return "-"
    if isinstance(v, (float, np.floating)): return f"{v:.{nd}f}"
    return str(v)


def _fmt_time(res, s):
    return (res.epoch + timedelta(seconds=float(s))).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_sections(res, launch=None, validation=None) -> list[tuple[str, list]]:
    """Return [(heading, rows)] where rows are lists of strings (first row = table header) or ('text', str)."""
    cfg, s = res.config, res.summary
    secs = []
    secs.append(("Mission configuration", [["Parameter", "Value"],
        ["Mission", cfg.name], ["Epoch", cfg.epoch.strftime("%Y-%m-%d %H:%M:%S UTC")],
        ["Force model", cfg.model], ["Integrator", cfg.method],
        ["Duration / step", f"{cfg.duration_h:g} h / {cfg.dt_s:g} s"],
        ["Initial state source", "Launch burnout hand-off" if cfg.state0 is not None and launch is not None else "Orbital elements"],
        ["Sensor half-FOV", _fmt(cfg.half_fov_deg, 1) + " deg" if cfg.half_fov_deg else "none"]]))
    secs.append(("Orbit parameters", [["Parameter", "Value"],
        ["Semi-major axis", f"{s['a_km']:.3f} km"], ["Eccentricity", f"{s['e']:.6f}"], ["Inclination", f"{s['i_deg']:.4f} deg"],
        ["RAAN / arg. perigee / true anomaly", f"{s['raan_deg']:.3f} / {s['argp_deg']:.3f} / {s['nu_deg']:.3f} deg"],
        ["Perigee x apogee altitude", f"{s['perigee_alt_km']:.2f} x {s['apogee_alt_km']:.2f} km"],
        ["Period", f"{s['period_min']:.3f} min ({s['mean_motion_rev_day']:.4f} rev/day)"],
        ["Speed at perigee / apogee", f"{s['v_perigee_kms']:.4f} / {s['v_apogee_kms']:.4f} km/s"],
        ["Specific energy", f"{s['energy_km2s2']:.4f} km^2/s^2"],
        ["J2 RAAN / perigee drift", f"{s['raan_rate_deg_day']:+.4f} / {s['argp_rate_deg_day']:+.4f} deg/day"],
        ["Sun-synchronous inclination at this a, e", _fmt(s['sso_inclination_deg'], 3) + " deg" if s['sso_inclination_deg'] else "n/a"]]))
    inv = res.invariants
    secs.append(("Numerical integrity of this run", [["Quantity", "Value"],
        ["Relative energy drift", f"{inv['energy_rel_drift']:.2e}"], [f"Relative {inv['invariant_used']} drift", f"{inv['angmom_rel_drift']:.2e}"],
        ["Function evaluations", str(res.prop.nfev)]]))
    if launch is not None:
        l = launch.summary
        secs.append(("Launch & ascent", [["Parameter", "Value"],
            ["Vehicle", launch.vehicle.name], ["Site", f"{launch.site.name} ({launch.site.lat_deg:.3f}, {launch.site.lon_deg:.3f})"],
            ["Outcome", launch.status], ["Burnout time / altitude", f"{l['burnout_time_s']:.1f} s / {l['burnout_alt_km']:.1f} km"],
            ["Burnout inertial speed", f"{l['burnout_speed_inertial_kms']:.4f} km/s"], ["Downrange", f"{l['downrange_km']:.0f} km"],
            ["Max-Q", f"{l['max_q_kpa']:.1f} kPa at t={l['max_q_time_s']:.0f} s ({l['max_q_alt_km']:.1f} km)"],
            ["Max acceleration", f"{l['max_accel_g']:.2f} g"], ["Engine delta-v / velocity gain", f"{l['engine_dv_kms']:.3f} / {l['velocity_gain_kms']:.3f} km/s"],
            ["Total losses (gravity+drag+steering)", f"{l['total_losses_kms']:.3f} km/s"], ["Burnout mass", f"{l['burnout_mass_kg']:.0f} kg"]]))
    for name, ps in res.passes.items():
        rv = res.revisit[name]
        rows = [["Metric", "Value"], ["Passes in window", str(rv["n_passes"])], ["Total access", f"{rv['total_access_s']/60:.1f} min ({100*rv['access_fraction']:.2f}% of window)"],
                ["Mean / max revisit gap", f"{_fmt(None if rv['mean_gap_s'] is None else rv['mean_gap_s']/3600,2)} / {_fmt(None if rv['max_gap_s'] is None else rv['max_gap_s']/3600,2)} h"]]
        secs.append((f"Access: {name}", rows))
        if ps:
            secs.append((f"Pass list: {name}", [["AOS (UTC)", "LOS (UTC)", "Duration [s]", "Max elev [deg]"]] +
                [[_fmt_time(res, p.aos_s), _fmt_time(res, p.los_s), f"{p.duration_s:.0f}", f"{p.max_elev_deg:.1f}"] for p in ps[:40]]))
    if res.coverage is not None:
        c = res.coverage
        secs.append(("Region coverage", [["Metric", "Value"], ["Region (lat/lon box)", str(cfg.region)], ["Area-weighted coverage", f"{100*c.coverage_fraction:.1f}%"],
                                         ["Mean worst-case revisit gap", _fmt(None if c.mean_revisit_s is None else c.mean_revisit_s / 3600, 2) + " h"]]))
    secs.append(("Mission feasibility assessment", [["Severity", "Finding", "Detail", "Suggested correction"]] +
        [[f.severity.upper(), f.title, f.message, f.suggestion or "-"] for f in res.assessment.findings]))
    if validation:
        secs.append(("Numerical validation", [["Check", "Value", "Threshold", "Result"]] +
            [[r["check"], f"{r['value']:.3e}", f"{r['threshold']:.1e}", "PASS" if r["passed"] else "FAIL"] for r in validation]))
    return secs


def to_markdown(res, launch=None, validation=None) -> str:
    out = [f"# Orbital Intelligence report: {res.config.name}", f"_Generated by Orbital Intelligence v{__version__}. All values are computed by the simulation engine for the configuration shown._", ""]
    for head, rows in build_sections(res, launch, validation):
        out.append(f"## {head}")
        hdr = rows[0]
        out.append("| " + " | ".join(hdr) + " |"); out.append("|" + "---|" * len(hdr))
        for r in rows[1:]: out.append("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
        out.append("")
    return "\n".join(out)


def to_json(res, launch=None) -> str:
    def conv(o):
        if isinstance(o, (np.floating, np.integer)): return o.item()
        if isinstance(o, np.ndarray): return o.tolist()
        return str(o)
    d = {"mission": res.config.name, "epoch": res.config.epoch.isoformat(), "orbit": res.summary,
         "invariants": res.invariants, "revisit": res.revisit,
         "findings": [f.as_dict() for f in res.assessment.findings], "status": res.assessment.status,
         "passes": {k: [p.__dict__ for p in v] for k, v in res.passes.items()}}
    if launch is not None: d["launch"] = launch.summary
    return json.dumps(d, default=conv, indent=2)


def ephemeris_csv(res, stride: int = 1) -> str:
    buf = io.StringIO()
    buf.write("t_s,utc,x_km,y_km,z_km,vx_kms,vy_kms,vz_kms,lat_deg,lon_deg,alt_km\n")
    p, g = res.prop, res.track
    for k in range(0, len(p.t), stride):
        y = p.y[k]
        buf.write(f"{p.t[k]:.3f},{_fmt_time(res, p.t[k])},{y[0]:.6f},{y[1]:.6f},{y[2]:.6f},{y[3]:.9f},{y[4]:.9f},{y[5]:.9f},{g['lat'][k]:.6f},{g['lon'][k]:.6f},{g['alt'][k]:.4f}\n")
    return buf.getvalue()


def to_pdf(res, launch=None, validation=None) -> bytes:
    """PDF via reportlab (raises ImportError with a clear message if unavailable)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError as e:
        raise ImportError("PDF export needs the 'reportlab' package (pip install reportlab).") from e
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    st = getSampleStyleSheet(); cell = st["BodyText"].clone("cell"); cell.fontSize = 7.5; cell.leading = 9
    story = [Paragraph(f"Orbital Intelligence report: {res.config.name}", st["Title"]),
             Paragraph(f"Orbital Intelligence v{__version__} - all figures computed from the simulation run.", st["Italic"]), Spacer(1, 8)]
    for head, rows in build_sections(res, launch, validation):
        story.append(Paragraph(head, st["Heading3"]))
        data = [[Paragraph(str(c).replace("&", "&amp;").replace("<", "&lt;"), cell) for c in r] for r in rows]
        ncol = len(rows[0]); width = (A4[0] - 72) / ncol
        t = Table(data, colWidths=[width] * ncol if ncol != 2 else [(A4[0] - 72) * 0.4, (A4[0] - 72) * 0.6], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B3C5D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        for r in data[:1]:
            for c in r: c.style = cell.clone("h", textColor=colors.white)
        story += [t, Spacer(1, 8)]
    doc.build(story)
    return buf.getvalue()
