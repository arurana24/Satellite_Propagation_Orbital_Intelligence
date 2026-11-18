"""Figure builders returning plain Plotly JSON ({"data": [...], "layout": {...}}).

No plotly import: these run unchanged inside Pyodide and are drawn by plotly.js in the browser.
Every array comes from a real engine run. tests/test_bridge.py validates every figure with the real
plotly Python package (which raises on any invalid property), and the browser test runs Plotly.validate.
"""
from __future__ import annotations

import math
import numpy as np

from orbital_intelligence.constants import RE, DEG
from orbital_intelligence.frames import gmst_at, geodetic_to_ecef, ecef_to_eci
from orbital_intelligence.groundtrack import split_dateline, footprint_circle, sensor_central_angle, horizon_central_angle

P = {"bg": "#0A1020", "panel": "#111A2E", "grid": "#22304A", "text": "#E2E8F0", "muted": "#94A3B8",
     "cyan": "#22D3EE", "amber": "#F59E0B", "red": "#EF4444", "green": "#22C55E", "violet": "#A78BFA", "blue": "#3B82F6"}


def L(a, nd=None):
    """numpy -> JSON-safe list (NaN -> None so plotly.js breaks lines there)."""
    a = np.asarray(a, float)
    if nd is not None: a = np.round(a, nd)
    return [None if not math.isfinite(x) else float(x) for x in a.ravel()]


def _layout(title="", height=420, **extra):
    # No "template": a template *name* string is a plotly-Python convenience that plotly.js does not accept.
    lay = {"paper_bgcolor": P["bg"], "plot_bgcolor": P["panel"],
           "title": {"text": title, "x": 0.01}, "height": height, "margin": {"l": 55, "r": 20, "t": 50, "b": 70},
           "font": {"color": P["text"]}, "hoverlabel": {"bgcolor": P["panel"]}, "legend": {"orientation": "h", "y": -0.12, "yanchor": "top", "x": 0}}
    lay.update(extra)
    if "scene" in lay or "geo" in lay:          # no cartesian axes: plot_bgcolor would be ignored (plotly.js warns)
        lay.pop("plot_bgcolor")
    return lay


def _grid(lay, rows, cols, titles, hgap=0.10, vgap=0.12, secondary=()):
    """Minimal make_subplots: axis domains + subplot-title annotations. Returns {(r,c): (xref, yref, y2ref)}."""
    w = (1 - hgap * (cols - 1)) / cols; h = (1 - vgap * (rows - 1)) / rows
    refs = {}; ann = []; n = 0; extra = 1 + rows * cols
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            n += 1; sx = "" if n == 1 else str(n)
            x0 = (c - 1) * (w + hgap); y1 = 1 - (r - 1) * (h + vgap); y0 = y1 - h
            lay[f"xaxis{sx}"] = {"domain": [round(max(0.0, x0), 6), round(min(1.0, x0 + w), 6)], "anchor": f"y{sx}", "gridcolor": P["grid"]}
            lay[f"yaxis{sx}"] = {"domain": [round(max(0.0, y0), 6), round(min(1.0, y1), 6)], "anchor": f"x{sx}", "gridcolor": P["grid"]}
            y2 = None
            if (r, c) in secondary:
                y2 = str(extra); extra += 1
                lay[f"yaxis{y2}"] = {"anchor": f"x{sx}", "overlaying": f"y{sx}", "side": "right", "showgrid": False}
            refs[(r, c)] = (f"x{sx}", f"y{sx}", None if y2 is None else f"y{y2}")
            if n <= len(titles) and titles[n - 1]:
                ann.append({"text": titles[n - 1], "x": x0 + w / 2, "y": y1, "xref": "paper", "yref": "paper",
                            "xanchor": "center", "yanchor": "bottom", "showarrow": False, "font": {"size": 13}})
    lay["annotations"] = ann
    return refs


def _ax(ref):
    return "axis" + ref[1:]          # 'x2' -> 'axis2', 'x' -> 'axis'


# ----------------------------------------------------------------------------- 3D
def _graticule(theta, step=30, n=100):
    lines = []
    lon = np.radians(np.linspace(-180, 180, n))
    for la in range(-60, 90, step):
        lat = np.full(n, np.radians(la))
        lines.append((RE * np.cos(lat) * np.cos(lon + theta), RE * np.cos(lat) * np.sin(lon + theta), RE * np.sin(lat)))
    lat = np.radians(np.linspace(-90, 90, n))
    for lo in range(0, 360, step):
        l = np.full(n, np.radians(lo))
        lines.append((RE * np.cos(lat) * np.cos(l + theta), RE * np.cos(lat) * np.sin(l + theta), RE * np.sin(lat)))
    return lines


def earth_3d(res, idx: int, frame: str = "eci") -> dict:
    """Earth + orbit. frame='eci' (inertial, Earth rotates beneath) or 'ecef' (Earth-fixed)."""
    t = res.prop.t; ep = res.config.epoch
    theta = float(gmst_at(ep, t[idx])) if frame == "eci" else 0.0
    R = res.prop.r if frame == "eci" else res.track["r_ecef"]
    lat = np.linspace(-np.pi / 2, np.pi / 2, 37); lon = np.linspace(-np.pi, np.pi, 73)
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    data = [{"type": "surface", "x": (RE * np.cos(LA) * np.cos(LO + theta)).round(1).tolist(),
             "y": (RE * np.cos(LA) * np.sin(LO + theta)).round(1).tolist(), "z": (RE * np.sin(LA)).round(1).tolist(),
             "colorscale": [[0, "#0B2A4A"], [1, "#1D5C8C"]], "showscale": False, "opacity": 0.95, "hoverinfo": "skip", "name": "Earth"}]
    for x, y, z in _graticule(theta):
        data.append({"type": "scatter3d", "x": L(x, 1), "y": L(y, 1), "z": L(z, 1), "mode": "lines",
                     "line": {"color": "#3B6FA0", "width": 1}, "hoverinfo": "skip", "showlegend": False})
    data.append({"type": "scatter3d", "x": L(R[:, 0], 2), "y": L(R[:, 1], 2), "z": L(R[:, 2], 2), "mode": "lines",
                 "line": {"color": P["cyan"], "width": 4}, "name": "Orbit"})
    data.append({"type": "scatter3d", "x": [float(R[idx, 0])], "y": [float(R[idx, 1])], "z": [float(R[idx, 2])], "mode": "markers",
                 "marker": {"size": 6, "color": P["amber"]}, "name": "Satellite"})
    pts = [(tg.name, tg.lat_deg, tg.lon_deg, tg.alt_km) for tg in res.config.targets]
    site = res.config.launch_site
    if site is not None:                       # short label ("Sriharikota") so it does not collide with nearby targets
        short = site.name.split("(")[-1].rstrip(")") if "(" in site.name else site.name
        pts.append((short, site.lat_deg, site.lon_deg, site.alt_km))
    for name, la, lo, h in pts:
        p = geodetic_to_ecef(la * DEG, lo * DEG, h)
        if frame == "eci": p = ecef_to_eci(p, theta)
        data.append({"type": "scatter3d", "x": [float(p[0])], "y": [float(p[1])], "z": [float(p[2])], "mode": "markers+text",
                     "text": [name], "textposition": "top center", "marker": {"size": 4, "color": P["green"]}, "name": name, "showlegend": False})
    ext = float(np.max(np.abs(R))) * 1.05
    ax = {"visible": False, "range": [-ext, ext]}
    lay = _layout(f"Orbit in {frame.upper()} frame", 600,
                  scene={"xaxis": dict(ax), "yaxis": dict(ax), "zaxis": dict(ax), "aspectmode": "cube", "bgcolor": P["bg"]},
                  uirevision="earth3d")
    return {"data": data, "layout": lay}


# ----------------------------------------------------------------------------- ground track
def ground_track_map(res, idx: int, show_footprint=True, show_full=True) -> dict:
    g = res.track; cfg = res.config
    data = []
    if show_full:
        lon, lat = split_dateline(g["lon"], g["lat"])
        data.append({"type": "scattergeo", "lon": L(lon, 3), "lat": L(lat, 3), "mode": "lines",
                     "line": {"color": P["cyan"], "width": 2}, "name": "Ground track"})
    data.append({"type": "scattergeo", "lon": [float(g["lon"][idx])], "lat": [float(g["lat"][idx])], "mode": "markers",
                 "marker": {"size": 9, "color": P["amber"]}, "name": "Satellite"})
    if show_footprint:
        alt = g["alt"][idx]
        lam = float(sensor_central_angle(alt, cfg.half_fov_deg)) if cfg.half_fov_deg else float(horizon_central_angle(alt, 10.0))
        la, lo = footprint_circle(g["lat"][idx], g["lon"][idx], lam)
        lo, la = split_dateline(lo, la)
        data.append({"type": "scattergeo", "lon": L(lo, 3), "lat": L(la, 3), "mode": "lines", "line": {"color": P["amber"], "width": 1.5},
                     "name": "Sensor footprint" if cfg.half_fov_deg else "10 deg visibility"})
    if cfg.targets:
        data.append({"type": "scattergeo", "lon": [t.lon_deg for t in cfg.targets], "lat": [t.lat_deg for t in cfg.targets],
                     "text": [t.name for t in cfg.targets], "mode": "markers+text", "textposition": "top right",
                     "marker": {"size": 8, "color": P["green"], "symbol": "diamond"}, "name": "Targets"})
    if cfg.launch_site is not None:
        s = cfg.launch_site
        data.append({"type": "scattergeo", "lon": [s.lon_deg], "lat": [s.lat_deg], "text": [s.name], "mode": "markers",
                     "marker": {"size": 8, "color": P["red"], "symbol": "triangle-up"}, "name": "Launch site"})
    if cfg.region:
        a, b, c, d = cfg.region
        data.append({"type": "scattergeo", "lon": [c, d, d, c, c], "lat": [a, a, b, b, a], "mode": "lines",
                     "line": {"color": P["violet"], "dash": "dash"}, "name": "Region"})
    # D1: layout.geo has no top-level `gridcolor`; the grid colour belongs to each axis object.
    geo = {"projection": {"type": "equirectangular"}, "showland": True, "landcolor": "#1B2A41", "showocean": True,
           "oceancolor": "#0B1730", "showcountries": True, "countrycolor": "#33415C", "coastlinecolor": "#3B82F6",
           "lataxis": {"showgrid": True, "gridcolor": P["grid"], "range": [-90, 90]},
           "lonaxis": {"showgrid": True, "gridcolor": P["grid"], "range": [-180, 180]},
           "bgcolor": P["bg"], "resolution": 110}
    return {"data": data, "layout": _layout("Ground track", 520, geo=geo, uirevision="gt")}


def ascent_ground_track(lr) -> dict:
    s = lr.samples; site = lr.site
    data = [{"type": "scattergeo", "lon": L(s["lon_deg"], 3), "lat": L(s["lat_deg"], 3), "mode": "lines",
             "line": {"color": P["amber"], "width": 3}, "name": "Ascent ground track"},
            {"type": "scattergeo", "lon": [site.lon_deg], "lat": [site.lat_deg], "text": [site.name], "mode": "markers+text",
             "textposition": "top right", "marker": {"size": 9, "color": P["red"], "symbol": "triangle-up"}, "name": "Launch site"}]
    la, lo = s["lat_deg"], s["lon_deg"]
    pad = 8.0
    geo = {"projection": {"type": "equirectangular"}, "showland": True, "landcolor": "#1B2A41", "showocean": True,
           "oceancolor": "#0B1730", "showcountries": True, "countrycolor": "#33415C", "coastlinecolor": "#3B82F6",
           "lataxis": {"showgrid": True, "gridcolor": P["grid"], "range": [float(max(-90, la.min() - pad)), float(min(90, la.max() + pad))]},
           "lonaxis": {"showgrid": True, "gridcolor": P["grid"], "range": [float(max(-180, lo.min() - pad)), float(min(180, lo.max() + pad))]},
           "bgcolor": P["bg"], "resolution": 110}
    return {"data": data, "layout": _layout("Ascent ground track (where the rocket flies over)", 420, geo=geo)}


# ----------------------------------------------------------------------------- analysis
def separation_figure(cmp) -> dict:
    return {"data": [{"type": "scatter", "x": L(cmp["t"] / 3600, 4), "y": L(cmp["sep_km"], 4), "line": {"color": P["amber"]},
                      "name": "|r_J2 - r_2body|"}],
            "layout": _layout("J2 vs two-body: position separation", 380,
                              xaxis={"title": {"text": "Time [h]"}, "gridcolor": P["grid"]},
                              yaxis={"title": {"text": "Position difference [km]"}, "gridcolor": P["grid"]})}


def element_comparison_figure(cmp, key: str, label: str) -> dict:
    t = L(cmp["t"] / 3600, 4)
    return {"data": [{"type": "scatter", "x": t, "y": L(cmp["osc_twobody"][key], 6), "name": "Two-body", "line": {"color": P["muted"]}},
                     {"type": "scatter", "x": t, "y": L(cmp["osc_j2"][key], 6), "name": "J2", "line": {"color": P["cyan"]}}],
            "layout": _layout(f"Osculating {label}", 320, xaxis={"title": {"text": "Time [h]"}, "gridcolor": P["grid"]},
                              yaxis={"title": {"text": label}, "gridcolor": P["grid"]})}


def osculating_figure(res) -> dict:
    o = res.osculating; t = L(o["t"] / 3600, 4)
    lay = _layout("Osculating elements and altitude", 680)
    refs = _grid(lay, 3, 2, ("a [km]", "e", "i [deg]", "RAAN [deg]", "arg. latitude u [deg]", "altitude [km]"))
    data = []
    for k, rc in (("a", (1, 1)), ("e", (1, 2)), ("i", (2, 1)), ("raan", (2, 2)), ("u", (3, 1))):
        x, y, _ = refs[rc]
        data.append({"type": "scatter", "x": t, "y": L(o[k], 7), "line": {"color": P["cyan"]}, "showlegend": False, "xaxis": x, "yaxis": y, "name": k})
    x, y, _ = refs[(3, 2)]
    stride = max(1, len(res.prop.t) // 1500)
    data.append({"type": "scatter", "x": L(res.prop.t[::stride] / 3600, 4), "y": L(res.track["alt"][::stride], 3),
                 "line": {"color": P["amber"]}, "showlegend": False, "xaxis": x, "yaxis": y, "name": "altitude"})
    for c in (1, 2):
        lay["x" + _ax(refs[(3, c)][0])]["title"] = {"text": "Time [h]"}
    return {"data": data, "layout": lay}


def convergence_figure(cs) -> dict:
    lay = _layout("Numerical convergence vs analytical Kepler solution (1 orbit)", 380)
    refs = _grid(lay, 1, 2, (f"RK4: observed order = {cs['rk4_observed_order']:.2f}", "DOP853: tolerance vs error"))
    (x1, y1, _), (x2, y2, _) = refs[(1, 1)], refs[(1, 2)]
    data = [{"type": "scatter", "x": cs["rk4_steps_s"], "y": cs["rk4_pos_err_km"], "mode": "lines+markers",
             "line": {"color": P["cyan"]}, "showlegend": False, "xaxis": x1, "yaxis": y1, "name": "RK4"},
            {"type": "scatter", "x": [d["rtol"] for d in cs["dop853"]], "y": [max(d["pos_err_km"], 1e-16) for d in cs["dop853"]],
             "mode": "lines+markers", "line": {"color": P["amber"]}, "showlegend": False, "xaxis": x2, "yaxis": y2, "name": "DOP853"}]
    for ref in (x1, x2, y1, y2):
        lay[ref[0] + _ax(ref)]["type"] = "log"
    lay["x" + _ax(x1)]["title"] = {"text": "step [s]"}; lay["x" + _ax(x2)]["title"] = {"text": "rtol = atol"}
    lay["y" + _ax(y1)]["title"] = {"text": "final position error [km]"}
    return {"data": data, "layout": lay}


def sweep_figure(rows, xcol: str, ycols: list) -> dict:
    colors = [P["cyan"], P["amber"], P["green"], P["violet"], P["red"]]
    x = [r[xcol] for r in rows]
    data = [{"type": "scatter", "x": x, "y": [r.get(y) for r in rows], "mode": "lines+markers", "name": y,
             "line": {"color": colors[k % 5]}} for k, y in enumerate(ycols)]
    return {"data": data, "layout": _layout("Parameter sweep", 400, xaxis={"title": {"text": xcol}, "gridcolor": P["grid"]},
                                            yaxis={"gridcolor": P["grid"]})}


# ----------------------------------------------------------------------------- coverage
def coverage_heatmap(cov) -> dict:
    lats, lons = cov.grid_axes
    z = cov.n_events.reshape(len(lats), len(lons)).astype(float)
    z[~cov.covered_ever.reshape(len(lats), len(lons))] = np.nan
    return {"data": [{"type": "heatmap", "x": L(lons, 3), "y": L(lats, 3), "z": [L(row) for row in z], "colorscale": "Viridis",
                      "colorbar": {"title": {"text": "events"}}}],
            "layout": _layout("Distinct coverage events per grid cell (blank = never covered)", 460,
                              xaxis={"title": {"text": "Longitude [deg]"}, "gridcolor": P["grid"]},
                              yaxis={"title": {"text": "Latitude [deg]"}, "scaleanchor": "x", "gridcolor": P["grid"]})}


def coverage_time_figure(cov) -> dict:
    stride = max(1, len(cov.t) // 1500)
    return {"data": [{"type": "scatter", "x": L(cov.t[::stride] / 3600, 4), "y": L(100 * cov.cum_fraction[::stride], 3),
                      "fill": "tozeroy", "line": {"color": P["green"]}, "name": "covered"}],
            "layout": _layout("Cumulative region coverage", 320, xaxis={"title": {"text": "Time [h]"}, "gridcolor": P["grid"]},
                              yaxis={"title": {"text": "Cumulative area covered [%]"}, "range": [0, 100], "gridcolor": P["grid"]})}


def pass_timeline(res) -> dict:
    data = []
    for name, ps in res.passes.items():
        if not ps: continue
        data.append({"type": "bar", "orientation": "h", "base": [p.aos_s / 3600 for p in ps], "x": [p.duration_s / 3600 for p in ps],
                     "y": [name] * len(ps), "marker": {"color": P["cyan"]}, "showlegend": False, "name": name,
                     "customdata": [[p.duration_s, p.max_elev_deg] for p in ps],
                     "hovertemplate": name + "<br>AOS %{base:.2f} h<br>duration %{customdata[0]:.0f} s<br>max elev %{customdata[1]:.1f} deg<extra></extra>"})
    return {"data": data, "layout": _layout("Access windows (AOS to LOS)", 140 + 60 * max(1, len(res.passes)),
                                            xaxis={"title": {"text": "Time since epoch [h]"}, "range": [0, res.config.duration_h], "gridcolor": P["grid"]},
                                            yaxis={"gridcolor": P["grid"]})}


# ----------------------------------------------------------------------------- launch
def ascent_figure(lr) -> dict:
    s = lr.samples; t = L(s["t"], 2)
    lay = _layout("Ascent telemetry", 700)
    refs = _grid(lay, 2, 2, ("Altitude and speed", "Trajectory (altitude vs downrange)", "Dynamic pressure and acceleration", "Vehicle mass"),
                 hgap=0.14, secondary=((1, 1), (2, 1)))
    x11, y11, y11b = refs[(1, 1)]; x12, y12, _ = refs[(1, 2)]; x21, y21, y21b = refs[(2, 1)]; x22, y22, _ = refs[(2, 2)]
    tr = lambda x, y, name, color, xa, ya, dash=None, legend=True: {"type": "scatter", "x": x, "y": y, "name": name, "xaxis": xa, "yaxis": ya,
                                                                    "line": {"color": color, **({"dash": dash} if dash else {})}, "showlegend": legend}
    data = [tr(t, L(s["alt_km"], 3), "Altitude [km]", P["cyan"], x11, y11),
            tr(t, L(s["speed_inertial_kms"], 4), "Inertial speed [km/s]", P["amber"], x11, y11b),
            tr(t, L(s["speed_rel_kms"], 4), "Earth-relative speed [km/s]", P["green"], x11, y11b, "dot"),
            tr(L(s["downrange_km"], 2), L(s["alt_km"], 3), "Trajectory", P["cyan"], x12, y12, legend=False),
            tr(t, L(s["q_kpa"], 3), "q [kPa]", P["violet"], x21, y21),
            tr(t, L(s["accel_g"], 3), "Acceleration [g]", P["red"], x21, y21b),
            tr(t, L(s["mass"] / 1000, 3), "Mass [t]", P["blue"], x22, y22, legend=False)]
    shapes = []
    for te, name in lr.events:
        if any(k in name for k in ("separation", "cutoff", "burnout")):
            for xa, ya in ((x11, y11), (x21, y21), (x22, y22)):
                shapes.append({"type": "line", "x0": float(te), "x1": float(te), "xref": xa, "y0": 0, "y1": 1, "yref": f"{ya} domain",
                               "line": {"color": P["muted"], "dash": "dot", "width": 1}})
    lay["shapes"] = shapes
    for ref, txt in ((x21, "Time [s]"), (x22, "Time [s]"), (x12, "Downrange [km]")):
        lay["x" + _ax(ref)]["title"] = {"text": txt}
    for ref, txt in ((y11, "km"), (y11b, "km/s"), (y21, "kPa"), (y21b, "g"), (y12, "altitude [km]"), (y22, "tonnes")):
        lay["y" + _ax(ref)]["title"] = {"text": txt}
    lay["legend"] = {"orientation": "h", "y": -0.1, "yanchor": "top", "x": 0}
    lay["margin"] = {"l": 55, "r": 55, "t": 50, "b": 80}
    return {"data": data, "layout": lay}
