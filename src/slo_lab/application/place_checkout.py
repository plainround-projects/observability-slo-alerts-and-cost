"""The service's use case: accept a basket and answer, failing or slowing down when the lab asks it to.

This use case is deliberately naive about personal data. It puts the customer's email on the span and writes the card
number into a log line, which is the leak the Collector's redaction processor exists to stop.
"""

from __future__ import annotations

import logging

from slo_lab.application.ports import Annotator, Delay, Reference
from slo_lab.domain.checkout import CheckoutFailedError, CheckoutRequest, Receipt, Simulation

_log = logging.getLogger(__name__)


class PlaceCheckout:
    """Handles one checkout."""

    def __init__(self, annotator: Annotator, delay: Delay, references: Reference, slow_seconds: float) -> None:
        """Wire the use case to its collaborators."""
        self._annotator = annotator
        self._delay = delay
        self._references = references
        self._slow_seconds = slow_seconds

    def execute(self, request: CheckoutRequest) -> Receipt:
        """Return a receipt, or raise ``CheckoutFailedError`` for a simulated server error."""
        if request.customer_email:
            self._annotator.set("customer.email", request.customer_email)
        if request.card_number:
            self._annotator.set("payment.card", request.card_number)
            _log.info(f"payment received from {request.customer_email or 'guest'} with card {request.card_number}")
        if request.simulation is Simulation.SLOW:
            self._delay.wait(self._slow_seconds)
        if request.simulation is Simulation.ERROR:
            _log.error("checkout failed", extra={"lines": len(request.lines)})
            raise CheckoutFailedError("the payment provider did not answer")
        receipt = Receipt(self._references.next(), sum(line.quantity for line in request.lines))
        _log.info("checkout completed", extra={"reference": receipt.reference, "items": receipt.item_count})
        return receipt
