"""Ground track, target access (AOS/LOS), revisit statistics, sensor footprint and region coverage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np

from .constants import RE, DEG
from .frames import gmst_at, eci_to_ecef, ecef_to_geodetic, look_angles, geodetic_to_ecef


@dataclass(frozen=True)
class GroundTarget:
    name: str
    lat_deg: float
    lon_deg: float
    alt_km: float = 0.0
    min_elev_deg: float = 10.0


def ground_track(t, r_eci, epoch: datetime) -> dict:
    """Sub-satellite points. Returns lat/lon [deg], geodetic altitude [km], and ECEF positions."""
    r_ecef = eci_to_ecef(r_eci, gmst_at(epoch, t))
    lat, lon, h = ecef_to_geodetic(r_ecef)
    return {"lat": np.degrees(lat), "lon": np.degrees(lon), "alt": h, "r_ecef": r_ecef}


def split_dateline(lon, lat):
    """Insert NaN at antimeridian jumps so line plots don't wrap across the map."""
    lon = np.asarray(lon, float); lat = np.asarray(lat, float)
    jumps = np.flatnonzero(np.abs(np.diff(lon)) > 180.0) + 1
    return np.insert(lon, jumps, np.nan), np.insert(lat, jumps, np.nan)


@dataclass
class Pass:
    aos_s: float
    los_s: float
    max_elev_deg: float
    t_max_s: float
    aos_az_deg: float
    los_az_deg: float
    clipped_start: bool = False
    clipped_end: bool = False

    @property
    def duration_s(self): return self.los_s - self.aos_s


def _cross_time(t0, t1, e0, e1, thr):
    return t0 + (thr - e0) / (e1 - e0) * (t1 - t0) if e1 != e0 else t0


def _refine_peak(t, r_ecef, rng, k, target):
    """Refine the time and value of peak elevation between samples (defect D4).

    Fits a parabola to slant-range^2 through samples k-1, k, k+1 (exact for straight-line relative motion,
    and the minimum range is very close to the maximum elevation for a pass), interpolates the ECEF position
    quadratically at the vertex and evaluates the elevation there. Falls back to the sample at the window edge."""
    n = len(t)
    if k == 0 or k == n - 1:
        return float(t[k]), float(np.degrees(look_angles(r_ecef[k], target.lat_deg * DEG, target.lon_deg * DEG, target.alt_km)[1]))
    k = max(1, min(n - 2, k))
    j = int(np.argmin(rng[k - 1:k + 2])) + k - 1          # centre the stencil on the minimum-range sample
    j = max(1, min(n - 2, j))
    tt = np.asarray(t[j - 1:j + 2], float); f = np.asarray(rng[j - 1:j + 2], float) ** 2
    c2, c1, _ = np.polyfit(tt - tt[1], f, 2)
    if c2 <= 0:
        tv = tt[1]
    else:
        tv = tt[1] + float(np.clip(-c1 / (2 * c2), tt[0] - tt[1], tt[2] - tt[1]))
    L = [np.prod([(tv - tt[m]) / (tt[q] - tt[m]) for m in range(3) if m != q]) for q in range(3)]
    rv = sum(L[q] * np.asarray(r_ecef[j - 1 + q], float) for q in range(3))
    el = look_angles(rv, target.lat_deg * DEG, target.lon_deg * DEG, target.alt_km)[1]
    return float(tv), float(np.degrees(el))


def compute_passes(t, r_ecef, target: GroundTarget) -> list[Pass]:
    """Contiguous intervals with elevation >= min_elev. AOS/LOS refined by linear interpolation of
    the elevation series (error << sampling step for dt <= ~30 s); window-edge passes are flagged as clipped."""
    az, el, rng = look_angles(r_ecef, target.lat_deg * DEG, target.lon_deg * DEG, target.alt_km)
    eld = np.degrees(el); azd = np.degrees(az); thr = target.min_elev_deg
    up = eld >= thr
    passes = []
    i = 0; n = len(t)
    while i < n:
        if not up[i]: i += 1; continue
        j = i
        while j + 1 < n and up[j + 1]: j += 1
        cs, ce = i == 0, j == n - 1
        aos = t[i] if cs else _cross_time(t[i - 1], t[i], eld[i - 1], eld[i], thr)
        los = t[j] if ce else _cross_time(t[j], t[j + 1], eld[j], eld[j + 1], thr)
        k = i + int(np.argmax(eld[i:j + 1]))
        tmax, emax = _refine_peak(t, r_ecef, rng, k, target)
        passes.append(Pass(float(aos), float(los), float(max(emax, eld[k])), float(tmax), float(azd[i]), float(azd[j]), cs, ce))
        i = j + 1
    return passes


def revisit_stats(passes: list[Pass], duration_s: float) -> dict:
    """Gaps between consecutive passes (LOS -> next AOS) and window-based figures."""
    if not passes:
        return {"n_passes": 0, "mean_gap_s": None, "max_gap_s": None, "min_gap_s": None, "total_access_s": 0.0,
                "access_fraction": 0.0, "first_access_s": None}
    gaps = [passes[k + 1].aos_s - passes[k].los_s for k in range(len(passes) - 1)]
    total = sum(p.duration_s for p in passes)
    return {"n_passes": len(passes), "mean_gap_s": float(np.mean(gaps)) if gaps else None,
            "max_gap_s": float(np.max(gaps)) if gaps else None, "min_gap_s": float(np.min(gaps)) if gaps else None,
            "total_access_s": float(total), "access_fraction": float(total / duration_s), "first_access_s": passes[0].aos_s,
            "mean_duration_s": float(total / len(passes)), "max_elev_deg": float(max(p.max_elev_deg for p in passes))}


# ----------------------------------------------------------------------------- footprint
def horizon_central_angle(alt_km, min_elev_deg=0.0):
    """Earth central angle [rad] to the edge of visibility at elevation >= min_elev (spherical Earth)."""
    el = min_elev_deg * DEG
    return np.arccos(RE * np.cos(el) / (RE + np.asarray(alt_km, float))) - el


def sensor_central_angle(alt_km, half_fov_deg):
    """Earth central angle [rad] seen by a nadir-pointing sensor of half-angle `half_fov_deg`
    (clamped at the horizon when the sensor sees past the limb). Spherical Earth."""
    alt = np.asarray(alt_km, float); eta = half_fov_deg * DEG
    s = (RE + alt) / RE * np.sin(eta)
    lam = np.arcsin(np.clip(s, -1, 1)) - eta
    return np.where(s >= 1.0, horizon_central_angle(alt, 0.0), lam)


def swath_width_km(alt_km, half_fov_deg):
    return 2.0 * RE * sensor_central_angle(alt_km, half_fov_deg)


@dataclass
class CoverageResult:
    lat: np.ndarray               # grid latitudes (flat), deg
    lon: np.ndarray
    weights: np.ndarray           # cos(lat) area weights
    covered_ever: np.ndarray      # bool per grid point
    n_events: np.ndarray          # distinct coverage intervals per point
    max_gap_s: np.ndarray         # longest gap (s) per point (nan if never covered)
    first_cover_s: np.ndarray
    t: np.ndarray
    cum_fraction: np.ndarray      # area-weighted covered fraction vs time

    @property
    def grid_axes(self):
        """(unique lats, unique lons) so flat arrays can be reshaped to (len(lats), len(lons))."""
        return np.unique(self.lat), np.unique(self.lon)

    @property
    def coverage_fraction(self): return float(np.sum(self.weights * self.covered_ever) / np.sum(self.weights))
    @property
    def mean_revisit_s(self):
        g = self.max_gap_s[~np.isnan(self.max_gap_s)]
        return float(np.mean(g)) if g.size else None


def region_coverage(t, sub_lat_deg, sub_lon_deg, alt_km, region, half_fov_deg=None, min_elev_deg=None,
                    grid_deg=2.0, max_cells=4_000_000, max_work=2_000_000_000) -> CoverageResult:
    """Simplified coverage: a grid point is covered when its central angle to the sub-satellite point
    is <= the sensor (or elevation-limited) footprint angle. Spherical-Earth geometry; nadir pointing.

    region = (lat_min, lat_max, lon_min, lon_max) deg. Every output time sample is used: the (time x cell)
    boolean matrix is built in chunks of grid cells so that no chunk exceeds `max_cells` elements.
    (Earlier versions silently dropped time samples above that budget, which under-reported coverage; D2.)
    Raises ValueError if time x cells exceeds `max_work`, instead of returning a degraded answer."""
    la0, la1, lo0, lo1 = region
    if not (la0 < la1 and lo0 < lo1): raise ValueError("Region must satisfy lat_min<lat_max and lon_min<lon_max.")
    if half_fov_deg is None and min_elev_deg is None: raise ValueError("Provide half_fov_deg or min_elev_deg.")
    lats = np.arange(la0 + grid_deg / 2, la1, grid_deg); lons = np.arange(lo0 + grid_deg / 2, lo1, grid_deg)
    if lats.size == 0 or lons.size == 0: raise ValueError("Grid resolution too coarse for the region.")
    LA, LO = np.meshgrid(lats, lons, indexing="ij"); LA, LO = LA.ravel(), LO.ravel()
    G = LA.size
    ts = np.asarray(t, float); T = ts.size
    if T * G > max_work:
        raise ValueError(f"Coverage needs {T} time samples x {G} grid cells = {T*G:.2e} evaluations "
                         f"(limit {max_work:.1e}). Use a coarser grid, a smaller region, a larger step or a shorter duration.")
    slat = np.radians(np.asarray(sub_lat_deg, float)); slon = np.radians(np.asarray(sub_lon_deg, float))
    alt = np.asarray(alt_km, float)
    lam = sensor_central_angle(alt, half_fov_deg) if half_fov_deg is not None else horizon_central_angle(alt, min_elev_deg)
    coslam = np.cos(lam)[:, None]
    gla, glo = np.radians(LA), np.radians(LO)
    g = np.stack((np.cos(gla) * np.cos(glo), np.cos(gla) * np.sin(glo), np.sin(gla)), axis=1)
    s = np.stack((np.cos(slat) * np.cos(slon), np.cos(slat) * np.sin(slon), np.sin(slat)), axis=1)
    w = np.cos(gla)
    dt = float(np.median(np.diff(ts))) if T > 1 else 0.0
    covered = np.zeros(G, bool); rising = np.zeros(G, int)
    first = np.full(G, np.nan); maxgap = np.full(G, np.nan)
    chunk = max(1, int(max_cells // max(T, 1)))
    for c0 in range(0, G, chunk):
        sl = slice(c0, min(G, c0 + chunk))
        cov = (s @ g[sl].T) >= coslam                                # (T, C)
        anyc = cov.any(axis=0)
        covered[sl] = anyc
        rising[sl] = (np.diff(cov.astype(np.int8), axis=0) == 1).sum(axis=0) + cov[0].astype(int)
        f = np.where(anyc, ts[np.argmax(cov, axis=0)], np.nan)
        first[sl] = f
        if T > 1:
            last = np.maximum.accumulate(np.where(cov, ts[:, None], -np.inf), axis=0)   # last covered time so far
            prev = last[:-1]
            gap = np.where(cov[1:] & np.isfinite(prev), ts[1:, None] - np.where(np.isfinite(prev), prev, 0.0) - dt, -np.inf)
            gmax = gap.max(axis=0)
            mg = np.where(np.isfinite(gmax), np.maximum(gmax, 0.0), 0.0)
        else:
            mg = np.zeros(anyc.size)
        maxgap[sl] = np.where(anyc, mg, np.nan)
    order = np.argsort(np.where(covered, first, np.inf))
    cw = np.cumsum(w[order]); fs = np.where(covered, first, np.inf)[order]
    idx = np.searchsorted(fs, ts, side="right")                      # cells first covered at or before each t
    cum_frac = np.where(idx > 0, cw[np.maximum(idx - 1, 0)], 0.0) / w.sum()
    return CoverageResult(LA, LO, w, covered, rising, maxgap, first, ts, cum_frac)


def footprint_circle(lat_deg, lon_deg, central_angle_rad, n: int = 120):
    """Small-circle outline (lat, lon in deg) of angular radius `central_angle_rad` about a point on a sphere."""
    la, lo = np.radians(lat_deg), np.radians(lon_deg); lam = float(central_angle_rad)
    brg = np.linspace(0, 2 * np.pi, n)
    lat2 = np.arcsin(np.sin(la) * np.cos(lam) + np.cos(la) * np.sin(lam) * np.cos(brg))
    lon2 = lo + np.arctan2(np.sin(brg) * np.sin(lam) * np.cos(la), np.cos(lam) - np.sin(la) * np.sin(lat2))
    return np.degrees(lat2), (np.degrees(lon2) + 180.0) % 360.0 - 180.0
