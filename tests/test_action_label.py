from pipeline.action_label import ActionLabelConfig, ActionLabelInput, action_label

DEFAULT_CFG = ActionLabelConfig(
    min_confidence_floor=0.3,
    buy_min_score=70,
    buy_min_confidence=0.6,
    buy_min_agreeing_sources=3,
    buy_min_rr=2.0,
    buy_max_valuation_percentile=70,
    watch_min_score=55,
    reduce_exit_max_score=40,
)


def _base_input(**overrides):
    defaults = dict(
        coverage_insufficient=False,
        gate_blocked=False,
        data_stale=False,
        confidence=0.7,
        holding_state="none",
        thesis_invalidated=False,
        score=80,
        buy_allowed=True,
        regime="risk_on",
        agreeing_sources=4,
        rr=2.5,
        valuation_percentile=50,
    )
    defaults.update(overrides)
    return ActionLabelInput(**defaults)


def test_insufficient_coverage_returns_none():
    assert action_label(_base_input(coverage_insufficient=True), DEFAULT_CFG) is None


def test_gate_blocked_returns_stay_out():
    assert action_label(_base_input(gate_blocked=True), DEFAULT_CFG) == "stay_out"


def test_data_stale_returns_stay_out():
    assert action_label(_base_input(data_stale=True), DEFAULT_CFG) == "stay_out"


def test_confidence_below_floor_returns_stay_out():
    assert action_label(_base_input(confidence=0.1), DEFAULT_CFG) == "stay_out"


def test_holding_with_invalidated_thesis_returns_reduce_exit():
    inp = _base_input(holding_state="holding", thesis_invalidated=True)
    assert action_label(inp, DEFAULT_CFG) == "reduce_exit"


def test_holding_with_score_below_reduce_threshold_returns_reduce_exit():
    inp = _base_input(holding_state="holding", score=30)
    assert action_label(inp, DEFAULT_CFG) == "reduce_exit"


def test_holding_with_valid_thesis_and_ok_score_returns_hold():
    inp = _base_input(holding_state="holding", score=60, thesis_invalidated=False)
    assert action_label(inp, DEFAULT_CFG) == "hold"


def test_all_buy_conditions_met_returns_buy_accumulate():
    assert action_label(_base_input(), DEFAULT_CFG) == "buy_accumulate"


def test_buy_not_allowed_falls_through_to_watch():
    inp = _base_input(buy_allowed=False)
    assert action_label(inp, DEFAULT_CFG) == "watch"


def test_risk_off_regime_blocks_buy_falls_to_watch():
    inp = _base_input(regime="risk_off")
    assert action_label(inp, DEFAULT_CFG) == "watch"


def test_score_below_watch_threshold_returns_stay_out():
    inp = _base_input(score=40, buy_allowed=False)
    assert action_label(inp, DEFAULT_CFG) == "stay_out"


def test_confidence_below_buy_min_falls_through_to_watch():
    # §10.5: this is the gate tier B's capped confidence must actually hit —
    # previously min_confidence was declared in config but never checked.
    inp = _base_input(confidence=0.5)
    assert action_label(inp, DEFAULT_CFG) == "watch"


# --- provisional (in-session) label rule ---
from pipeline.action_label import provisional_label


def test_provisional_label_downgrades_only_on_a_risk_trigger():
    assert provisional_label("stay_out", "watch", risk_triggered=True) == ("stay_out", True)
    assert provisional_label("reduce_exit", "hold", risk_triggered=True) == ("reduce_exit", True)
    assert provisional_label("stay_out", "watch", risk_triggered=False) == ("watch", False)  # noise: keep official


def test_provisional_label_never_upgrades_before_the_close():
    assert provisional_label("buy_accumulate", "watch", risk_triggered=False) == ("watch", False)
    assert provisional_label("watch", "stay_out", risk_triggered=True) == ("stay_out", False)


def test_provisional_label_without_official_caps_buy_and_stores_nothing():
    assert provisional_label("buy_accumulate", None, risk_triggered=False) == ("watch", False)
    assert provisional_label("stay_out", None, risk_triggered=True) == ("stay_out", False)
    assert provisional_label(None, "watch", risk_triggered=False) == ("watch", False)


# --- per-user holder view of a position-neutral (shared) label ---
from pipeline.action_label import personal_label


def test_personal_label_matches_labelling_the_holder_directly():
    for score in (20, 39, 40, 50, 55, 69, 70, 90):
        for conf in (0.1, 0.5, 0.7):
            for stale in (False, True):
                for regime in ("risk_on", "risk_off"):
                    neutral = action_label(_base_input(holding_state="unknown", score=score, confidence=conf,
                                                       data_stale=stale, regime=regime), DEFAULT_CFG)
                    holder = action_label(_base_input(holding_state="holding", score=score, confidence=conf,
                                                      data_stale=stale, regime=regime), DEFAULT_CFG)
                    assert personal_label(neutral, "holding", score, conf, stale, DEFAULT_CFG) == holder
                    assert personal_label(neutral, "none", score, conf, stale, DEFAULT_CFG) == neutral


def test_personal_label_turns_a_synthesis_downgrade_into_exit_for_a_holder():
    assert personal_label("stay_out", "holding", 75, 0.7, False, DEFAULT_CFG) == "reduce_exit"
    assert personal_label(None, "holding", 75, 0.7, False, DEFAULT_CFG) is None
