"""Physical constants. Engine units: km, s, rad, kg (thrust in kN, so kN/kg == km/s^2)."""
import math

MU = 398600.4418            # km^3/s^2  (EGM-96/WGS-84 GM)
RE = 6378.137               # km  (WGS-84 equatorial radius)
J2 = 1.08262668e-3
OMEGA_E = 7.2921158553e-5   # rad/s  (Earth rotation rate)
F_WGS84 = 1.0 / 298.257223563
A_WGS84 = RE
B_WGS84 = A_WGS84 * (1.0 - F_WGS84)
E2_WGS84 = F_WGS84 * (2.0 - F_WGS84)
G0 = 9.80665                # m/s^2
JD_J2000 = 2451545.0
SIDEREAL_DAY_S = 86164.0905
TWO_PI = 2.0 * math.pi
DEG = math.pi / 180.0
