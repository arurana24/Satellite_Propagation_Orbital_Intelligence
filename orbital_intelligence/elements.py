"""Classical orbital elements, Kepler's equation, and state-vector conversions (km, s, rad)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import numpy as np

from .constants import MU, RE, DEG, TWO_PI


class OrbitError(ValueError):
    """Raised for physically invalid or unsupported orbit definitions."""


def kepler_solve(M, e, tol: float = 1e-13, maxiter: int = 60):
    """Solve M = E - e sin E for eccentric anomaly E (elliptic, 0 <= e < 1). Vectorised."""
    e = float(e)
    if not (0.0 <= e < 1.0):
        raise OrbitError(f"Kepler solver supports 0 <= e < 1 (got e={e}).")
    M = np.asarray(M, dtype=float)
    Mw = np.mod(M + np.pi, TWO_PI) - np.pi
    E = np.where(e < 0.8, Mw + e * np.sin(Mw), np.where(Mw >= 0, np.pi, -np.pi))
    E = np.where(Mw == 0.0, 0.0, E)
    for _ in range(maxiter):
        dE = (E - e * np.sin(E) - Mw) / (1.0 - e * np.cos(E))
        E = E - dE
        if np.all(np.abs(dE) < tol):
            break
    else:
        raise OrbitError("Kepler solver failed to converge.")
    out = E + (M - Mw)
    return float(out) if out.ndim == 0 else out


def nu_from_E(E, e):
    return 2.0 * np.arctan2(np.sqrt(1 + e) * np.sin(E / 2), np.sqrt(1 - e) * np.cos(E / 2))


def E_from_nu(nu, e):
    return 2.0 * np.arctan2(np.sqrt(1 - e) * np.sin(nu / 2), np.sqrt(1 + e) * np.cos(nu / 2))


def M_from_nu(nu, e):
    E = E_from_nu(nu, e)
    return E - e * np.sin(E)


def nu_from_M(M, e):
    return nu_from_E(kepler_solve(M, e), e)


def mean_motion(a: float, mu: float = MU) -> float:
    return float(np.sqrt(mu / a**3))


def period(a: float, mu: float = MU) -> float:
    return float(TWO_PI * np.sqrt(a**3 / mu))


def circular_velocity(r: float, mu: float = MU) -> float:
    return float(np.sqrt(mu / r))


def vis_viva(r: float, a: float, mu: float = MU) -> float:
    return float(np.sqrt(mu * (2.0 / r - 1.0 / a)))


def specific_energy(a: float, mu: float = MU) -> float:
    return -mu / (2.0 * a)


def hohmann_delta_v(r1: float, r2: float, mu: float = MU) -> dict:
    """Two-impulse Hohmann transfer between coplanar circular orbits (km, km/s)."""
    at = 0.5 * (r1 + r2)
    dv1 = vis_viva(r1, at, mu) - circular_velocity(r1, mu)
    dv2 = circular_velocity(r2, mu) - vis_viva(r2, at, mu)
    return {"dv1": dv1, "dv2": dv2, "total": abs(dv1) + abs(dv2), "tof_s": float(np.pi * np.sqrt(at**3 / mu))}


@dataclass(frozen=True)
class ClassicalElements:
    """a [km], e [-], i/raan/argp/nu [rad]."""
    a: float
    e: float
    i: float
    raan: float
    argp: float
    nu: float

    def __post_init__(self):
        if not (0.0 <= self.e < 1.0):
            raise OrbitError(f"Eccentricity must satisfy 0 <= e < 1 (got {self.e}).")
        if self.a <= 0:
            raise OrbitError("Semi-major axis must be positive.")
        if not (0.0 <= self.i <= np.pi + 1e-12):
            raise OrbitError("Inclination must be within 0..180 deg.")

    @classmethod
    def from_degrees(cls, a, e, i, raan, argp, nu):
        return cls(a, e, i * DEG, raan * DEG, argp * DEG, nu * DEG)

    @classmethod
    def from_altitudes(cls, perigee_alt, apogee_alt, i_deg, raan_deg=0.0, argp_deg=0.0, nu_deg=0.0):
        if apogee_alt < perigee_alt:
            raise OrbitError("Apogee altitude must be >= perigee altitude.")
        rp, ra = RE + perigee_alt, RE + apogee_alt
        if rp <= 0:
            raise OrbitError("Perigee radius must be positive.")
        return cls.from_degrees(0.5 * (rp + ra), (ra - rp) / (ra + rp), i_deg, raan_deg, argp_deg, nu_deg)

    @property
    def p(self): return self.a * (1 - self.e**2)
    @property
    def rp(self): return self.a * (1 - self.e)
    @property
    def ra(self): return self.a * (1 + self.e)
    @property
    def perigee_alt(self): return self.rp - RE
    @property
    def apogee_alt(self): return self.ra - RE
    @property
    def period(self): return period(self.a)
    @property
    def n(self): return mean_motion(self.a)
    @property
    def energy(self): return specific_energy(self.a)
    @property
    def mean_anomaly(self): return float(M_from_nu(self.nu, self.e))

    def with_nu(self, nu): return ClassicalElements(self.a, self.e, self.i, self.raan, self.argp, nu)

    def as_degrees(self) -> dict:
        d = asdict(self)
        d["i"] = float(np.degrees(self.i))
        for k in ("raan", "argp", "nu"):
            d[k] = float(np.degrees(d[k]) % 360.0)
        return d

    def to_state(self, mu: float = MU):
        return coe_to_rv(self, mu)


def _rot3(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def _rot1(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1.0, 0, 0], [0, c, -s], [0, s, c]])


def perifocal_to_eci(raan, i, argp):
    return _rot3(raan) @ _rot1(i) @ _rot3(argp)


def coe_to_rv(el: ClassicalElements, mu: float = MU):
    """Elements -> (r [km], v [km/s]) in the inertial (ECI) frame."""
    p = el.p
    cn, sn = np.cos(el.nu), np.sin(el.nu)
    r_pf = (p / (1 + el.e * cn)) * np.array([cn, sn, 0.0])
    v_pf = np.sqrt(mu / p) * np.array([-sn, el.e + cn, 0.0])
    Q = perifocal_to_eci(el.raan, el.i, el.argp)
    return Q @ r_pf, Q @ v_pf


def _angle(u, w, sign_ref):
    c = np.clip(np.dot(u, w) / (np.linalg.norm(u) * np.linalg.norm(w)), -1, 1)
    x = float(np.arccos(c))
    return TWO_PI - x if sign_ref < 0 else x


def rv_to_coe(r, v, mu: float = MU, tol: float = 1e-10) -> ClassicalElements:
    """(r, v) -> elements. Circular/equatorial singularities: argp and/or raan set to 0
    and nu becomes argument of latitude / true longitude."""
    r = np.asarray(r, float); v = np.asarray(v, float)
    rn, vn = np.linalg.norm(r), np.linalg.norm(v)
    h = np.cross(r, v); hn = np.linalg.norm(h)
    if hn < 1e-12:
        raise OrbitError("Rectilinear trajectory: angular momentum is zero.")
    eps = 0.5 * vn**2 - mu / rn
    if eps >= 0:
        raise OrbitError("State is not on a bound (elliptic) orbit.")
    a = -mu / (2 * eps)
    evec = ((vn**2 - mu / rn) * r - np.dot(r, v) * v) / mu
    e = float(np.linalg.norm(evec))
    i = float(np.arccos(np.clip(h[2] / hn, -1, 1)))
    nvec = np.cross([0, 0, 1.0], h); nn = np.linalg.norm(nvec)
    circ, equ = e < tol, nn < tol * hn
    xhat = np.array([1.0, 0, 0])
    raan = 0.0 if equ else _angle(xhat, nvec, nvec[1])
    if circ and equ:
        argp, nu = 0.0, _angle(xhat, r, r[1])
    elif circ:
        argp, nu = 0.0, _angle(nvec, r, r[2])
    elif equ:
        argp = _angle(xhat, evec, evec[1]) if h[2] > 0 else (TWO_PI - _angle(xhat, evec, evec[1]))
        nu = _angle(evec, r, np.dot(r, v))
    else:
        argp = _angle(nvec, evec, evec[2]); nu = _angle(evec, r, np.dot(r, v))
    return ClassicalElements(float(a), min(e, 1 - 1e-12), i, float(raan), float(argp % TWO_PI), float(nu % TWO_PI))


def kepler_propagate(r0, v0, dt, mu: float = MU):
    """Analytical two-body propagation (reference solution). dt scalar/array -> states (N,6)."""
    el = rv_to_coe(r0, v0, mu)
    dt = np.atleast_1d(np.asarray(dt, float))
    M = M_from_nu(el.nu, el.e) + mean_motion(el.a, mu) * dt
    nus = np.atleast_1d(nu_from_M(M, el.e))
    out = np.empty((dt.size, 6))
    for k, nu in enumerate(nus):
        out[k, :3], out[k, 3:] = coe_to_rv(el.with_nu(float(nu)), mu)
    return out


# ----------------------------------------------------------------------------- J2 secular theory
def j2_secular_rates(a: float, e: float, i: float) -> dict:
    """First-order J2 secular rates [rad/s] of RAAN, argument of perigee and mean anomaly offset."""
    from .constants import J2, RE
    n = mean_motion(a); p = a * (1 - e**2); k = 1.5 * J2 * (RE / p) ** 2 * n
    return {"raan_dot": -k * np.cos(i), "argp_dot": 0.5 * k * (5 * np.cos(i) ** 2 - 1),
            "M_dot_extra": 0.5 * k * np.sqrt(1 - e**2) * (3 * np.cos(i) ** 2 - 1)}


SUN_RATE = TWO_PI / (365.2421897 * 86400.0)          # rad/s, mean motion of the Earth about the Sun


def sun_sync_inclination(a: float, e: float = 0.0):
    """Inclination [rad] giving a nodal precession of +360 deg/tropical year, or None if no such orbit exists."""
    from .constants import J2, RE
    n = mean_motion(a); p = a * (1 - e**2)
    c = -SUN_RATE / (1.5 * J2 * (RE / p) ** 2 * n)
    return float(np.arccos(c)) if abs(c) <= 1.0 else None
