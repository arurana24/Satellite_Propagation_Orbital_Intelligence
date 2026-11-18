"""Atmosphere model: US Standard Atmosphere 1976 (0-86 km geometric) blended into the
Vallado exponential table up to 1000+ km (scaled for continuity at the join).
Returns SI units. The upper table is a mean-solar-activity approximation, so orbital
lifetimes derived from it are order-of-magnitude estimates only."""
from __future__ import annotations
import math
import numpy as np

_RG = 6356.766                     # km, geopotential radius
_K = 34.163195                      # g0*M/R [K/km]
_H = [0.0, 11.0, 20.0, 32.0, 47.0, 51.0, 71.0, 84.852]
_T = [288.15, 216.65, 216.65, 228.65, 270.65, 270.65, 214.65, 186.946]
_L = [-6.5, 0.0, 1.0, 2.8, 0.0, -2.8, -2.0, 0.0]
_P = [101325.0]
for _i in range(1, len(_H)):
    _dh = _H[_i] - _H[_i - 1]
    if _L[_i - 1] == 0.0:
        _P.append(_P[-1] * math.exp(-_K * _dh / _T[_i - 1]))
    else:
        _P.append(_P[-1] * (_T[_i - 1] / (_T[_i - 1] + _L[_i - 1] * _dh)) ** (_K / _L[_i - 1]))

# Vallado exponential atmosphere: (base alt km, rho0 kg/m^3, scale height km)
_TAB = [(0, 1.225, 7.249), (25, 3.899e-2, 6.349), (30, 1.774e-2, 6.682), (40, 3.972e-3, 7.554),
        (50, 1.057e-3, 8.382), (60, 3.206e-4, 7.714), (70, 8.770e-5, 6.549), (80, 1.905e-5, 5.799),
        (90, 3.396e-6, 5.382), (100, 5.297e-7, 5.877), (110, 9.661e-8, 7.263), (120, 2.438e-8, 9.473),
        (130, 8.484e-9, 12.636), (140, 3.845e-9, 16.149), (150, 2.070e-9, 22.523), (180, 5.464e-10, 29.740),
        (200, 2.789e-10, 37.105), (250, 7.248e-11, 45.546), (300, 2.418e-11, 53.628), (350, 9.158e-12, 53.298),
        (400, 3.725e-12, 58.515), (450, 1.585e-12, 60.828), (500, 6.967e-13, 63.822), (600, 1.454e-13, 71.835),
        (700, 3.614e-14, 88.667), (800, 1.170e-14, 124.64), (900, 5.245e-15, 181.05), (1000, 3.019e-15, 268.0)]


def _tab_rho(h):
    row = _TAB[0]
    for r in _TAB:
        if h >= r[0]: row = r
        else: break
    return row[1] * math.exp(-(h - row[0]) / row[2])


def _us76(h_km):
    Hg = _RG * h_km / (_RG + h_km)
    i = 0
    while i < len(_H) - 1 and Hg >= _H[i + 1]: i += 1
    dh = Hg - _H[i]; L = _L[i]
    T = _T[i] + L * dh
    P = _P[i] * math.exp(-_K * dh / _T[i]) if L == 0.0 else _P[i] * (_T[i] / T) ** (_K / L)
    return P / (287.053 * T), P, T


_RHO86 = _us76(86.0)[0]
_SCALE = _RHO86 / _tab_rho(86.0)


def atmosphere(h_km: float):
    """(density kg/m^3, pressure Pa, temperature K, speed of sound m/s) at geometric altitude [km]."""
    h = max(float(h_km), 0.0)
    if h <= 86.0:
        rho, P, T = _us76(h)
    else:
        rho = _tab_rho(h) * _SCALE; T = 186.95; P = rho * 287.053 * T
    return rho, P, T, math.sqrt(1.4 * 287.053 * T)


def density(h_km):
    f = np.vectorize(lambda x: atmosphere(x)[0])
    out = f(h_km)
    return float(out) if np.ndim(out) == 0 else out
