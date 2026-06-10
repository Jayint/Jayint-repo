class PlanningError(Exception):
    """Base class for planning module errors."""


class PlanningValidationError(PlanningError):
    """Raised when a typed task graph is structurally invalid."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))
