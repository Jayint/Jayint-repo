"""ExecuteAgent-only DepGraph ablation.

Everything needed by the ablation lives in this package.  The package may reuse
generic host facilities from the parent project, but it deliberately does not
import or construct DepGraph objects.
"""

from .models import FlatBlock, FlatPatch, FlatPlan

__all__ = ["FlatBlock", "FlatPatch", "FlatPlan"]
