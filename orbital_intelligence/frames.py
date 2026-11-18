"""Time scales, ECI<->ECEF (GMST rotation), and WGS-84 geodetic conversions.

ECI here is the mean-of-date-like inertial frame obtained by a pure GMST rotation about z
(no precession/nutation/polar motion): adequate for mission-design-level analysis (< ~0.1 deg)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import numpy as np

from .constants import OMEGA_E, A_WGS84, E2_WGS84, JD_J2000, TWO_PI, DEG


def julian_date(dt: datetime) -> float:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    y, m = dt.year, dt.month
    if m <= 2:
        y -= 1; m += 12
    A = y // 100; B = 2 - A + A // 4
    day = dt.day + (dt.hour + (dt.minute + (dt.second + dt.microsecond * 1e-6) / 60) / 60) / 24
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day + B - 1524.5


def datetime_from_jd(jd: float) -> datetime:
    return datetime(2000, 1, 1, 12) + timedelta(days=jd - JD_J2000)


def gmst(jd):
    """Greenwich Mean Sidereal Time [rad] (IAU-82), UT1 ~ UTC. Vectorised."""
    T = (np.asarray(jd, float) - JD_J2000) / 36525.0
    sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * T + 0.093104 * T**2 - 6.2e-6 * T**3
    return np.mod(sec, 86400.0) * (TWO_PI / 86400.0)


def gmst_at(epoch: datetime, t_s):
    return gmst(julian_date(epoch) + np.asarray(t_s, float) / 86400.0)


def _rz(theta, x):
    c, s = np.cos(theta), np.sin(theta)
    return np.stack((c * x[..., 0] + s * x[..., 1], -s * x[..., 0] + c * x[..., 1], x[..., 2]), axis=-1)


def eci_to_ecef(r_eci, theta):
    """Position ECI->ECEF. r (...,3), theta = GMST (broadcastable)."""
    r = np.asarray(r_eci, float)
    return _rz(np.asarray(theta, float), r)


def ecef_to_eci(r_ecef, theta):
    return _rz(-np.asarray(theta, float), np.asarray(r_ecef, float))


def state_eci_to_ecef(r_eci, v_eci, theta):
    """Position and Earth-relative velocity in ECEF: v_ecef = R v - w x r_ecef."""
    re = eci_to_ecef(r_eci, theta)
    ve = _rz(np.asarray(theta, float), np.asarray(v_eci, float))
    w = np.array([0.0, 0.0, OMEGA_E])
    return re, ve - np.cross(w, re)


def geodetic_to_ecef(lat, lon, h=0.0):
    """WGS-84 geodetic (rad, rad, km) -> ECEF [km]."""
    lat, lon, h = np.broadcast_arrays(np.asarray(lat, float), np.asarray(lon, float), np.asarray(h, float))
    N = A_WGS84 / np.sqrt(1 - E2_WGS84 * np.sin(lat) ** 2)
    return np.stack(((N + h) * np.cos(lat) * np.cos(lon), (N + h) * np.cos(lat) * np.sin(lon),
                     (N * (1 - E2_WGS84) + h) * np.sin(lat)), axis=-1)


def ecef_to_geodetic(r_ecef, iters: int = 8):
    """ECEF [km] -> (lat rad, lon rad, h km), iterative; valid at poles. Vectorised over (...,3)."""
    r = np.asarray(r_ecef, float)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    lon = np.arctan2(y, x); p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - E2_WGS84))
    for _ in range(iters):
        s = np.sin(lat)
        N = A_WGS84 / np.sqrt(1 - E2_WGS84 * s**2)
        with np.errstate(divide="ignore", invalid="ignore"):
            h = np.where(np.abs(np.cos(lat)) > 1e-6, p / np.cos(lat) - N, np.abs(z) / np.abs(s) - N * (1 - E2_WGS84))
        lat = np.arctan2(z, p * (1 - E2_WGS84 * N / (N + h)))
    return lat, lon, h


def look_angles(r_sat_ecef, lat, lon, h=0.0):
    """Azimuth (from north, cw), elevation [rad] and slant range [km] from a site to satellites."""
    site = geodetic_to_ecef(lat, lon, h)
    rho = np.asarray(r_sat_ecef, float) - site
    sl, cl, so, co = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    east = np.array([-so, co, 0.0]); north = np.array([-sl * co, -sl * so, cl]); up = np.array([cl * co, cl * so, sl])
    e, n, u = rho @ east, rho @ north, rho @ up
    rng = np.linalg.norm(rho, axis=-1)
    return np.mod(np.arctan2(e, n), TWO_PI), np.arcsin(np.clip(u / rng, -1, 1)), rng
