from pipeline.action_label import ActionLabelConfig, ActionLabelInput, action_label

DEFAULT_CFG = ActionLabelConfig(
    min_confidence_floor=0.3,
    buy_min_score=70,
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
