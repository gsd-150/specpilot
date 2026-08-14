# W4 Task 6: canonical planning generation suffix

Planner now treats only a terminal `-g<digits>` segment as the structured
generation component and replaces it with the requested generation. Thus a
canonical L2 planning root produces exactly `...-g0`, then `...-g1`, never
`...-g0-g1`. Similar text such as `run-g7` earlier in the key remains intact.

Transport-level tests capture the actual reservation keys for both generations.
`PYTHONPATH=.:src make check` passed on 2026-08-14.
