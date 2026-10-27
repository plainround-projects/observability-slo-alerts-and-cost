"""Named errors that cross layer boundaries."""


class DomainError(Exception):
    """Base class for every error this project raises on purpose."""


class InvalidSloError(DomainError):
    """An SLO definition is out of range or incomplete."""


class InvalidPolicyError(DomainError):
    """A burn-rate policy is inconsistent (a short window longer than its long window, a non-positive rate)."""
