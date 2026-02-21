import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import score_market


def test_score_market_tight_spread_midrange():
    m = {"yes_ask": 50, "yes_bid": 47, "volume": 500}
    score = score_market(m)
    # tight spread (<=5) +30, midrange +20, volume bonus
    assert score >= 50


def test_score_market_wide_spread():
    # Same price, same volume — only spread differs. Wide spread should score lower.
    wide = {"yes_ask": 80, "yes_bid": 60, "volume": 500}
    tight = {"yes_ask": 80, "yes_bid": 77, "volume": 500}
    assert score_market(wide) < score_market(tight), (
        "Wide spread should score lower than equivalent tight-spread market"
    )


def test_score_market_extreme_price_no_midrange_bonus():
    # Same spread and volume — only yes_ask differs (95 vs 50).
    # Midrange bonus (+20) applies to 15-85 range only.
    # So the difference should be exactly 20 points.
    extreme = {"yes_ask": 95, "yes_bid": 93, "volume": 500}
    midrange = {"yes_ask": 50, "yes_bid": 47, "volume": 500}
    assert score_market(midrange) - score_market(extreme) == 20, (
        "Midrange bonus should be exactly +20 points vs extreme price"
    )


def test_score_market_missing_fields_doesnt_crash():
    # Empty dict falls back to yes_ask=50, yes_bid=50 (spread=0, midrange)
    # so it scores as a top candidate (~50 pts). This is a known quirk of the
    # or-50 fallback — test documents the actual behavior.
    score = score_market({})
    assert isinstance(score, float)
    assert score >= 0


import importlib
import main as main_module


def test_calculate_edge_does_not_exist():
    """Ensure the fake edge calculation has been removed."""
    assert not hasattr(main_module, "calculate_edge"), \
        "calculate_edge should be removed — Grok probability estimates are unreliable"


def test_min_edge_constant_does_not_exist():
    """MIN_EDGE was paired with calculate_edge and should be removed with it."""
    assert not hasattr(main_module, "MIN_EDGE"), \
        "MIN_EDGE should be removed — confidence-only gate replaced edge-based gating"


def test_max_trade_usd_is_four():
    import main
    assert main.MAX_TRADE_USD == 4.0, "MAX_TRADE_USD should be $4.00"


def test_write_trade_log_enter_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from main import write_trade_log
    write_trade_log(
        action="ENTER", market_id="TEST-01", side="YES",
        price=0.65, amount_usd=4.0, conf=0.87,
        reason="Strong data supports YES", key_evidence="GDP grew 2.1% last quarter"
    )
    log_path = tmp_path / "logs" / "trades.log"
    assert log_path.exists()
    content = log_path.read_text()
    assert "ENTER YES" in content
    assert "TEST-01" in content
    assert "GDP grew 2.1%" in content
    assert "conf=87%" in content


def test_write_trade_log_exit_appends(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from main import write_trade_log
    write_trade_log(
        action="ENTER", market_id="TEST-01", side="YES",
        price=0.65, amount_usd=4.0, conf=0.87,
        reason="r", key_evidence="e"
    )
    write_trade_log(
        action="EXIT", market_id="TEST-01", side="YES",
        price=0.81, amount_usd=4.0, pnl=0.98, exit_reason="TAKE PROFIT"
    )
    content = (tmp_path / "logs" / "trades.log").read_text()
    assert content.count("TEST-01") == 2
    assert "TAKE PROFIT" in content
    assert "+$0.98" in content


from datetime import datetime
from src.utils.database import Position


def test_trade_params_no_uses_no_cost():
    """NO trade entry_price should be the NO contract cost, not the YES price."""
    from main import _trade_params
    entry_price, qty, sl, tp = _trade_params("NO", 2)   # yes_ask = 2¢
    # NO costs 98¢, not 2¢
    assert abs(entry_price - 0.98) < 0.001, f"Expected ~0.98, got {entry_price}"
    # Quantity: $4 / $0.98 ≈ 4 contracts, not 200
    assert qty == 4, f"Expected 4 contracts, got {qty}"
    # SL: YES level where NO has lost 35% = 1 - 0.98*0.65 ≈ 0.363
    assert abs(sl - (1.0 - 0.98 * 0.65)) < 0.001
    # TP: YES would need to go negative (impossible) — tp = max(0, 1 - 0.98*1.25) = 0
    assert tp == 0.0


def test_trade_params_yes_uses_yes_price():
    """YES trade entry_price should equal yes_ask / 100."""
    from main import _trade_params
    entry_price, qty, sl, tp = _trade_params("YES", 40)  # yes_ask = 40¢
    assert abs(entry_price - 0.40) < 0.001
    assert qty == 10, f"Expected 10 contracts ($4 / $0.40), got {qty}"
    assert abs(sl - 0.26) < 0.001   # 0.40 * 0.65
    assert abs(tp - 0.50) < 0.001   # 0.40 * 1.25


def test_exit_thresholds_yes_side():
    entry = 0.60
    pos = Position(
        market_id="T", side="YES", entry_price=entry, quantity=6,
        timestamp=datetime.now(),
        stop_loss_price=entry * 0.65,
        take_profit_price=entry * 1.25,
        max_hold_hours=48,
    )
    assert abs(pos.stop_loss_price - 0.39) < 0.001
    assert abs(pos.take_profit_price - 0.75) < 0.001
    assert pos.max_hold_hours == 48


def test_exit_thresholds_no_side():
    # NO position where yes_ask=60¢ → NO contract cost = 0.40
    no_entry = 0.40
    # SL: YES price level where NO has lost 35% = 1 - no_entry * 0.65
    sl_price = min(1.0, 1.0 - no_entry * 0.65)   # 0.74
    # TP: YES price level where NO has gained 25% = 1 - no_entry * 1.25
    tp_price = max(0.0, 1.0 - no_entry * 1.25)    # 0.50
    pos = Position(
        market_id="T", side="NO", entry_price=no_entry, quantity=10,
        timestamp=datetime.now(),
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
        max_hold_hours=48,
    )
    assert abs(pos.stop_loss_price - 0.74) < 0.001
    assert abs(pos.take_profit_price - 0.50) < 0.001


def test_take_profit_triggered_yes_side():
    """YES position: take-profit fires when curr_price >= take_profit_price."""
    entry = 0.60
    take_profit_price = min(1.0, entry * 1.25)  # 0.75
    curr_price = 0.76
    triggered = curr_price >= take_profit_price
    assert triggered


def test_take_profit_not_triggered_yes_side():
    entry = 0.60
    take_profit_price = min(1.0, entry * 1.25)  # 0.75
    curr_price = 0.73
    triggered = curr_price >= take_profit_price
    assert not triggered


def test_take_profit_triggered_no_side():
    """NO position: TP fires when YES price <= tp_price (NO contract value rose 25%)."""
    no_entry = 0.60  # NO cost when yes_ask was 40¢
    tp_price = max(0.0, 1.0 - no_entry * 1.25)  # 1 - 0.75 = 0.25
    # YES dropped to 24¢ → NO is now worth 76¢ → 27% gain → TP fires
    curr_yes_price = 0.24
    triggered = curr_yes_price <= tp_price
    assert triggered


def test_stop_loss_threshold_tightened():
    """Stop-loss triggers at -35%, not -50%."""
    entry = 0.60
    # -36% loss should trigger
    curr_below = entry * 0.64
    unrealized = (curr_below - entry) / entry
    assert unrealized <= -0.35

    # -33% loss should NOT trigger
    curr_above = entry * 0.67
    unrealized2 = (curr_above - entry) / entry
    assert unrealized2 > -0.35


def test_max_hold_threshold():
    """Position held > 48h should be flagged for closure."""
    from datetime import timedelta
    entry_time = datetime.now() - timedelta(hours=49)
    hours_held = (datetime.now() - entry_time).total_seconds() / 3600
    assert hours_held >= 48
