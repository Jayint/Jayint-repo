"""① SELECT — language detection + base image + interpreter pin (src/ stage-refactor §4C).

An import-island run BEFORE the graph exists: language -> candidate images -> image
(LLM picks) -> runtime (pin) -> choose returns the pinned base image.
"""
