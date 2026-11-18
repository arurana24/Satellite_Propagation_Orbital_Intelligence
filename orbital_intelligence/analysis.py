"""Mission analysis: osculating-element history, two-body vs J2 comparison, parameter sweeps, numerical validation."""
from __future__ import annotations

import math
import numpy as np

from .constants import MU, RE, DEG, OMEGA_E
from .elements import (ClassicalElements, rv_to_coe, kepler_propagate, j2_secular_rates, sun_sync_inclination,
                       circular_velocity, vis_viva, period)
from .propagators import propagate, invariants_drift, convergence_study


def orbit_summary(el: ClassicalElements) -> dict:
    """Derived scalar orbit parameters (km, km/s, s)."""
    rates = j2_secular_rates(el.a, el.e, el.i)
    isso = sun_sync_inclination(el.a, el.e)
    return {"a_km": el.a, "e": el.e, "i_deg": math.degrees(el.i), "raan_deg": math.degrees(el.raan), "argp_deg": math.degrees(el.argp),
            "nu_deg": math.degrees(el.nu), "perigee_alt_km": el.perigee_alt, "apogee_alt_km": el.apogee_alt,
            "period_min": el.period / 60.0, "mean_motion_rev_day": 86400.0 / el.period,
            "v_perigee_kms": vis_viva(el.rp, el.a), "v_apogee_kms": vis_viva(el.ra, el.a),
            "v_circular_kms": circular_velocity(el.a), "energy_km2s2": el.energy,
            "h_km2s": math.sqrt(MU * el.p), "raan_rate_deg_day": math.degrees(rates["raan_dot"]) * 86400,
            "argp_rate_deg_day": math.degrees(rates["argp_dot"]) * 86400,
            "sso_inclination_deg": None if isso is None else math.degrees(isso)}


def osculating_series(y) -> dict:
    """Osculating elements along a trajectory (degrees, km). `u` = argument of latitude (robust for circular orbits)."""
    n = len(y)
    out = {k: np.empty(n) for k in ("a", "e", "i", "raan", "argp", "nu", "u")}
    for k in range(n):
        el = rv_to_coe(y[k, :3], y[k, 3:])
        out["a"][k], out["e"][k] = el.a, el.e
        out["i"][k], out["raan"][k] = math.degrees(el.i), math.degrees(el.raan)
        out["argp"][k], out["nu"][k] = math.degrees(el.argp), math.degrees(el.nu)
        out["u"][k] = (out["argp"][k] + out["nu"][k]) % 360.0
    return out


def _secular_slope_deg_per_day(t, ang_deg):
    return float(np.polyfit(t, np.degrees(np.unwrap(np.radians(ang_deg))), 1)[0] * 86400.0)


def compare_twobody_j2(r0, v0, duration_s, dt_s=60.0, method="dop853") -> dict:
    """Propagate the same state with both force models; report separation growth and secular drifts."""
    tb = propagate(r0, v0, duration_s, dt_s, "twobody", method)
    j2 = propagate(r0, v0, duration_s, dt_s, "j2", method)
    d = j2.r - tb.r
    el0 = rv_to_coe(r0, v0)
    osc_tb, osc_j2 = osculating_series(tb.y), osculating_series(j2.y)
    ana = j2_secular_rates(el0.a, el0.e, el0.i)
    out = {"t": tb.t, "sep_km": np.linalg.norm(d, axis=1), "twobody": tb, "j2": j2, "osc_twobody": osc_tb, "osc_j2": osc_j2,
           "final_sep_km": float(np.linalg.norm(d[-1])), "max_sep_km": float(np.max(np.linalg.norm(d, axis=1))),
           "raan_rate_analytical_deg_day": math.degrees(ana["raan_dot"]) * 86400,
           "argp_rate_analytical_deg_day": math.degrees(ana["argp_dot"]) * 86400}
    if duration_s >= 0.5 * el0.period:
        out["raan_rate_measured_deg_day"] = _secular_slope_deg_per_day(j2.t, osc_j2["raan"]) if el0.i > 1e-3 else 0.0
        if el0.e > 0.01:
            out["argp_rate_measured_deg_day"] = _secular_slope_deg_per_day(j2.t, osc_j2["argp"])
    return out


def parameter_sweep(base: ClassicalElements, parameter: str, values, numeric_days: float = 0.0):
    """pandas.DataFrame version of `parameter_sweep_rows` (pandas is imported only here)."""
    import pandas as pd
    return pd.DataFrame(parameter_sweep_rows(base, parameter, values, numeric_days))


def parameter_sweep_rows(base: ClassicalElements, parameter: str, values, numeric_days: float = 0.0) -> list[dict]:
    """Sweep altitude (circular, km), inclination (deg) or eccentricity (perigee altitude held).
    Analytical metrics always; optional numerical J2-vs-two-body separation after `numeric_days`."""
    rows = []
    for v in values:
        if parameter == "altitude_km":
            el = ClassicalElements(RE + v, base.e, base.i, base.raan, base.argp, base.nu)
        elif parameter == "inclination_deg":
            el = ClassicalElements(base.a, base.e, v * DEG, base.raan, base.argp, base.nu)
        elif parameter == "eccentricity":
            rp = base.rp; el = ClassicalElements(rp / (1 - v), v, base.i, base.raan, base.argp, base.nu)
        else:
            raise ValueError("parameter must be altitude_km, inclination_deg or eccentricity")
        s = orbit_summary(el)
        row = {parameter: v, "period_min": s["period_min"], "v_perigee_kms": s["v_perigee_kms"], "v_apogee_kms": s["v_apogee_kms"],
               "energy_km2s2": s["energy_km2s2"], "perigee_alt_km": s["perigee_alt_km"], "apogee_alt_km": s["apogee_alt_km"],
               "raan_rate_deg_day": s["raan_rate_deg_day"], "argp_rate_deg_day": s["argp_rate_deg_day"],
               "sso_inclination_deg": s["sso_inclination_deg"]}
        if numeric_days > 0:
            r0, v0 = el.to_state()
            cmp = compare_twobody_j2(r0, v0, numeric_days * 86400, 120.0)
            row["j2_separation_km"] = cmp["final_sep_km"]
        rows.append(row)
    return rows


def validate_numerics(el: ClassicalElements) -> list[dict]:
    """Automatic numerical self-check of the propagators on the supplied orbit. Each row: name, value, threshold, passed."""
    r0, v0 = el.to_state(); T = el.period; rows = []
    def row(name, val, thr, unit="", cmp="<="):
        ok = val <= thr if cmp == "<=" else val >= thr
        rows.append({"check": name, "value": float(val), "threshold": float(thr), "unit": unit, "passed": bool(ok)})
    ref = kepler_propagate(r0, v0, T)[0]
    d853 = propagate(r0, v0, T, T / 4, "twobody", "dop853")
    row("DOP853 vs analytical Kepler, 1 orbit (position)", np.linalg.norm(d853.r[-1] - ref[:3]), 1e-4, "km")
    rk = propagate(r0, v0, T, T / 4, "twobody", "rk4", rk4_step_s=10.0)
    row("RK4 (10 s) vs analytical Kepler, 1 orbit (position)", np.linalg.norm(rk.r[-1] - ref[:3]), 0.5, "km")
    drift = invariants_drift(propagate(r0, v0, 5 * T, 30.0, "twobody", "dop853"))
    row("Two-body energy drift, 5 orbits (DOP853)", drift["energy_rel_drift"], 1e-8, "rel")
    row("Two-body angular-momentum drift, 5 orbits", drift["angmom_rel_drift"], 1e-8, "rel")
    j = invariants_drift(propagate(r0, v0, 5 * T, 30.0, "j2", "dop853"))
    row("J2 total-energy drift, 5 orbits (DOP853)", j["energy_rel_drift"], 1e-8, "rel")
    row("J2 h_z drift, 5 orbits (DOP853)", j["angmom_rel_drift"], 1e-8, "rel")
    cs = convergence_study(r0, v0, T)
    row("RK4 observed convergence order (expect ~4)", cs["rk4_observed_order"], 3.5, "", ">=")
    if el.i > 0.05 and el.i < math.pi - 0.05:
        c = compare_twobody_j2(r0, v0, 2 * 86400.0, 120.0)
        ana = c["raan_rate_analytical_deg_day"]
        row("J2 RAAN drift vs analytical (relative error)", abs(c["raan_rate_measured_deg_day"] - ana) / max(abs(ana), 1e-9), 0.02, "rel")
    return rows
