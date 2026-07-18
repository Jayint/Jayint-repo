"""④ RUN + the outer run<->repair driver (src/ stage-refactor §4C).

The run_v3 driver + run/gate/trace/world-model. Drives the graph against a live
container, gates on pass-rate, and loops back to construct/compile/repair. Imports
graph + agent; graph never imports it (the direction tripwire).
"""
