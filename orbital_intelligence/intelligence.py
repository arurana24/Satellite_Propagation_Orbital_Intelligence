"""Rule-based mission feasibility engine. Every finding carries the measured value, the
threshold/basis used, an explanation, and (where possible) a concrete suggested correction."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from fractions import Fraction
import math
import numpy as np

from .constants import RE, MU, DEG, OMEGA_E, SIDEREAL_DAY_S, TWO_PI
from .atmosphere import _TAB, _SCALE, _tab_rho
from .elements import ClassicalElements, j2_secular_rates, sun_sync_inclination, hohmann_delta_v
from .groundtrack import horizon_central_angle, sensor_central_angle

SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "error": 3}


@dataclass
class Finding:
    rule: str
    severity: str          # ok | info | warning | error
    title: str
    message: str
    suggestion: str = ""
    value: str = ""
    basis: str = ""

    def as_dict(self): return asdict(self)


@dataclass
class Assessment:
    findings: list

    @property
    def status(self):
        worst = max((SEVERITY_RANK[f.severity] for f in self.findings), default=0)
        return {0: "ok", 1: "ok", 2: "warning", 3: "infeasible"}[worst]

    def by_severity(self, sev): return [f for f in self.findings if f.severity == sev]


def estimate_lifetime_days(alt_km: float, area_to_mass_m2kg: float = 0.01, cd: float = 2.2, reentry_alt_km: float = 120.0):
    """Circular-orbit decay lifetime [days] from da/dt = -rho*B*sqrt(mu*a) (B = Cd*A/m), integrated in altitude.
    Uses a mean-solar-activity exponential atmosphere: accuracy is order-of-magnitude (factor of several)."""
    B = cd * area_to_mass_m2kg
    if alt_km <= reentry_alt_km: return 0.0
    total = 0.0; h = alt_km
    while h > reentry_alt_km:
        hm = h - 0.5
        rho = _tab_rho(hm) * _SCALE
        adot = rho * B * math.sqrt(MU * (RE + hm)) * 1e3           # km/s
        total += 1.0 / adot                                          # seconds per km
        h -= 1.0
        if total / 86400 > 36500: return float("inf")
    return total / 86400.0


def ground_track_repeat(a: float, e: float, i: float, max_days: int = 10, max_revs: int = 200, tol: float = 2e-3):
    """Smallest (revs, days) such that the nodal-period ratio is within `tol` of a repeat cycle
    (J2-corrected nodal period and nodal Earth rotation)."""
    n = math.sqrt(MU / a**3); r = j2_secular_rates(a, e, i)
    n_ds = n + r["argp_dot"] + r["M_dot_extra"]                      # draconic angular rate
    omega_rel = OMEGA_E - r["raan_dot"]                                # Earth rate relative to the node
    ratio = n_ds / omega_rel                                           # revs per nodal day
    best = None
    for d in range(1, max_days + 1):
        k = round(ratio * d)
        if 0 < k <= max_revs and abs(ratio * d - k) / (ratio * d) < tol:
            g = math.gcd(k, d)
            best = (k // g, d // g); break
    return {"revs_per_nodal_day": ratio, "repeat": best}


def evaluate_orbit(el: ClassicalElements, targets=(), half_fov_deg=None, launch_site=None,
                   area_to_mass=0.01, cd=2.2) -> Assessment:
    F: list[Finding] = []
    add = lambda *a, **k: F.append(Finding(*a, **k))
    hp, ha = el.perigee_alt, el.apogee_alt
    inc = math.degrees(el.i); a = el.a
    # --- validity
    if el.rp <= RE:
        add("orbit.validity", "error", "Orbit intersects the Earth", f"Perigee radius {el.rp:.0f} km <= Earth radius {RE:.0f} km.",
            f"Raise the perigee altitude above ~200 km (currently {hp:.0f} km).", f"{hp:.0f} km", "rp > R_E")
    else:
        add("orbit.validity", "ok", "Orbit is geometrically valid", f"Perigee {hp:.0f} km, apogee {ha:.0f} km, e={el.e:.4f}.", value=f"e={el.e:.4f}", basis="0<=e<1, rp>R_E")
    # --- atmosphere
    if el.rp > RE:
        if hp < 120:
            add("atmo.reentry", "error", "Immediate re-entry", f"Perigee altitude {hp:.0f} km is below the ~120 km re-entry interface; the orbit decays within hours.",
                "Raise perigee to at least 300 km (>= 500 km for multi-year life).", f"{hp:.0f} km", "perigee >= 120 km")
        elif hp < 250:
            add("atmo.drag", "warning", "Very short orbital lifetime", f"Perigee altitude {hp:.0f} km: strong drag; lifetime of days to weeks without propulsion.",
                "Raise perigee to >= 350 km, or plan continuous drag make-up thrust.", f"{hp:.0f} km", "perigee >= 250 km")
        if el.e < 0.02 and hp < 1000:
            life = estimate_lifetime_days(0.5 * (hp + ha), area_to_mass, cd)
            txt = ">100 years" if life == float("inf") else (f"{life:.0f} days" if life < 400 else f"{life/365.25:.1f} years")
            sev = "warning" if life < 365 * 2 and hp >= 250 else "info"
            if hp >= 120:
                add("atmo.lifetime", sev, "Estimated drag lifetime", f"About {txt} for A/m={area_to_mass} m^2/kg, Cd={cd} (mean solar activity, order-of-magnitude).",
                    "Raise altitude or reduce A/m to extend life; ~25-year deorbit guideline applies above ~600 km." if sev != "info" else "",
                    txt, "da/dt = -rho*Cd*A/m*sqrt(mu*a)")
    # --- regime
    mean_alt = 0.5 * (hp + ha)
    regime = "LEO" if ha <= 2000 else ("GEO-like" if abs(a - 42164) < 300 and el.e < 0.05 else ("MEO" if hp > 2000 and ha < 35000 else "HEO/transfer"))
    add("regime", "info", f"Orbit regime: {regime}", f"Altitude {hp:.0f} x {ha:.0f} km, period {el.period/60:.1f} min.", value=regime)
    # --- radiation
    if any(lo < h < hi for lo, hi in ((1000, 6000), (13000, 25000)) for h in (hp, ha, mean_alt)) or (hp < 1000 and ha > 6000):
        add("radiation", "warning", "Passes through Van Allen belt regions",
            f"Orbit altitudes {hp:.0f}-{ha:.0f} km overlap the inner (~1000-6000 km) and/or outer (~13000-25000 km) radiation belts.",
            "Use radiation-hardened electronics and shielding, or choose an orbit below ~1000 km.", f"{hp:.0f}-{ha:.0f} km", "belt altitude ranges (approximate, solar-cycle dependent)")
    # --- J2 effects / sun-synchronous
    rates = j2_secular_rates(a, el.e, el.i)
    raan_dpd = math.degrees(rates["raan_dot"]) * 86400
    isso = sun_sync_inclination(a, el.e)
    if isso is not None:
        d = abs(inc - math.degrees(isso))
        if d < 0.3:
            add("sso", "ok", "Orbit is sun-synchronous", f"Node precesses {raan_dpd:+.3f} deg/day (target +0.9856).", value=f"i={inc:.2f} deg")
        else:
            add("sso", "info", "Not sun-synchronous", f"Node drifts {raan_dpd:+.3f} deg/day; a sun-synchronous orbit at this altitude needs i = {math.degrees(isso):.2f} deg.",
                f"Set inclination to {math.degrees(isso):.2f} deg for constant local solar time.", f"i={inc:.2f} deg", "RAAN rate = +0.9856 deg/day")
    else:
        add("sso", "info", "Sun-synchronous orbit impossible at this altitude", "J2 precession cannot reach 0.9856 deg/day at this semi-major axis.", f"Node drift {raan_dpd:+.3f} deg/day")
    if el.e > 0.01 and (abs(inc - 63.435) < 1.0 or abs(inc - 116.565) < 1.0):
        add("critical.inc", "info", "Near critical inclination", f"At i={inc:.2f} deg the J2 apsidal rotation vanishes (frozen apsides, Molniya-type).", value=f"{inc:.2f} deg")
    elif el.e > 0.01:
        add("apsidal", "info", "Apsidal rotation", f"Perigee rotates {math.degrees(rates['argp_dot'])*86400:+.3f} deg/day (J2).", "Use i=63.4 deg / 116.6 deg for frozen apsides." if el.e > 0.1 else "")
    if abs(a - 42164.0) < 100 and el.e < 0.01 and inc < 1.5:
        add("geo", "ok", "Geostationary orbit", f"Period {el.period/3600:.3f} h matches the sidereal day.", value=f"a={a:.0f} km")
    if el.e > 0.3:
        add("eccentric", "info", "Highly eccentric orbit", f"Altitude varies between {hp:.0f} and {ha:.0f} km; sensor resolution and link budget change strongly with position.")
    # --- ground track repeat
    rep = ground_track_repeat(a, el.e, el.i)
    if rep["repeat"] and ha < 2000:
        k, d = rep["repeat"]
        add("repeat", "info", "Repeat ground track nearby", f"{k} revolutions in {d} day(s) repeats the track within tolerance ({rep['revs_per_nodal_day']:.4f} revs/nodal day).",
            value=f"{k}/{d}")
    # --- targets
    max_lat = inc if inc <= 90 else 180 - inc
    for t in targets:
        reach = max_lat + (math.degrees(float(horizon_central_angle(mean_alt, t.min_elev_deg))) if mean_alt > 0 else 0.0)
        view = math.degrees(float(sensor_central_angle(mean_alt, half_fov_deg))) if half_fov_deg else 0.0
        if abs(t.lat_deg) > max_lat + max(math.degrees(float(horizon_central_angle(mean_alt, t.min_elev_deg))), view):
            need = abs(t.lat_deg)
            add("target.lat", "error", f"Target '{t.name}' is unreachable",
                f"Ground track is confined to |lat| <= {max_lat:.1f} deg; target at {t.lat_deg:.1f} deg lies beyond the {max(view, math.degrees(float(horizon_central_angle(mean_alt, t.min_elev_deg)))):.1f} deg footprint reach.",
                f"Increase inclination to at least {max(need - max(view, 0), need - math.degrees(float(horizon_central_angle(mean_alt, t.min_elev_deg)))):.0f} deg (or its supplement), or raise the altitude.",
                f"max lat {max_lat:.1f} deg", "|target lat| <= i + footprint angle")
        elif abs(t.lat_deg) > max_lat:
            add("target.lat", "warning", f"Target '{t.name}' only seen at pass edges", f"Target latitude {t.lat_deg:.1f} deg exceeds max sub-satellite latitude {max_lat:.1f} deg; only low-elevation access is possible.",
                "Raise inclination so the ground track reaches the target latitude.", f"max lat {max_lat:.1f} deg")
    if half_fov_deg:
        sw = 2 * RE * float(sensor_central_angle(mean_alt, half_fov_deg))
        add("sensor.swath", "info", "Sensor swath", f"Nadir swath {sw:.0f} km at {mean_alt:.0f} km altitude (half-FOV {half_fov_deg:.1f} deg).", value=f"{sw:.0f} km")
        eq_shift = 360.0 / max(rep["revs_per_nodal_day"], 1e-9) / 360.0 * 2 * math.pi * RE
        if sw < eq_shift * 0.8 and ha < 2000:
            add("coverage.gaps", "info", "Daily equatorial gaps expected", f"Adjacent equatorial passes are ~{eq_shift:.0f} km apart but the swath is {sw:.0f} km.",
                "Widen the FOV, raise altitude, or accept multi-day revisit.", value=f"{sw:.0f} vs {eq_shift:.0f} km")
    # --- launch site
    if launch_site is not None:
        lat = abs(launch_site.lat_deg)
        if inc < lat - 1e-6 or (180 - inc) < lat - 1e-6 and inc > 90:
            add("launch.inc", "error", "Inclination unreachable from launch site",
                f"A direct ascent from latitude {launch_site.lat_deg:.1f} deg cannot reach i={inc:.1f} deg (minimum {lat:.1f} deg).",
                f"Use i >= {lat:.1f} deg, a different launch site, or budget a plane-change (dog-leg) manoeuvre.", f"{inc:.1f} deg", "i >= |site latitude|")
        else:
            add("launch.inc", "ok", "Inclination reachable from launch site", f"i={inc:.1f} deg >= site latitude {lat:.1f} deg.")
        if hp > 150:
            h = hohmann_delta_v(RE + 200, RE + hp)
            add("dv.estimate", "info", "Orbit-raising cost from 200 km parking orbit", f"Hohmann to {hp:.0f} km: {h['total']*1000:.0f} m/s.", value=f"{h['total']*1000:.0f} m/s")
    return Assessment(F)


def evaluate_launch(summary: dict, target_alt_km=None, target_incl_deg=None, max_payload_g=6.0) -> Assessment:
    """Rule checks on an ascent simulation summary."""
    F: list[Finding] = []; add = lambda *a, **k: F.append(Finding(*a, **k))
    if not summary.get("reached_target_energy") and "perigee_alt_km" not in summary:
        add("launch.orbit", "error", "No bound orbit achieved", "Vehicle did not reach orbital energy.", "Reduce payload, add propellant, or lower the target altitude.")
    elif summary.get("perigee_alt_km", -1) < 160:
        add("launch.orbit", "error", "Burnout perigee too low", f"Perigee {summary.get('perigee_alt_km', float('nan')):.0f} km: the payload would re-enter within the first orbits.",
            "Raise cutoff energy or add a circularisation burn at apogee.", f"{summary.get('perigee_alt_km', float('nan')):.0f} km", "perigee >= 160 km")
    else:
        add("launch.orbit", "ok", "Stable orbit achieved", f"Burnout orbit {summary['perigee_alt_km']:.0f} x {summary['apogee_alt_km']:.0f} km.")
    if target_incl_deg is not None and "inclination_deg" in summary:
        d = summary["inclination_deg"] - target_incl_deg
        add("launch.incl", "ok" if abs(d) < 0.5 else "warning", "Inclination accuracy", f"Achieved {summary['inclination_deg']:.2f} deg vs target {target_incl_deg:.2f} deg ({d:+.2f}).",
            "Adjust launch azimuth." if abs(d) >= 0.5 else "")
    if target_alt_km is not None and "perigee_alt_km" in summary:
        d = summary["perigee_alt_km"] - target_alt_km
        add("launch.alt", "ok" if abs(d) < 0.1 * target_alt_km else "warning", "Altitude accuracy", f"Perigee {summary['perigee_alt_km']:.0f} km vs target {target_alt_km:.0f} km.")
    g = summary.get("max_accel_g", 0.0)
    add("launch.g", "warning" if g > max_payload_g else "ok", "Peak axial acceleration", f"{g:.2f} g (typical payload limit {max_payload_g:.0f} g).",
        "Throttle down the final stage or add propellant offload." if g > max_payload_g else "", f"{g:.2f} g")
    if summary.get("in_corridor") is False:
        lo, hi = summary["corridor_deg"]
        add("launch.range", "warning", "Launch azimuth outside the range-safety corridor",
            f"Azimuth {summary['launch_azimuth_deg']:.1f} deg ({summary.get('heading', '')}) is {summary['distance_deg']:.0f} deg outside the "
            f"approximate {lo:.0f}-{hi:.0f} deg corridor for this site, so the first stage would fly over land or a neighbouring country.",
            "Real launches use a yaw (dog-leg) manoeuvre or a different site; this model does not simulate a dog-leg. Try the other azimuth branch.",
            f"{summary['launch_azimuth_deg']:.1f} deg", "approximate corridor (planning value, not official)")
    elif summary.get("in_corridor") is True:
        add("launch.range", "ok", "Launch azimuth inside corridor", f"Azimuth {summary['launch_azimuth_deg']:.1f} deg is inside the approximate corridor.")
    add("launch.maxq", "info", "Max dynamic pressure", f"{summary.get('max_q_kpa', 0):.1f} kPa at {summary.get('max_q_alt_km', 0):.1f} km altitude (t={summary.get('max_q_time_s', 0):.0f} s).")
    return Assessment(F)
