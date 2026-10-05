from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ActionLabelInput:
    coverage_insufficient: bool
    gate_blocked: bool
    data_stale: bool
    confidence: float
    holding_state: Literal["holding", "none", "unknown"]
    thesis_invalidated: bool
    score: float
    buy_allowed: bool
    regime: str
    agreeing_sources: int
    rr: float
    valuation_percentile: float


@dataclass
class ActionLabelConfig:
    min_confidence_floor: float
    buy_min_score: float
    buy_min_confidence: float
    buy_min_agreeing_sources: int
    buy_min_rr: float
    buy_max_valuation_percentile: float
    watch_min_score: float
    reduce_exit_max_score: float

    @classmethod
    def from_rules(cls, rules: dict) -> "ActionLabelConfig":
        al = rules["action_labels"]
        return cls(
            min_confidence_floor=al["min_confidence_floor"],
            buy_min_score=al["buy_accumulate"]["min_score"],
            buy_min_confidence=al["buy_accumulate"]["min_confidence"],
            buy_min_agreeing_sources=al["buy_accumulate"]["min_agreeing_sources"],
            buy_min_rr=al["buy_accumulate"]["min_rr"],
            buy_max_valuation_percentile=al["buy_accumulate"]["max_valuation_percentile"],
            watch_min_score=al["watch"]["min_score"],
            reduce_exit_max_score=al["reduce_exit"]["max_score"],
        )


DEFENSIVE_LABELS = {"reduce_exit", "stay_out"}


def provisional_label(live: str | None, official: str | None, risk_triggered: bool) -> tuple[str | None, bool]:
    """Label to show while the session is open, and whether it may be stored as a prediction.

    The official label is the one set after the close (on closing prices). Intraday we only
    move it DOWN, and only on a real trigger (stop breached / move beyond 2 ATR): a late
    downgrade costs the investor money, a late upgrade only a part of the move, and an
    intraday spike is often sold back into the close. Upgrades wait for the close."""
    if live is None:
        return official, False
    if risk_triggered and live in DEFENSIVE_LABELS and official is not None and official not in DEFENSIVE_LABELS:
        return live, True
    if official is None:  # nothing official yet: show the live view but never a buy before the close
        return ("watch" if live == "buy_accumulate" else live), False
    return official, False


def action_label(inp: ActionLabelInput, cfg: ActionLabelConfig) -> str | None:
    if inp.coverage_insufficient:
        return None
    if inp.gate_blocked or inp.data_stale or inp.confidence < cfg.min_confidence_floor:
        return "stay_out"
    if inp.holding_state == "holding":
        if inp.thesis_invalidated or inp.score < cfg.reduce_exit_max_score:
            return "reduce_exit"
        return "hold"
    if (
        inp.buy_allowed
        and inp.regime != "risk_off"
        and inp.score >= cfg.buy_min_score
        and inp.confidence >= cfg.buy_min_confidence
        and inp.agreeing_sources >= cfg.buy_min_agreeing_sources
        and inp.rr >= cfg.buy_min_rr
        and inp.valuation_percentile <= cfg.buy_max_valuation_percentile
    ):
        return "buy_accumulate"
    if inp.score >= cfg.watch_min_score:
        return "watch"
    return "stay_out"
