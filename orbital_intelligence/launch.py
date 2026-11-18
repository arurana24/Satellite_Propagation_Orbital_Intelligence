"""Launch analysis: analytical calculator + 3-DOF ascent simulation (staging, drag, rotating
atmosphere, thrust vs. back-pressure) and automatic hand-off of the burnout state to orbit propagation.

The ascent is integrated directly in the inertial (ECI) frame, so the burnout state can be passed
unchanged to `propagate`. Units: km, s, kg, thrust in kN (kN/kg == km/s^2)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

from .constants import MU, RE, J2, OMEGA_E, G0, DEG
from .atmosphere import atmosphere
from .elements import ClassicalElements, rv_to_coe, circular_velocity, OrbitError
from .frames import gmst_at, geodetic_to_ecef, ecef_to_eci, eci_to_ecef, ecef_to_geodetic

_W = np.array([0.0, 0.0, OMEGA_E])


class LaunchError(ValueError):
    pass


# ----------------------------------------------------------------------------- data classes
@dataclass(frozen=True)
class Stage:
    name: str
    dry_mass_kg: float
    prop_mass_kg: float
    thrust_sl_kn: float
    thrust_vac_kn: float
    isp_vac_s: float

    @property
    def mdot(self): return 1000.0 * self.thrust_vac_kn / (self.isp_vac_s * G0)   # kg/s
    @property
    def burn_time(self): return self.prop_mass_kg / self.mdot
    @property
    def isp_sl_s(self): return self.isp_vac_s * self.thrust_sl_kn / self.thrust_vac_kn


@dataclass(frozen=True)
class Vehicle:
    name: str
    stages: tuple
    payload_kg: float
    ref_area_m2: float
    cd_scale: float = 1.0

    @property
    def liftoff_mass(self): return self.payload_kg + sum(s.dry_mass_kg + s.prop_mass_kg for s in self.stages)

    def stage_delta_vs(self):
        """Ideal (rocket-equation) delta-v per stage [km/s]; stage 1 uses mean of SL and vacuum Isp."""
        out = []; m = self.liftoff_mass
        for k, s in enumerate(self.stages):
            isp = 0.5 * (s.isp_sl_s + s.isp_vac_s) if k == 0 else s.isp_vac_s
            mf = m - s.prop_mass_kg
            out.append(isp * G0 * math.log(m / mf) / 1000.0)
            m = mf - s.dry_mass_kg
        return out

    @property
    def total_delta_v(self): return sum(self.stage_delta_vs())


DEFAULT_VEHICLE = Vehicle(
    "Generic two-stage medium launcher (illustrative, Falcon-9-class parameters)",
    (Stage("Stage 1", 25600, 395700, 7607, 8227, 311),
     Stage("Stage 2", 4000, 92670, 934, 934, 348)),
    payload_kg=5000, ref_area_m2=10.75)


@dataclass(frozen=True)
class LaunchSite:
    name: str
    lat_deg: float
    lon_deg: float
    alt_km: float = 0.0


SITES = {s.name: s for s in (
    LaunchSite("Satish Dhawan (Sriharikota)", 13.7199, 80.2304), LaunchSite("Cape Canaveral", 28.5623, -80.5774),
    LaunchSite("Vandenberg", 34.7420, -120.5724), LaunchSite("Kourou", 5.2322, -52.7750),
    LaunchSite("Baikonur", 45.9650, 63.3050), LaunchSite("Wallops", 37.9402, -75.4664),
    LaunchSite("Tanegashima", 30.4009, 130.9753), LaunchSite("Jiuquan", 40.9606, 100.2784))}


@dataclass(frozen=True)
class AscentProgram:
    azimuth_deg: float = 90.0          # launch azimuth in the Earth-relative frame (from north, cw)
    vertical_time_s: float = 8.0
    kick_angle_deg: float = 1.5        # thrust offset from velocity vector during the kick
    kick_duration_s: float = 8.0
    upper_pitch_end_deg: float = 0.0   # upper-stage elevation above local horizontal at nominal burn end
    stage_coast_s: float = 3.0
    upper_ramp_fraction: float = 0.85  # pitch ramp completes at this fraction of the nominal upper-stage burn
    use_j2: bool = True


# ----------------------------------------------------------------------------- analytical
def inertial_azimuths(incl_deg: float, lat_deg: float):
    """Both inertial launch azimuths [deg] that reach inclination `incl_deg` by direct ascent, as
    {"north": beta, "south": 180 - beta}; None if i < |lat| (infeasible without a dog-leg).

    For a prograde orbit the two branches are north-east and south-east; for a retrograde orbit they are
    north-west and south-west. Both give the same inclination (they differ in the ascending/descending
    pass over the site). Earlier versions returned only the north branch (defect D3)."""
    c = math.cos(incl_deg * DEG) / math.cos(lat_deg * DEG)
    if abs(c) > 1.0: return None
    b = math.degrees(math.asin(c))
    return {"north": b % 360.0, "south": (180.0 - b) % 360.0}


def inertial_azimuth(incl_deg: float, lat_deg: float, branch: str = "north"):
    """Inertial launch azimuth [deg] of the chosen branch ('north' or 'south'); None if infeasible."""
    b = inertial_azimuths(incl_deg, lat_deg)
    return None if b is None else b[branch]


def rotating_azimuth(incl_deg: float, lat_deg: float, v_orb_kms: float, branch: str = "north"):
    """Earth-relative azimuth that accounts for launch-site rotation velocity."""
    b = inertial_azimuth(incl_deg, lat_deg, branch)
    if b is None: return None
    b = b * DEG; vs = OMEGA_E * RE * math.cos(lat_deg * DEG)
    return math.degrees(math.atan2(v_orb_kms * math.sin(b) - vs, v_orb_kms * math.cos(b))) % 360.0


# Approximate permitted launch-azimuth corridors (deg from north, clockwise; Earth-relative), used only for
# a range-safety *warning* and to choose the default azimuth branch. These are planning-level values quoted
# in general astronautics references, NOT official range-safety data: verify against a primary source
# before relying on them. Sites without an entry get no corridor check.
RANGE_CORRIDORS = {
    "Cape Canaveral": (35.0, 120.0),
    "Vandenberg": (147.0, 201.0),
    "Kourou": (349.5, 93.5),                       # wraps through north
    "Satish Dhawan (Sriharikota)": (100.0, 140.0), # east / south-east over the Bay of Bengal (approximate)
}


def _az_in(az, lo, hi):
    az, lo, hi = az % 360.0, lo % 360.0, hi % 360.0
    return lo <= az <= hi if lo <= hi else (az >= lo or az <= hi)


def _az_dist(az, lo, hi):
    """Angular distance [deg] from azimuth to the corridor (0 if inside)."""
    if _az_in(az, lo, hi): return 0.0
    d = lambda a, b: abs((a - b + 180.0) % 360.0 - 180.0)
    return min(d(az, lo), d(az, hi))


def corridor_check(site, azimuth_deg: float) -> dict:
    """Range-safety corridor status for an Earth-relative azimuth at a site."""
    cor = RANGE_CORRIDORS.get(getattr(site, "name", None))
    if cor is None:
        return {"corridor_deg": None, "in_corridor": None, "distance_deg": None}
    return {"corridor_deg": cor, "in_corridor": bool(_az_in(azimuth_deg, *cor)), "distance_deg": float(_az_dist(azimuth_deg, *cor))}


def choose_branch(site, incl_deg: float, v_orb_kms: float, branch: str = "auto") -> tuple[str, str]:
    """Pick the azimuth branch. 'north'/'south' are honoured as given. 'auto' picks the branch inside (or
    closest to) the site's corridor; with no corridor data it picks the south branch for retrograde/polar
    orbits (i >= 90 deg) and the north branch otherwise, and says so. Returns (branch, reason)."""
    if branch in ("north", "south"):
        return branch, "chosen by user"
    cor = RANGE_CORRIDORS.get(getattr(site, "name", None))
    azs = {b: rotating_azimuth(incl_deg, site.lat_deg, v_orb_kms, b) for b in ("north", "south")}
    if azs["north"] is None:
        return "north", "infeasible"
    if cor is not None:
        dn, ds = _az_dist(azs["north"], *cor), _az_dist(azs["south"], *cor)
        b = "south" if ds < dn else "north"
        return b, f"closest to the approximate {cor[0]:.0f}-{cor[1]:.0f} deg corridor for {site.name}"
    b = "south" if incl_deg >= 90.0 else "north"
    return b, "no corridor data for this site; default branch for this inclination"


def analytical_launch(lat_deg, alt_km, incl_deg, vehicle: Vehicle | None = None, losses_kms: float = 1.0, branch: str = "north"):
    """First-order launch budget for a circular target orbit.

    required = orbital-velocity vector change (Earth-rotation credit included)
             + climb term (energy-equivalent speed minus circular speed, grows with altitude)
             + `losses_kms`, a *typical-value assumption* for drag, steering and gravity-turn inefficiency
    (the ascent simulation reports the actual losses for a given vehicle)."""
    r = RE + alt_km; v = circular_velocity(r)
    beta = inertial_azimuth(incl_deg, lat_deg, branch)
    out = {"v_circ_kms": v, "feasible": beta is not None, "min_inclination_deg": abs(lat_deg),
           "site_speed_kms": OMEGA_E * RE * math.cos(lat_deg * DEG)}
    if beta is None:
        out["message"] = (f"Direct launch to i={incl_deg:.1f} deg from latitude {lat_deg:.1f} deg is impossible "
                          f"(minimum {abs(lat_deg):.1f} deg without a dog-leg manoeuvre).")
        return out
    b = beta * DEG; vs = out["site_speed_kms"]
    dv_ideal = math.hypot(v * math.sin(b) - vs, v * math.cos(b))
    v_eq = math.sqrt(2 * MU * (1 / RE - 1 / (2 * r)))
    climb = v_eq - v
    out.update(climb_term_kms=climb, inertial_azimuth_deg=beta, relative_azimuth_deg=rotating_azimuth(incl_deg, lat_deg, v, branch), azimuth_branch=branch,
               rotation_benefit_kms=v - dv_ideal, dv_ideal_kms=dv_ideal, dv_losses_assumed_kms=losses_kms,
               dv_required_kms=dv_ideal + climb + losses_kms)
    if vehicle is not None:
        stages = vehicle.stage_delta_vs()
        out.update(stage_dv_kms=stages, vehicle_dv_kms=sum(stages), margin_kms=sum(stages) - out["dv_required_kms"])
        lo, hi = 0.0, 5 * vehicle.liftoff_mass
        def margin(pl):
            return sum(replace(vehicle, payload_kg=pl).stage_delta_vs()) - out["dv_required_kms"]
        if margin(0.0) < 0: out["max_payload_kg"] = 0.0
        else:
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if margin(mid) >= 0 else (lo, mid)
            out["max_payload_kg"] = lo
    return out


# ----------------------------------------------------------------------------- ascent simulation
def _cd(mach: float) -> float:
    if mach < 0.8: return 0.30
    if mach < 1.2: return 0.30 + (mach - 0.8) / 0.4 * 0.25 if mach < 1.0 else 0.55 - (mach - 1.0) / 0.2 * 0.05
    return max(0.22, 0.50 - 0.10 * (mach - 1.2))


def _enu(r):
    up = r / np.linalg.norm(r)
    east = np.cross([0, 0, 1.0], up); east /= np.linalg.norm(east)
    return east, np.cross(up, east), up


class _Ascent:
    def __init__(self, veh: Vehicle, prog: AscentProgram):
        self.veh, self.prog = veh, prog
        self.k = 0; self.t_ign = 0.0; self.gamma_sep = 0.0; self.thrusting = True

    def thrust_dir(self, t, r, v):
        east, north, up = _enu(r)
        az = math.radians(self.prog.azimuth_deg)
        az_dir = math.sin(az) * east + math.cos(az) * north
        if self.k == 0:
            if t < self.prog.vertical_time_s: return up
            vrel = v - np.cross(_W, r); sp = np.linalg.norm(vrel)
            if sp < 1e-3: return up
            vh = vrel / sp
            d = self.prog.kick_angle_deg * DEG if t < self.prog.vertical_time_s + self.prog.kick_duration_s else 0.0
            hh = az_dir - np.dot(az_dir, vh) * vh; hn = np.linalg.norm(hh)
            if d == 0.0 or hn < 1e-9: return vh
            return math.cos(d) * vh + math.sin(d) * hh / hn
        vh = v - np.dot(v, up) * up; n = np.linalg.norm(vh)
        h_dir = vh / n if n > 1e-6 else az_dir
        st = self.veh.stages[self.k]
        frac = min(1.0, max(0.0, (t - self.t_ign) / (self.prog.upper_ramp_fraction * st.burn_time)))
        e = self.gamma_sep + (self.prog.upper_pitch_end_deg * DEG - self.gamma_sep) * frac
        return math.cos(e) * h_dir + math.sin(e) * up

    def forces(self, t, y):
        """Returns (a_total, a_thrust_vec, a_drag_vec, rho, q_pa, mach)."""
        r, v, m = y[:3], y[3:6], y[6]
        rn = np.linalg.norm(r); alt = rn - RE
        a_g = -MU * r / rn**3
        if self.prog.use_j2:
            z2 = 5 * r[2] ** 2 / rn**2
            a_g = a_g + 1.5 * J2 * MU * RE**2 / rn**5 * np.array([r[0] * (z2 - 1), r[1] * (z2 - 1), r[2] * (z2 - 3)])
        rho, P, T, cs = atmosphere(alt)
        vrel = v - np.cross(_W, r); vr = np.linalg.norm(vrel)
        mach = vr * 1000.0 / cs
        a_d = -0.5 * rho * self.veh.cd_scale * _cd(mach) * self.veh.ref_area_m2 / m * 1e3 * vr * vrel
        a_t = np.zeros(3)
        if self.thrusting:
            st = self.veh.stages[self.k]
            T_kn = st.thrust_vac_kn - (st.thrust_vac_kn - st.thrust_sl_kn) * min(1.0, P / 101325.0)
            a_t = T_kn / m * self.thrust_dir(t, r, v)
        q = 0.5 * rho * (vr * 1000.0) ** 2
        return a_g + a_t + a_d, a_t, a_d, rho, q, mach

    def rhs(self, t, y):
        a = self.forces(t, y)[0]
        md = -self.veh.stages[self.k].mdot if self.thrusting else 0.0
        return np.concatenate((y[3:6], a, [md]))


@dataclass
class LaunchResult:
    epoch: datetime
    site: LaunchSite
    vehicle: Vehicle
    program: AscentProgram
    t: np.ndarray
    samples: dict
    events: list
    status: str
    reached_target_energy: bool
    burnout_r: np.ndarray
    burnout_v: np.ndarray
    burnout_mass_kg: float
    burnout_time_s: float
    summary: dict = field(default_factory=dict)

    @property
    def burnout_epoch(self): return self.epoch + timedelta(seconds=self.burnout_time_s)

    @property
    def burnout_elements(self):
        try: return rv_to_coe(self.burnout_r, self.burnout_v)
        except OrbitError: return None

    def handoff(self) -> dict:
        """State to feed straight into `propagate` (epoch, ECI r/v, elements or None if not bound)."""
        return {"epoch": self.burnout_epoch, "r": self.burnout_r, "v": self.burnout_v, "elements": self.burnout_elements}


def simulate_ascent(vehicle: Vehicle, site: LaunchSite, program: AscentProgram, epoch: datetime,
                    cutoff_a_km: float | None = None, dt_out: float = 1.0, rtol: float = 1e-9) -> LaunchResult:
    """Integrate the powered ascent. If `cutoff_a_km` is given the last stage is shut down (guided cutoff)
    as soon as the osculating semi-major axis reaches it; otherwise it burns to propellant depletion."""
    s0 = vehicle.stages[0]
    m0 = vehicle.liftoff_mass
    if s0.thrust_sl_kn * 1000.0 / (m0 * G0) <= 1.0:
        raise LaunchError(f"Liftoff thrust-to-weight is {s0.thrust_sl_kn*1000/(m0*G0):.2f} (<= 1): vehicle cannot lift off.")
    th0 = float(gmst_at(epoch, 0.0))
    r0 = ecef_to_eci(geodetic_to_ecef(site.lat_deg * DEG, site.lon_deg * DEG, site.alt_km), th0)
    v0 = np.cross(_W, r0)
    y = np.concatenate((r0, v0, [m0])); t = 0.0
    sim = _Ascent(vehicle, program)
    T_list, Y_list, K_list, TI_list, TH_list = [], [], [], [], []
    events = [(0.0, "Liftoff")]; status = "Burnout (propellant depleted)"; reached = False
    eps_target = -MU / (2 * cutoff_a_km) if cutoff_a_km else None
    impact = lambda tt, yy: np.linalg.norm(yy[:3]) - RE + 0.2
    impact.terminal = True; impact.direction = -1
    n = len(vehicle.stages)

    def run(t_start, y_start, t_end, ev, k, thrusting, t_ign):
        sim.k, sim.thrusting, sim.t_ign = k, thrusting, t_ign
        grid = np.arange(t_start, t_end, dt_out)
        grid = np.append(grid, t_end) if grid.size == 0 or grid[-1] < t_end - 1e-9 else grid
        sol = solve_ivp(sim.rhs, (t_start, t_end), y_start, method="DOP853", t_eval=grid, events=ev,
                        rtol=rtol, atol=np.array([1e-9] * 3 + [1e-12] * 3 + [1e-6]))
        if not sol.success: raise RuntimeError(f"Ascent integration failed: {sol.message}")
        tt, yy = list(sol.t), list(sol.y.T)
        hit = None
        for e in sol.t_events:
            if len(e):
                hit = (e[0], sol.y_events[[i for i, x in enumerate(sol.t_events) if len(x)][0]][0]); break
        if hit is not None:
            if hit[0] > tt[-1] + 1e-9: tt.append(hit[0]); yy.append(hit[1])
            else: tt[-1], yy[-1] = hit[0], hit[1]
        for q, w in zip(tt, yy):
            T_list.append(q); Y_list.append(w); K_list.append(k if thrusting else -1)
            TI_list.append(t_ign); TH_list.append(sim.gamma_sep)
        return tt[-1], yy[-1], hit

    for k, st in enumerate(vehicle.stages):
        up = y[:3] / np.linalg.norm(y[:3])
        sim.gamma_sep = math.asin(np.clip(np.dot(y[3:6], up) / max(np.linalg.norm(y[3:6]), 1e-9), -1, 1))
        events.append((t, f"{st.name} ignition"))
        last = k == n - 1
        ev = [impact]
        if last and eps_target is not None:
            def cut(tt, yy, e=eps_target): return 0.5 * np.dot(yy[3:6], yy[3:6]) - MU / np.linalg.norm(yy[:3]) - e
            cut.terminal = True; cut.direction = 1; ev.append(cut)
        t_end = t + st.burn_time
        t_new, y_new, hit = run(t, y, t_end, ev, k, True, t)
        t, y = t_new, y_new
        if hit is not None:
            if np.linalg.norm(y[:3]) - RE < 0.5:
                status = "FAILURE: vehicle impacted the ground during ascent"; events.append((t, "Ground impact")); break
            reached = True; status = "Guided cutoff: target energy reached"
            events.append((t, f"{st.name} guided cutoff (target energy)")); break
        events.append((t, f"{st.name} burnout"))
        if not last:
            y = y.copy(); y[6] -= st.dry_mass_kg
            events.append((t, f"{st.name} separation"))
            t_new, y, hit = run(t, y, t + program.stage_coast_s, [impact], k + 1, False, t)
            t = t_new
            if hit is not None:
                status = "FAILURE: vehicle impacted the ground during staging coast"; events.append((t, "Ground impact")); break

    Tt, Yy = np.array(T_list), np.array(Y_list)
    samples = _derive(sim, Tt, Yy, np.array(K_list), np.array(TI_list), np.array(TH_list), epoch, site)
    res = LaunchResult(epoch, site, vehicle, program, Tt, samples, events, status, reached,
                       Yy[-1, :3].copy(), Yy[-1, 3:6].copy(), float(Yy[-1, 6]), float(Tt[-1]))
    res.summary = _summarise(res, m0)
    return res


def _derive(sim, T, Y, K, TI, TH, epoch, site):
    n = len(T)
    q = np.zeros(n); mach = np.zeros(n); gload = np.zeros(n); drag = np.zeros(n)
    for i in range(n):
        sim.k = max(K[i], 0); sim.thrusting = K[i] >= 0; sim.t_ign = TI[i]; sim.gamma_sep = TH[i]
        _, at, ad, rho, qq, mc = sim.forces(T[i], Y[i])
        q[i], mach[i] = qq, mc
        gload[i] = np.linalg.norm(at + ad) * 1000.0 / G0
        drag[i] = np.linalg.norm(ad) * 1000.0 / G0
    r, v = Y[:, :3], Y[:, 3:6]
    rn = np.linalg.norm(r, axis=1)
    th = gmst_at(epoch, T)
    lat, lon, h = ecef_to_geodetic(eci_to_ecef(r, th))
    vrel = v - np.cross(_W, r)
    up = r / rn[:, None]
    fpa_in = np.degrees(np.arcsin(np.clip(np.sum(v * up, axis=1) / np.linalg.norm(v, axis=1), -1, 1)))
    la0, lo0 = site.lat_deg * DEG, site.lon_deg * DEG
    cosd = np.sin(la0) * np.sin(lat) + np.cos(la0) * np.cos(lat) * np.cos(lon - lo0)
    eps = 0.5 * np.sum(v * v, axis=1) - MU / rn
    return {"t": T, "r": r, "v": v, "mass": Y[:, 6], "alt_km": rn - RE, "lat_deg": np.degrees(lat), "lon_deg": np.degrees(lon),
            "speed_rel_kms": np.linalg.norm(vrel, axis=1), "speed_inertial_kms": np.linalg.norm(v, axis=1),
            "fpa_deg": fpa_in, "q_kpa": q / 1000.0, "mach": mach, "accel_g": gload, "drag_g": drag,
            "downrange_km": RE * np.arccos(np.clip(cosd, -1, 1)), "stage": K, "sma_km": -MU / (2 * eps)}


def _summarise(res: LaunchResult, m0: float) -> dict:
    s = res.samples; i = int(np.argmax(s["q_kpa"]))
    el = res.burnout_elements
    dv_used = 0.0
    # delta-v actually bought by the engines (Tsiolkovsky with effective Isp over thrusting samples)
    mass = s["mass"]; k = s["stage"]
    for j in range(1, len(mass)):
        if k[j] >= 0 and mass[j] < mass[j - 1]:
            st = res.vehicle.stages[k[j]]
            P = atmosphere(s["alt_km"][j])[1]
            T = st.thrust_vac_kn - (st.thrust_vac_kn - st.thrust_sl_kn) * min(1.0, P / 101325.0)
            dv_used += T * 1000.0 / st.mdot / 1000.0 * math.log(mass[j - 1] / mass[j])
    gain = float(np.linalg.norm(s["v"][-1] - s["v"][0]))   # vector velocity change
    prop_used = float(sum(mass[j - 1] - mass[j] for j in range(1, len(mass)) if k[j] >= 0))
    out = {"burnout_time_s": res.burnout_time_s, "burnout_alt_km": float(s["alt_km"][-1]),
           "burnout_speed_inertial_kms": float(s["speed_inertial_kms"][-1]), "burnout_fpa_deg": float(s["fpa_deg"][-1]),
           "downrange_km": float(s["downrange_km"][-1]), "max_q_kpa": float(s["q_kpa"][i]), "max_q_time_s": float(s["t"][i]),
           "max_q_alt_km": float(s["alt_km"][i]), "max_accel_g": float(np.max(s["accel_g"])),
           "propellant_used_kg": prop_used,
           "burnout_mass_kg": res.burnout_mass_kg, "engine_dv_kms": dv_used, "velocity_gain_kms": float(gain),
           "total_losses_kms": float(dv_used - gain), "reached_target_energy": res.reached_target_energy,
           "launch_azimuth_deg": float(res.program.azimuth_deg % 360.0),
           "heading": "north-going" if math.cos(res.program.azimuth_deg * DEG) >= 0 else "south-going"}
    out.update(corridor_check(res.site, res.program.azimuth_deg))
    if el is not None:
        out.update(perigee_alt_km=el.perigee_alt, apogee_alt_km=el.apogee_alt, inclination_deg=math.degrees(el.i),
                   eccentricity=el.e, sma_km=el.a)
    return out


# ----------------------------------------------------------------------------- guidance tuning
def tune_ascent(vehicle: Vehicle, site: LaunchSite, target_alt_km: float, incl_deg: float, epoch: datetime,
                base: AscentProgram | None = None, branch: str = "auto"):
    """Find (kick angle, upper-stage end pitch, azimuth) so a guided cutoff lands on a circular orbit of the target altitude.
    `branch` = 'auto' | 'north' | 'south' selects the azimuth branch (see choose_branch).
    Returns (program, LaunchResult, converged: bool)."""
    a_t = RE + target_alt_km
    br, _ = choose_branch(site, incl_deg, circular_velocity(a_t), branch)
    az = rotating_azimuth(incl_deg, site.lat_deg, circular_velocity(a_t), br)
    if az is None:
        raise LaunchError(f"Inclination {incl_deg:.1f} deg is unreachable from latitude {site.lat_deg:.1f} deg.")
    base = replace(base or AscentProgram(), azimuth_deg=az)

    def prog_of(x):
        return replace(base, kick_angle_deg=x[0], upper_pitch_end_deg=x[1], azimuth_deg=x[2])

    def resid(x):
        try: r = simulate_ascent(vehicle, site, prog_of(x), epoch, cutoff_a_km=a_t, dt_out=5.0, rtol=1e-7)
        except Exception: return [1e3, 1e3, 1e3]
        s = r.summary
        if not r.reached_target_energy or "inclination_deg" not in s:
            return [1e2, 1e2, 1e2]
        return [(s["burnout_alt_km"] - target_alt_km) / 10.0, s["burnout_fpa_deg"], s["inclination_deg"] - incl_deg]

    best = None
    for x0 in ([1.2, -10.0], [1.0, 0.0], [1.4, -20.0], [0.9, -5.0], [1.6, -25.0]):
        sol = least_squares(resid, x0 + [base.azimuth_deg], bounds=([0.2, -60.0, 0.0], [3.0, 40.0, 360.0]),
                            diff_step=1e-2, xtol=1e-8, ftol=1e-8, max_nfev=60)
        if best is None or sol.cost < best.cost: best = sol
        if best.cost < 1e-3: break
    prog = prog_of(best.x)
    result = simulate_ascent(vehicle, site, prog, epoch, cutoff_a_km=a_t)
    s = result.summary
    ok = (result.reached_target_energy and abs(s["burnout_alt_km"] - target_alt_km) < 0.05 * target_alt_km + 10
          and abs(s["burnout_fpa_deg"]) < 0.5 and abs(s.get("inclination_deg", 999) - incl_deg) < 0.3)
    return prog, result, bool(ok)
