"""① SELECT + ④ RUN + the outer e2e driver (src/ stage-refactor §4C).

The driver that sequences the five pipeline stages and owns the outer run<->repair
loop. Imports ``graph``/``agent`` and calls them in order; neither imports it.
"""
