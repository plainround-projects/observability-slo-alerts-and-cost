"""The checkout request the service accepts and what it can end in."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from slo_lab.domain.errors import DomainError

_SKU = re.compile(r"^[A-Za-z0-9-]{1,32}$")
MAX_LINES = 20
MAX_QUANTITY = 100


class InvalidCheckoutError(DomainError):
    """The request body is not a valid checkout."""


class CheckoutFailedError(DomainError):
    """The checkout failed on the server side (the simulated 5xx)."""


class Simulation(StrEnum):
    """Lab-only switch that makes a request fail or run slowly, so the scenario can plant known incidents."""

    NONE = "none"
    ERROR = "error"
    SLOW = "slow"


@dataclass(frozen=True, slots=True)
class OrderLine:
    """One line of the basket."""

    sku: str
    quantity: int

    def __post_init__(self) -> None:
        """Validate the SKU shape and the quantity range."""
        if not _SKU.fullmatch(self.sku):
            raise InvalidCheckoutError("sku must be 1 to 32 letters, digits or dashes")
        if not 1 <= self.quantity <= MAX_QUANTITY:
            raise InvalidCheckoutError(f"quantity must be between 1 and {MAX_QUANTITY}")


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    """A basket with optional contact and payment details and an optional lab simulation."""

    lines: tuple[OrderLine, ...]
    customer_email: str | None = None
    card_number: str | None = None
    simulation: Simulation = Simulation.NONE

    def __post_init__(self) -> None:
        """A checkout needs between 1 and ``MAX_LINES`` lines."""
        if not 1 <= len(self.lines) <= MAX_LINES:
            raise InvalidCheckoutError(f"a checkout needs between 1 and {MAX_LINES} lines")


@dataclass(frozen=True, slots=True)
class Receipt:
    """What a successful checkout returns."""

    reference: str
    item_count: int
