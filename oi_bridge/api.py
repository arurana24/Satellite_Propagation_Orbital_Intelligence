"""JSON boundary between the browser and the unchanged physics engine.

    run_mission(config_json: str) -> result_json: str        # the one entry point for a mission

The browser (a Pyodide Web Worker) only ever sees JSON. Two helpers reuse the last result instead of
re-running it: `analyse(request_json)` (J2 vs two-body, validation, sweeps) and `figure(request_json)`
(redraw the 3-D / ground-track view at another time index). Everything here is plain Python + numpy/scipy,
so the same code is tested natively by pytest and executed in the browser.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timedelta
import numpy as np

from orbital_intelligence import __version__ as ENGINE_VERSION
from orbital_intelligence import constants as C
from orbital_intelligence.elements import ClassicalElements, OrbitError, nu_from_M
from orbital_intelligence.groundtrack import GroundTarget
from orbital_intelligence.simulation import MissionConfig, run_mission as _run_mission, config_from_launch
from orbital_intelligence.launch import (SITES, DEFAULT_VEHICLE, Vehicle, Stage, AscentProgram, LaunchError, RANGE_CORRIDORS,
                                         analytical_launch, simulate_ascent, tune_ascent, choose_branch, rotating_azimuth,
                                         inertial_azimuths)
from orbital_intelligence.elements import circular_velocity
from orbital_intelligence.intelligence import evaluate_launch
from orbital_intelligence.analysis import compare_twobody_j2, parameter_sweep_rows, validate_numerics
from orbital_intelligence.propagators import convergence_study
from orbital_intelligence import report as rpt
from . import figures as F

SCHEMA = "orbital-intelligence/mission@1"
BRIDGE_VERSION = "1.0.0"
_STATE: dict = {}                    # last MissionResult / LaunchResult, kept for analyse() and figure()


# ----------------------------------------------------------------------------- defaults
def default_config() -> dict:
    """The demonstration mission (also the shape of every saved mission file)."""
    return {
        "schema": SCHEMA, "name": "Demo-SSO-600", "epoch": "2026-11-17T00:00:00",
        "orbit": {"mode": "altitudes", "perigee_alt_km": 600.0, "apogee_alt_km": 600.0, "a_km": C.RE + 600.0, "e": 0.0,
                  "i_deg": 97.8, "raan_deg": 0.0, "argp_deg": 0.0, "anomaly_deg": 0.0, "anomaly_type": "true"},
        "propagation": {"duration_h": 24.0, "dt_s": 30.0, "model": "j2", "method": "dop853"},
        "targets": [{"name": "Patiala", "lat_deg": 30.34, "lon_deg": 76.39, "alt_km": 0.25, "min_elev_deg": 10.0},
                    {"name": "Bengaluru", "lat_deg": 12.97, "lon_deg": 77.59, "alt_km": 0.9, "min_elev_deg": 10.0}],
        "sensor": {"half_fov_deg": 30.0},
        "region": {"enabled": True, "lat_min": 5.0, "lat_max": 40.0, "lon_min": 65.0, "lon_max": 100.0, "grid_deg": 2.0},
        "area_to_mass": 0.01,
        "launch_site": "Satish Dhawan (Sriharikota)",
        "launch": {"enabled": False, "site": "Satish Dhawan (Sriharikota)", "target_alt_km": 600.0, "target_incl_deg": 97.8,
                   "branch": "auto", "guidance": "auto",
                   "manual": {"azimuth_deg": None, "vertical_time_s": 8.0, "kick_angle_deg": 1.2, "upper_pitch_end_deg": -15.0},
                   "vehicle": vehicle_to_dict(DEFAULT_VEHICLE)},
    }


def vehicle_to_dict(v: Vehicle) -> dict:
    return {"name": v.name, "payload_kg": v.payload_kg, "ref_area_m2": v.ref_area_m2, "cd_scale": v.cd_scale,
            "stages": [{"name": s.name, "dry_mass_kg": s.dry_mass_kg, "prop_mass_kg": s.prop_mass_kg, "thrust_sl_kn": s.thrust_sl_kn,
                        "thrust_vac_kn": s.thrust_vac_kn, "isp_vac_s": s.isp_vac_s} for s in v.stages]}


def vehicle_from_dict(d: dict) -> Vehicle:
    stages = tuple(Stage(str(s["name"]), float(s["dry_mass_kg"]), float(s["prop_mass_kg"]), float(s["thrust_sl_kn"]),
                         float(s["thrust_vac_kn"]), float(s["isp_vac_s"])) for s in d["stages"])
    if not stages: raise ValueError("Vehicle needs at least one stage.")
    for s in stages:
        if min(s.dry_mass_kg, s.prop_mass_kg, s.thrust_sl_kn, s.thrust_vac_kn, s.isp_vac_s) <= 0:
            raise ValueError(f"{s.name}: all stage values must be positive.")
        if s.thrust_sl_kn > s.thrust_vac_kn:
            raise ValueError(f"{s.name}: sea-level thrust cannot exceed vacuum thrust.")
    v = Vehicle(str(d.get("name", "Custom vehicle")), stages, float(d["payload_kg"]), float(d["ref_area_m2"]), float(d.get("cd_scale", 1.0)))
    if v.payload_kg < 0 or v.ref_area_m2 <= 0 or v.cd_scale <= 0: raise ValueError("Payload must be >= 0; area and drag scale > 0.")
    return v


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def normalise(cfg: dict) -> dict:
    """Fill missing keys from the defaults so old/partial mission files still load."""
    return _merge(default_config(), cfg or {})


# ----------------------------------------------------------------------------- config -> engine objects
def _parse_epoch(s: str) -> datetime:
    s = str(s).replace("Z", "")
    if "+" in s[10:]: s = s[:10] + s[10:].split("+")[0]
    return datetime.fromisoformat(s)


def _elements(o: dict) -> ClassicalElements:
    i, raan, argp, anom = (float(o[k]) for k in ("i_deg", "raan_deg", "argp_deg", "anomaly_deg"))
    if o.get("mode", "altitudes") == "altitudes":
        rp, ra = C.RE + float(o["perigee_alt_km"]), C.RE + float(o["apogee_alt_km"])
        if ra < rp: raise OrbitError("Apogee altitude must be >= perigee altitude.")
        a, e = 0.5 * (rp + ra), (ra - rp) / (ra + rp)
    else:
        a, e = float(o["a_km"]), float(o["e"])
    if not (0 <= e < 1): raise OrbitError(f"Eccentricity must satisfy 0 <= e < 1 (got {e}).")
    nu = anom if o.get("anomaly_type", "true") == "true" else math.degrees(float(nu_from_M(math.radians(anom), e))) % 360.0
    el = ClassicalElements.from_degrees(a, e, i, raan, argp, nu)
    if el.perigee_alt < 0: raise OrbitError(f"Perigee is {el.perigee_alt:.0f} km below the surface.")
    return el


def _site(name):
    if name in (None, "", "None"): return None
    if name not in SITES: raise ValueError(f"Unknown launch site '{name}'. Choose one of: {', '.join(SITES)}.")
    return SITES[name]


def mission_config(cfg: dict) -> MissionConfig:
    p, sen, reg = cfg["propagation"], cfg["sensor"], cfg["region"]
    fov = float(sen.get("half_fov_deg") or 0.0) or None
    targets = []
    for t in cfg.get("targets", []):
        if not str(t.get("name", "")).strip(): continue
        lat, lon = float(t["lat_deg"]), float(t["lon_deg"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise ValueError(f"Target {t['name']}: latitude/longitude out of range.")
        targets.append(GroundTarget(str(t["name"]), lat, lon, float(t.get("alt_km", 0.0)), float(t.get("min_elev_deg", 10.0))))
    region = None
    if reg.get("enabled") and fov:
        region = (float(reg["lat_min"]), float(reg["lat_max"]), float(reg["lon_min"]), float(reg["lon_max"]))
    if p["model"] not in ("j2", "twobody"): raise ValueError("Force model must be 'j2' or 'twobody'.")
    if p["method"] not in ("dop853", "rk4"): raise ValueError("Integrator must be 'dop853' or 'rk4'.")
    return MissionConfig(name=str(cfg["name"]), elements=_elements(cfg["orbit"]), epoch=_parse_epoch(cfg["epoch"]),
                         duration_h=float(p["duration_h"]), dt_s=float(p["dt_s"]), model=p["model"], method=p["method"],
                         targets=tuple(targets), half_fov_deg=fov, region=region, grid_deg=float(reg.get("grid_deg", 2.0)),
                         area_to_mass=float(cfg.get("area_to_mass", 0.01)), launch_site=_site(cfg.get("launch_site")))


# ----------------------------------------------------------------------------- JSON helpers
def _js(o):
    if isinstance(o, (np.floating,)): o = float(o)
    if isinstance(o, float): return o if math.isfinite(o) else None
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.bool_,)): return bool(o)
    if isinstance(o, np.ndarray): return [_js(x) for x in o.tolist()]
    if isinstance(o, dict): return {str(k): _js(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_js(x) for x in o]
    if isinstance(o, datetime): return o.isoformat()
    return o


def _dump(d) -> str:
    return json.dumps(_js(d), allow_nan=False, separators=(",", ":"))


def _err(e: Exception) -> str:
    kind = "input" if isinstance(e, (ValueError, OrbitError, LaunchError, KeyError, TypeError)) else "engine"
    return _dump({"ok": False, "error": {"kind": kind, "type": type(e).__name__, "message": str(e)}})


def _utc(epoch, s): return (epoch + timedelta(seconds=float(s))).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------- launch
def _launch(cfg: dict, epoch: datetime):
    lc = cfg["launch"]
    site = _site(lc["site"])
    if site is None: raise ValueError("Choose a launch site.")
    veh = vehicle_from_dict(lc["vehicle"])
    alt, inc = float(lc["target_alt_km"]), float(lc["target_incl_deg"])
    if not (160 <= alt <= 2000): raise ValueError("Target altitude must be within 160-2000 km for the ascent model.")
    vorb = circular_velocity(C.RE + alt)
    branch, reason = choose_branch(site, inc, vorb, lc.get("branch", "auto"))
    an = analytical_launch(site.lat_deg, alt, inc, veh, branch=branch)
    key = json.dumps({k: lc[k] for k in sorted(lc)}, sort_keys=True, default=str) + epoch.isoformat()
    cached = _STATE.get("launch_cache")
    if cached and cached[0] == key:
        lr, converged = cached[1], cached[2]
    else:
        if lc.get("guidance", "auto") == "manual":
            m = lc["manual"]
            az = m.get("azimuth_deg")
            if az is None: az = rotating_azimuth(inc, site.lat_deg, vorb, branch) or 90.0
            prog = AscentProgram(azimuth_deg=float(az), vertical_time_s=float(m["vertical_time_s"]),
                                 kick_angle_deg=float(m["kick_angle_deg"]), upper_pitch_end_deg=float(m["upper_pitch_end_deg"]))
            lr = simulate_ascent(veh, site, prog, epoch, cutoff_a_km=C.RE + alt); converged = None
        else:
            _, lr, converged = tune_ascent(veh, site, alt, inc, epoch, branch=branch)
        _STATE["launch_cache"] = (key, lr, converged)
    assess = evaluate_launch(lr.summary, alt, inc)
    azs = inertial_azimuths(inc, site.lat_deg)
    rel = {b: rotating_azimuth(inc, site.lat_deg, vorb, b) for b in ("north", "south")} if azs else None
    s = lr.samples
    stride = max(1, len(s["t"]) // 400)
    info = {"summary": lr.summary, "status": lr.status, "converged": converged, "events": [[float(t), n] for t, n in lr.events],
            "findings": [f.as_dict() for f in assess.findings], "assessment_status": assess.status,
            "analytical": {k: v for k, v in an.items()}, "branch": branch, "branch_reason": reason,
            "branches_relative_deg": rel, "corridor_deg": RANGE_CORRIDORS.get(site.name),
            "site": {"name": site.name, "lat_deg": site.lat_deg, "lon_deg": site.lon_deg},
            "vehicle": vehicle_to_dict(veh), "liftoff_mass_kg": veh.liftoff_mass, "stage_dv_kms": veh.stage_delta_vs(),
            "track": {"t": s["t"][::stride], "lat": s["lat_deg"][::stride], "lon": s["lon_deg"][::stride], "alt": s["alt_km"][::stride]},
            "figures": {"ascent": F.ascent_figure(lr), "ascent_track": F.ascent_ground_track(lr)}}
    return lr, info


# ----------------------------------------------------------------------------- main entry point
def run_mission(config_json: str) -> str:
    """config JSON (see default_config) -> result JSON. Never raises: errors come back as {"ok": false, ...}."""
    try:
        raw = json.loads(config_json) if config_json else {}
        cfg = normalise(raw)
        mc = mission_config(cfg)
        launch_info = None; lr = None
        if cfg["launch"].get("enabled"):
            lr, launch_info = _launch(cfg, mc.epoch)
            if lr.burnout_elements is None:
                raise LaunchError(f"Ascent did not reach a bound orbit ({lr.status}); nothing to propagate.")
            mc = config_from_launch(lr, replace(mc, name=mc.name))
        res = _run_mission(mc)
        _STATE.update(result=res, launch=lr, cfg=cfg)
        return _dump(_result_dict(res, cfg, lr, launch_info))
    except Exception as e:                                   # noqa: BLE001  (reported to the UI, never swallowed)
        return _err(e)


def _result_dict(res, cfg, lr, launch_info) -> dict:
    mc = res.config; t = res.prop.t
    passes = {n: [{"aos_utc": _utc(mc.epoch, p.aos_s), "los_utc": _utc(mc.epoch, p.los_s), "aos_s": p.aos_s, "los_s": p.los_s,
                   "duration_s": p.duration_s, "max_elev_deg": p.max_elev_deg, "t_max_s": p.t_max_s, "aos_az_deg": p.aos_az_deg,
                   "los_az_deg": p.los_az_deg, "clipped": p.clipped_start or p.clipped_end} for p in ps] for n, ps in res.passes.items()}
    cov = None
    if res.coverage is not None:
        c = res.coverage
        cov = {"fraction": c.coverage_fraction, "mean_revisit_s": c.mean_revisit_s, "cells": int(len(c.lat)), "grid_deg": mc.grid_deg,
               "time_samples": int(len(c.t)), "region": list(mc.region)}
    stride = max(1, len(t) // 2000)
    speed = np.linalg.norm(res.prop.v, axis=1)
    figs = {"ground_track": F.ground_track_map(res, 0), "earth_3d": F.earth_3d(res, 0, "eci"), "osculating": F.osculating_figure(res),
            "pass_timeline": F.pass_timeline(res)}
    if res.coverage is not None:
        figs["coverage_heatmap"] = F.coverage_heatmap(res.coverage); figs["coverage_time"] = F.coverage_time_figure(res.coverage)
    report_json = json.loads(rpt.to_json(res, lr))
    report_json["config"] = cfg; report_json["engine_version"] = ENGINE_VERSION
    return {"ok": True, "schema": SCHEMA, "engine_version": ENGINE_VERSION, "bridge_version": BRIDGE_VERSION,
            "constants": {"MU": C.MU, "RE": C.RE, "J2": C.J2, "OMEGA_E": C.OMEGA_E},
            "config": cfg, "name": mc.name, "epoch": mc.epoch.isoformat(), "initial_state_source": "launch" if lr is not None else "elements",
            "summary": res.summary, "invariants": res.invariants, "nfev": res.prop.nfev, "n_samples": int(len(t)),
            "status": res.assessment.status, "findings": [f.as_dict() for f in res.assessment.findings],
            "passes": passes, "revisit": res.revisit, "coverage": cov, "launch": launch_info,
            "track": {"t": t[::stride], "lat": res.track["lat"][::stride], "lon": res.track["lon"][::stride],
                      "alt": res.track["alt"][::stride], "speed": speed[::stride], "stride": stride},
            "figures": figs,
            "exports": {"markdown": rpt.to_markdown(res, lr), "json": json.dumps(_js(report_json), indent=2, allow_nan=False),
                        "csv": rpt.ephemeris_csv(res), "sections": rpt.build_sections(res, lr)}}


# ----------------------------------------------------------------------------- helpers on the last result
def _need():
    if "result" not in _STATE: raise ValueError("Run a mission first.")
    return _STATE["result"]


def figure(request_json: str) -> str:
    """Redraw a time-dependent figure: {"name": "earth_3d"|"ground_track", "idx": int (full-resolution index), ...}."""
    try:
        q = json.loads(request_json); res = _need()
        idx = int(np.clip(int(q.get("idx", 0)), 0, len(res.prop.t) - 1))
        if q["name"] == "earth_3d": fig = F.earth_3d(res, idx, q.get("frame", "eci"))
        elif q["name"] == "ground_track": fig = F.ground_track_map(res, idx, bool(q.get("footprint", True)), bool(q.get("full", True)))
        else: raise ValueError(f"Unknown figure {q['name']}")
        return _dump({"ok": True, "figure": fig})
    except Exception as e:                                   # noqa: BLE001
        return _err(e)


def analyse(request_json: str) -> str:
    """{"action": "compare", "hours", "step_s"} | {"action": "validate"} | {"action": "sweep", "parameter", "start", "stop", "n", "days"}."""
    try:
        q = json.loads(request_json); res = _need(); act = q["action"]
        y0 = res.prop.y[0]
        if act == "compare":
            hrs, step = float(q.get("hours", 24)), float(q.get("step_s", 60))
            if not (0.5 <= hrs <= 168 and 10 <= step <= 600): raise ValueError("Comparison: 0.5-168 h, step 10-600 s.")
            cmp = compare_twobody_j2(y0[:3], y0[3:], hrs * 3600, step)
            out = {k: cmp[k] for k in cmp if k.endswith("_deg_day") or k.endswith("_km")}
            # first time the separation exceeds 1 / 10 km (a prediction-horizon style figure)
            for thr in (1.0, 10.0):
                hit = np.flatnonzero(cmp["sep_km"] > thr)
                out[f"t_exceed_{int(thr)}km_min"] = None if hit.size == 0 else float(cmp["t"][hit[0]] / 60)
            out["figures"] = {"separation": F.separation_figure(cmp), "raan": F.element_comparison_figure(cmp, "raan", "RAAN [deg]"),
                              "incl": F.element_comparison_figure(cmp, "i", "inclination [deg]")}
            return _dump({"ok": True, "action": act, "hours": hrs, "step_s": step, **out})
        if act == "validate":
            from orbital_intelligence.elements import rv_to_coe
            el = rv_to_coe(y0[:3], y0[3:])
            rows = validate_numerics(el)
            r0, v0 = el.to_state(); cs = convergence_study(r0, v0, el.period)
            _STATE["validation"] = rows
            return _dump({"ok": True, "action": act, "rows": rows, "convergence": cs, "figures": {"convergence": F.convergence_figure(cs)},
                          "markdown": rpt.to_markdown(res, _STATE.get("launch"), rows)})
        if act == "sweep":
            par = q["parameter"]; n = int(q.get("n", 15)); days = float(q.get("days", 0))
            if par not in ("altitude_km", "inclination_deg", "eccentricity"): raise ValueError("Unknown sweep parameter.")
            if not (3 <= n <= 60): raise ValueError("Sweep needs 3-60 points.")
            from orbital_intelligence.elements import rv_to_coe
            el = rv_to_coe(y0[:3], y0[3:])
            base = ClassicalElements(el.a, el.e, el.i, 0.0, el.argp, 0.0)
            rows = parameter_sweep_rows(base, par, np.linspace(float(q["start"]), float(q["stop"]), n), numeric_days=days)
            ycols = q.get("plot") or ["period_min", "raan_rate_deg_day"]
            return _dump({"ok": True, "action": act, "parameter": par, "rows": rows, "figures": {"sweep": F.sweep_figure(rows, par, ycols)}})
        raise ValueError(f"Unknown action {act}")
    except Exception as e:                                   # noqa: BLE001
        return _err(e)


def meta() -> str:
    """Static data for the UI: defaults, sites, corridors, versions."""
    return _dump({"ok": True, "default_config": default_config(), "sites": {n: {"lat_deg": s.lat_deg, "lon_deg": s.lon_deg} for n, s in SITES.items()},
                  "corridors": RANGE_CORRIDORS, "engine_version": ENGINE_VERSION, "bridge_version": BRIDGE_VERSION, "schema": SCHEMA})
