"""Numerical orbit propagation: two-body and J2 force models, RK4 and DOP853 integrators."""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from scipy.integrate import solve_ivp

from .constants import MU, RE, J2
from .elements import kepler_propagate, rv_to_coe

MODELS = ("twobody", "j2")
METHODS = ("rk4", "dop853")


def accel_twobody(r):
    return -MU * r / np.linalg.norm(r) ** 3


def accel_j2(r):
    x, y, z = r
    rn = np.linalg.norm(r)
    k = 1.5 * J2 * MU * RE**2 / rn**5
    z2 = 5.0 * z * z / rn**2
    return accel_twobody(r) + k * np.array([x * (z2 - 1.0), y * (z2 - 1.0), z * (z2 - 3.0)])


def accel(r, model: str = "j2"):
    if model == "twobody":
        return accel_twobody(r)
    if model == "j2":
        return accel_j2(r)
    raise ValueError(f"Unknown force model '{model}' (choose from {MODELS}).")


def _deriv(model):
    f = accel_twobody if model == "twobody" else accel_j2
    def g(t, y):
        return np.concatenate((y[3:], f(y[:3])))
    return g


def rk4_step(f, t, y, h):
    k1 = f(t, y)
    k2 = f(t + h / 2, y + h / 2 * k1)
    k3 = f(t + h / 2, y + h / 2 * k2)
    k4 = f(t + h, y + h * k3)
    return y + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)


def rk4_integrate(f, y0, t_samples, h_max):
    """Classic fixed-step RK4 between output samples; each interval split into equal sub-steps <= h_max."""
    t_samples = np.asarray(t_samples, float)
    out = np.empty((t_samples.size, y0.size)); out[0] = y0
    y = np.array(y0, float); nfev = 0
    for k in range(1, t_samples.size):
        T = t_samples[k] - t_samples[k - 1]
        n = max(1, int(np.ceil(T / h_max - 1e-12)))
        h = T / n; t = t_samples[k - 1]
        for _ in range(n):
            y = rk4_step(f, t, y, h); t += h; nfev += 4
        out[k] = y
    return out, nfev


@dataclass
class PropagationResult:
    t: np.ndarray                 # s since start
    y: np.ndarray                 # (N,6) km, km/s (ECI)
    model: str
    method: str
    nfev: int = 0
    settings: dict = field(default_factory=dict)

    @property
    def r(self): return self.y[:, :3]
    @property
    def v(self): return self.y[:, 3:]


def propagate(r0, v0, duration, dt_out=10.0, model="j2", method="dop853",
              rtol=1e-10, atol=1e-10, rk4_step_s=None) -> PropagationResult:
    """Propagate an ECI state for `duration` s, sampled every `dt_out` s (last sample == duration)."""
    if model not in MODELS: raise ValueError(f"model must be one of {MODELS}")
    if method not in METHODS: raise ValueError(f"method must be one of {METHODS}")
    if duration <= 0 or dt_out <= 0: raise ValueError("duration and dt_out must be positive.")
    t = np.arange(0.0, duration, dt_out)
    t = np.append(t, duration) if t[-1] < duration - 1e-9 else t
    y0 = np.concatenate((np.asarray(r0, float), np.asarray(v0, float)))
    f = _deriv(model)
    if method == "rk4":
        h = rk4_step_s or dt_out
        y, nfev = rk4_integrate(f, y0, t, h)
        settings = {"step_s": h}
    else:
        sol = solve_ivp(f, (0.0, duration), y0, method="DOP853", t_eval=t, rtol=rtol, atol=atol)
        if not sol.success:
            raise RuntimeError(f"DOP853 integration failed: {sol.message}")
        y, nfev = sol.y.T, sol.nfev
        settings = {"rtol": rtol, "atol": atol}
    return PropagationResult(t, y, model, method, nfev, settings)


# ----------------------------------------------------------------------------- invariants
def specific_energy_series(y, model="j2"):
    """Total specific energy (incl. J2 potential if model=='j2') [km^2/s^2]."""
    r = y[:, :3]; v = y[:, 3:]
    rn = np.linalg.norm(r, axis=1)
    e = 0.5 * np.sum(v**2, axis=1) - MU / rn
    if model == "j2":
        e = e + MU * J2 * RE**2 * (3 * r[:, 2] ** 2 / rn**2 - 1) / (2 * rn**3)
    return e


def angular_momentum_series(y):
    return np.cross(y[:, :3], y[:, 3:])


def invariants_drift(res: PropagationResult) -> dict:
    """Relative drift of conserved quantities (energy; |h| for two-body, h_z for J2)."""
    e = specific_energy_series(res.y, res.model)
    h = angular_momentum_series(res.y)
    hq = np.linalg.norm(h, axis=1) if res.model == "twobody" else h[:, 2]
    return {"energy_rel_drift": float(np.max(np.abs(e - e[0])) / abs(e[0])),
            "angmom_rel_drift": float(np.max(np.abs(hq - hq[0])) / abs(hq[0])),
            "invariant_used": "|h|" if res.model == "twobody" else "h_z"}


# ----------------------------------------------------------------------------- convergence
def convergence_study(r0, v0, duration, steps=(60.0, 30.0, 15.0, 7.5), model="twobody") -> dict:
    """RK4 step-size study against the analytical Kepler solution (two-body only):
    final-position error vs step and observed convergence order (expected ~4)."""
    if model != "twobody":
        raise ValueError("Analytical reference exists for the two-body model only.")
    ref = kepler_propagate(r0, v0, duration)[0]
    errs = []
    for h in steps:
        res = propagate(r0, v0, duration, dt_out=duration, model="twobody", method="rk4", rk4_step_s=h)
        errs.append(float(np.linalg.norm(res.r[-1] - ref[:3])))
    steps = np.asarray(steps, float); errs_a = np.maximum(np.asarray(errs), 1e-300)
    order = float(np.polyfit(np.log(steps), np.log(errs_a), 1)[0])
    dop = []
    for tol in (1e-6, 1e-8, 1e-10, 1e-12):
        res = propagate(r0, v0, duration, dt_out=duration, model="twobody", method="dop853", rtol=tol, atol=tol)
        dop.append({"rtol": tol, "pos_err_km": float(np.linalg.norm(res.r[-1] - ref[:3])), "nfev": int(res.nfev)})
    return {"rk4_steps_s": steps.tolist(), "rk4_pos_err_km": errs, "rk4_observed_order": order, "dop853": dop}
