# Technical reference

## Equations
* Kepler: M = E - e sin E, solved by Newton iteration to 1e-13 rad. nu = 2 atan2(sqrt(1+e) sin(E/2), sqrt(1-e) cos(E/2)).
* Two-body: r'' = -mu r / r^3, mu = 398600.4418 km^3/s^2.
* J2: a = a_2body + (3/2) J2 mu Re^2 / r^5 * [x(5z^2/r^2 - 1), y(5z^2/r^2 - 1), z(5z^2/r^2 - 3)], J2 = 1.08262668e-3.
* Conserved with J2: E = v^2/2 - mu/r + mu J2 Re^2 (3z^2/r^2 - 1)/(2 r^3) and h_z.
* J2 secular rates (k = 1.5 J2 (Re/p)^2 n): RAAN' = -k cos i; argp' = 0.5 k (5 cos^2 i - 1).
* Sun-synchronous inclination: cos i = -RAAN'_sun / (1.5 J2 (Re/p)^2 n), RAAN'_sun = 2 pi / tropical year.
* GMST (IAU-82): 67310.54841 + (876600 h + 8640184.812866 s) T + 0.093104 T^2 - 6.2e-6 T^3 seconds.
* Geodetic: WGS-84 (a = 6378.137 km, f = 1/298.257223563), iterative latitude solution, valid at the poles.
* Ascent (ECI, point mass + optional J2): m a = T(h) u + D + m g, T(h) = T_vac - (T_vac - T_sl) p/p0, D = -1/2 rho Cd(M) A |v_rel| v_rel with
  v_rel = v - omega x r. Stage 1: vertical rise, kick (thrust offset from v_rel), then gravity turn. Upper stages: elevation ramp from the
  separation flight-path angle to `upper_pitch_end` over `upper_ramp_fraction` of nominal burn. Last stage cut off when osculating a reaches target.
* Footprint: central angle lambda = asin((Re+h) sin(eta)/Re) - eta (clamped at the horizon, arccos(Re/(Re+h))). Visibility at elevation el: lambda = acos(Re cos el/(Re+h)) - el.

## Validation evidence (all in `tests/`, run automatically)
| Check | Result |
|---|---|
| DOP853 vs analytical Kepler, 3 orbits | < 1e-4 km |
| RK4 observed convergence order | 4.1 (expected 4) |
| Energy / angular-momentum drift, 5 orbits | ~1e-10 relative, both models |
| J2 acceleration vs numerical gradient of the J2 potential | agree to 1e-6 relative |
| J2 nodal regression (3 days) vs analytical rate | within 1% |
| GMST vs Vallado Example 3-5 (1992-08-20 12:14 UT1) | 152.578787810 deg to 1e-4 deg |
| Geodetic round trip incl. poles | < 1e-9 rad, < 1e-6 km |
| US-76 atmosphere vs standard-table values | density within 0.2% at 0/5/20 km |
| Geostationary satellite sub-point / pass | stays within 0.05 deg lon, elevation 90 deg |
| Ascent -> orbit: tuned Cape Canaveral launch to 400 km / 51.6 deg | circular within 10 km, inclination within 0.3 deg |
| Hand-off | propagation initial state equals burnout state exactly |

## Changes after the third-eye review
* Coverage (`groundtrack.region_coverage`): every output time sample is evaluated; the (time × cell) matrix is built in
  chunks of cells. Max revisit gap per cell = max over consecutive covered samples of (Δt − step). Cumulative coverage
  from each cell's first-cover time. Bit-identical to the un-chunked computation (tested).
* Launch azimuth (`launch.inertial_azimuths`): β = asin(cos i / cos φ) and 180° − β both give inclination i.
  `choose_branch('auto')` picks the branch nearest an approximate site corridor (`RANGE_CORRIDORS`, planning values only).
* Peak elevation (`groundtrack._refine_peak`): quadratic fit to slant-range² through the three samples around the
  closest approach; position interpolated (Lagrange, quadratic) at the vertex; elevation evaluated there.
* Browser bridge (`oi_bridge.api`): JSON in, JSON out; NaN/Inf mapped to null; errors returned as `{ok: false, error}`.
