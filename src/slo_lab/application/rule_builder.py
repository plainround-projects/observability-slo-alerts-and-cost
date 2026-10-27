"""Generate burn-rate alert rules from SLO definitions and a policy, so thresholds are derived and never typed."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from slo_lab.domain.alerts import AlertRule
from slo_lab.domain.burn_rate import BurnRatePolicy, BurnRateRule, prometheus_duration
from slo_lab.domain.slo import Slo

_KIND = {"page": "FastBurn", "ticket": "SlowBurn"}


def alert_name(slo: Slo, rule: BurnRateRule) -> str:
    """For example ``AvailabilityBudgetFastBurn``."""
    return f"{slo.name.title()}Budget{_KIND[rule.severity]}"


def expression(slo: Slo, rule: BurnRateRule) -> str:
    """Both windows must show a bad-event ratio above the threshold."""
    threshold = f"{rule.error_ratio_threshold(slo):.6g}"
    long_ratio = slo.error_ratio_expression(prometheus_duration(rule.long_window))
    short_ratio = slo.error_ratio_expression(prometheus_duration(rule.short_window))
    return f"{long_ratio} > {threshold}\nand\n{short_ratio} > {threshold}"


def build_alert_rules(slos: Sequence[Slo], policy: BurnRatePolicy) -> list[AlertRule]:
    """One alert per (SLO, rule): page rules first, then tickets."""
    return [
        AlertRule(alert_name(slo, rule), expression(slo, rule), 0.0, rule.severity)
        for slo in slos
        for rule in policy.rules
    ]


def rule_file_document(slos: Sequence[Slo], policy: BurnRatePolicy) -> dict[str, Any]:
    """The Prometheus rule file for the policy as plain data, ready to be written as YAML."""
    rules: list[dict[str, Any]] = []
    for slo in slos:
        for rule in policy.rules:
            consumed = rule.budget_consumed(slo)
            rules.append(
                {
                    "alert": alert_name(slo, rule),
                    "expr": expression(slo, rule),
                    "labels": {"severity": rule.severity, "slo": slo.name},
                    "annotations": {
                        "summary": (
                            f"{slo.name.capitalize()} error budget burning at {rule.burn_rate:g}x: "
                            f"{consumed:.1%} of the {slo.window_days}-day budget in "
                            f"{prometheus_duration(rule.long_window)}."
                        ),
                        "runbook": (
                            f"Check the {slo.name} SLI panel, open an error or slow trace from the last "
                            f"{prometheus_duration(rule.short_window)}, and roll back or fix the cause."
                        ),
                    },
                }
            )
    return {"groups": [{"name": f"slo-burn-rate-{policy.name}", "rules": rules}]}
