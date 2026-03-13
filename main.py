"""
Kalshi BTC Range Trading Bot — Volatility Edge Strategy

The real edge: compare our GBM-based probability model (using REALIZED
BTC volatility) against the execution price (ask), not the mid-price.

Market structure discovered (Feb 2026):
  - 3 time buckets: ~40min hourly, ~18hr daily, ~7day weekly
  - Only 4-12 liquid markets per bucket (yes_bid > 0)
  - Daily markets (18hr) have the best combination of volume + edge
  - TIME_MAX_MINS was cutting off ALL daily/weekly markets — fixed

Strategy:
  - Track BTC price every 2 min → compute rolling realized volatility
  - For each liquid market: model_prob = GBM terminal distribution P(S_T in [L,U])
  - Compare to execution_price = yes_ask/100 (YES) or no_ask/100 (NO)
  - edge = model_prob - execution_price (accounts for spread)
  - edge < -MIN_EDGE → YES overpriced → BUY NO
  - edge > +MIN_EDGE → YES underpriced → BUY YES
  - Take profit: NO when yes_ask ≤ 5¢, YES when yes_bid ≥ 80¢
  - Stop loss: exit when edge flips or unrealized loss > 40%
"""

import asyncio
import math
import os
import sys
import time
from collections import deque
from statistics import stdev
from dotenv import load_dotenv

sys.path.append(os.getcwd())

from src.clients.kalshi_client import KalshiClient
from src.utils.database import DatabaseManager, Position
from datetime import datetime

import httpx

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
LIVE_MODE             = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
MAX_TRADE_USD         = 2.0
SCAN_INTERVAL_MINS    = 2
MAX_EXPOSURE_PCT      = 0.99
MAX_TRADES_PER_CYCLE  = 3

# ── STRATEGY PARAMS ───────────────────────────────────────────────────────────
TIME_MIN_MINS    = 5       # avoid last-minute thin books
TIME_MAX_MINS    = 30000   # capture hourly (40min), daily (18hr), weekly (7d)
MIN_EDGE         = 0.08    # 8% absolute edge required (model vs execution price)
VOL_LOOKBACK     = 30      # rolling window: 30 prices × 2min = 60min of history
DEFAULT_ANN_VOL  = 0.55    # 55% annualized — BTC baseline before enough data
MIN_ANN_VOL      = 0.20    # floor: never assume less than 20% annual vol
STOP_LOSS_PCT    = 0.40    # exit if unrealized loss exceeds 40% of entry
# ─────────────────────────────────────────────────────────────────────────────

# Rolling BTC price history for volatility estimation
btc_prices: deque = deque(maxlen=VOL_LOOKBACK)


# ── MATH ─────────────────────────────────────────────────────────────────────

def normal_cdf(x: float) -> float:
    """Standard normal CDF via error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def range_probability(btc: float, floor_s: float, cap_s: float,
                      vol_per_min: float, mins: float) -> float:
    """
    P(BTC lands in [floor_s, cap_s] at expiry) using GBM terminal distribution.
    No-drift lognormal model — conservative, appropriate for short-to-medium horizons.
    """
    if mins <= 0:
        return 1.0 if floor_s <= btc <= cap_s else 0.0
    sigma_t = vol_per_min * math.sqrt(mins)
    if sigma_t < 1e-9:
        return 1.0 if floor_s <= btc <= cap_s else 0.0
    d_cap   = math.log(cap_s   / btc) / sigma_t
    d_floor = math.log(floor_s / btc) / sigma_t
    return max(0.0, min(1.0, normal_cdf(d_cap) - normal_cdf(d_floor)))


def estimate_vol(prices: list) -> float:
    """
    Realized vol per minute from 2-minute BTC price samples.
    Falls back to DEFAULT_ANN_VOL until we have ≥5 samples.
    """
    if len(prices) < 5:
        return DEFAULT_ANN_VOL / math.sqrt(525_600)  # annual → per-minute

    log_rets = [math.log(prices[i] / prices[i-1])
                for i in range(1, len(prices))
                if prices[i-1] > 0 and prices[i] > 0]
    if len(log_rets) < 3:
        return DEFAULT_ANN_VOL / math.sqrt(525_600)

    vol_2min = stdev(log_rets)            # std dev of 2-min log returns
    vol_per_min = vol_2min / math.sqrt(2) # scale to per-minute
    floor = MIN_ANN_VOL / math.sqrt(525_600)
    return max(vol_per_min, floor)


# ── LOGGING ───────────────────────────────────────────────────────────────────

def write_trade_log(action, market_id, side, price, amount_usd,
                    reason=None, pnl=None, exit_reason=None):
    os.makedirs("logs", exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if action == "ENTER":
        line = (f"[{ts}] ENTER {side:<3}  {market_id:<35}  "
                f"entry={price:.2f}  ${amount_usd:.2f}\n"
                f"  {reason or ''}\n\n")
    else:
        pnl_str = f"+${pnl:.2f}" if pnl and pnl >= 0 else f"-${abs(pnl):.2f}" if pnl else "n/a"
        line = (f"[{ts}] EXIT  {side:<3}  {market_id:<35}  "
                f"exit={price:.2f}  PnL={pnl_str}  {exit_reason or ''}\n\n")
    try:
        with open("logs/trades.log", "a") as f:
            f.write(line)
    except OSError:
        pass


# ── POSITION MONITOR ─────────────────────────────────────────────────────────

async def monitor_positions(db: DatabaseManager, kalshi: KalshiClient,
                            btc: float = None, vol_pm: float = None):
    """Settlement detection + take-profit + stop-loss for both YES and NO positions."""
    positions = await db.get_open_positions()
    if not positions:
        return

    print(f"\n📋 Checking {len(positions)} open position(s)...")
    for pos in positions:
        try:
            market = await kalshi.get_market(pos.market_id)
            status = market.get("status", "")
            result = market.get("result", "")

            # ── Settlement ────────────────────────────────────────────────
            if status in ("settled", "closed", "finalized") and result:
                if result.lower() == "yes":
                    exit_value = 1.0 if pos.side == "YES" else 0.0
                elif result.lower() == "no":
                    exit_value = 0.0 if pos.side == "YES" else 1.0
                else:
                    exit_value = pos.entry_price
                pnl = (exit_value - pos.entry_price) * pos.quantity
                await db.close_position_with_pnl(pos, exit_value)
                outcome = "WIN" if pnl >= 0 else "LOSS"
                print(f"  {'✅' if pnl >= 0 else '❌'} SETTLED {outcome}: "
                      f"{pos.market_id} → {result.upper()} | "
                      f"PnL: {'+' if pnl >= 0 else ''}${pnl:.2f}")
                write_trade_log("EXIT", pos.market_id, pos.side,
                                exit_value, MAX_TRADE_USD,
                                pnl=pnl, exit_reason=f"SETTLED {result.upper()}")
                continue

            yes_ask_now = market.get("yes_ask") or 0
            yes_bid_now = market.get("yes_bid") or 0
            no_bid_now  = market.get("no_bid")  or 0

            # ── Take-profit: NO position ──────────────────────────────────
            if pos.side == "NO" and yes_ask_now > 0 and yes_ask_now <= 5:
                exit_price = no_bid_now / 100.0 if no_bid_now > 0 else (100 - yes_ask_now) / 100.0
                pnl = (exit_price - pos.entry_price) * pos.quantity
                if LIVE_MODE and no_bid_now > 0:
                    r = await kalshi.close_position(pos.market_id, "NO", pos.quantity, no_bid_now)
                    if r is None:
                        pass  # fall through to status display
                    else:
                        await db.close_position_with_pnl(pos, exit_price)
                        print(f"  💰 TAKE PROFIT (NO): {pos.market_id[-20:]} | "
                              f"yes_ask={yes_ask_now}¢ | PnL: +${pnl:.2f}")
                        write_trade_log("EXIT", pos.market_id, pos.side, exit_price,
                                        MAX_TRADE_USD, pnl=pnl,
                                        exit_reason=f"TAKE PROFIT yes_ask={yes_ask_now}¢")
                        continue
                elif not LIVE_MODE:
                    await db.close_position_with_pnl(pos, exit_price)
                    print(f"  💰 TAKE PROFIT (NO paper): {pos.market_id[-20:]} | PnL: +${pnl:.2f}")
                    write_trade_log("EXIT", pos.market_id, pos.side, exit_price,
                                    MAX_TRADE_USD, pnl=pnl,
                                    exit_reason=f"TAKE PROFIT yes_ask={yes_ask_now}¢")
                    continue

            # ── Take-profit: YES position ─────────────────────────────────
            if pos.side == "YES" and yes_bid_now >= 80:
                exit_price = yes_bid_now / 100.0
                pnl = (exit_price - pos.entry_price) * pos.quantity
                if LIVE_MODE:
                    r = await kalshi.close_position(pos.market_id, "YES", pos.quantity, yes_bid_now)
                    if r is None:
                        pass
                    else:
                        await db.close_position_with_pnl(pos, exit_price)
                        print(f"  💰 TAKE PROFIT (YES): {pos.market_id[-20:]} | "
                              f"yes_bid={yes_bid_now}¢ | PnL: +${pnl:.2f}")
                        write_trade_log("EXIT", pos.market_id, pos.side, exit_price,
                                        MAX_TRADE_USD, pnl=pnl,
                                        exit_reason=f"TAKE PROFIT yes_bid={yes_bid_now}¢")
                        continue
                elif not LIVE_MODE:
                    await db.close_position_with_pnl(pos, exit_price)
                    print(f"  💰 TAKE PROFIT (YES paper): {pos.market_id[-20:]} | PnL: +${pnl:.2f}")
                    write_trade_log("EXIT", pos.market_id, pos.side, exit_price,
                                    MAX_TRADE_USD, pnl=pnl,
                                    exit_reason=f"TAKE PROFIT yes_bid={yes_bid_now}¢")
                    continue

            # ── Stop-loss: model-based + fixed percentage ─────────────────
            if pos.side == "NO":
                curr_val = (100 - yes_ask_now) / 100.0 if yes_ask_now else pos.entry_price
            else:
                curr_val = yes_bid_now / 100.0 if yes_bid_now else pos.entry_price

            unrealized_loss_pct = (pos.entry_price - curr_val) / pos.entry_price if pos.entry_price else 0

            # Model-based stop: re-evaluate if BTC data is available
            edge_flipped = False
            if btc and vol_pm:
                try:
                    close_dt = datetime.fromisoformat(
                        market.get("close_time", "").replace("Z", "+00:00"))
                    mins_left = (close_dt.timestamp() - time.time()) / 60
                    floor_s = market.get("floor_strike")
                    cap_s = market.get("cap_strike")
                    if floor_s and cap_s and mins_left > 0:
                        model_prob = range_probability(btc, floor_s, cap_s, vol_pm, mins_left)
                        if pos.side == "YES" and model_prob < (yes_ask_now / 100.0):
                            edge_flipped = True  # model now says YES is overpriced
                        elif pos.side == "NO" and model_prob > (1 - (market.get("no_ask", 0) or 0) / 100.0):
                            edge_flipped = True  # model now says NO is overpriced
                except Exception:
                    pass

            should_stop = False
            stop_reason = ""
            if unrealized_loss_pct >= STOP_LOSS_PCT:
                should_stop = True
                stop_reason = f"STOP LOSS loss={unrealized_loss_pct:.0%} >= {STOP_LOSS_PCT:.0%}"
            elif edge_flipped and unrealized_loss_pct > 0.05:
                should_stop = True
                stop_reason = f"EDGE FLIPPED + losing {unrealized_loss_pct:.0%}"

            if should_stop:
                pnl = (curr_val - pos.entry_price) * pos.quantity
                if LIVE_MODE:
                    if pos.side == "NO" and no_bid_now > 0:
                        r = await kalshi.close_position(pos.market_id, "NO", pos.quantity, no_bid_now)
                    elif pos.side == "YES" and yes_bid_now > 0:
                        r = await kalshi.close_position(pos.market_id, "YES", pos.quantity, yes_bid_now)
                    else:
                        r = None
                    if r is not None:
                        await db.close_position_with_pnl(pos, curr_val)
                        print(f"  🛑 {stop_reason}: {pos.market_id[-20:]} | PnL: ${pnl:.2f}")
                        write_trade_log("EXIT", pos.market_id, pos.side, curr_val,
                                        MAX_TRADE_USD, pnl=pnl, exit_reason=stop_reason)
                        continue
                else:
                    await db.close_position_with_pnl(pos, curr_val)
                    print(f"  🛑 {stop_reason} (paper): {pos.market_id[-20:]} | PnL: ${pnl:.2f}")
                    write_trade_log("EXIT", pos.market_id, pos.side, curr_val,
                                    MAX_TRADE_USD, pnl=pnl, exit_reason=stop_reason)
                    continue

            # ── Status display ────────────────────────────────────────────
            pct = (curr_val - pos.entry_price) / pos.entry_price * 100 if pos.entry_price else 0
            print(f"  📊 {pos.market_id[-22:]} ({pos.side}): "
                  f"entry={pos.entry_price:.2f} now={curr_val:.2f} ({pct:+.0f}%)")

        except Exception as e:
            print(f"  ⚠️  Error checking {pos.market_id}: {e}")


# ── MAIN BOT LOOP ─────────────────────────────────────────────────────────────

async def run_bot():
    print(f"🚀 Kalshi BTC Bot — {'LIVE' if LIVE_MODE else 'PAPER'} mode")
    print(f"   Strategy: Volatility Edge (GBM model vs market price)")
    print(f"   Min edge: {MIN_EDGE:.0%} | Scan: {SCAN_INTERVAL_MINS}min\n")

    db = DatabaseManager()
    await db.initialize()
    kalshi = KalshiClient()

    while True:
        try:
            print(f"\n{'='*60}")
            print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # ── Phase 1: Monitor positions ─────────────────────────────────
            await monitor_positions(db, kalshi, btc=None, vol_pm=None)

            # ── Phase 2: Capital check ─────────────────────────────────────
            balance = await kalshi.get_balance()
            if balance <= 0 and not LIVE_MODE:
                balance = 1000.0

            if LIVE_MODE:
                live_pos = {p["ticker"]: p.get("position", 0)
                            for p in await kalshi.get_positions()}
                for p in await db.get_open_positions():
                    if live_pos.get(p.market_id, 0) == 0:
                        await db.close_position_with_pnl(p, p.entry_price)
                        print(f"  🗑️  RECONCILE: {p.market_id} removed (0 on exchange)")

            positions = await db.get_open_positions()
            held      = {p.market_id for p in positions}
            exposure  = sum(p.entry_price * p.quantity for p in positions)
            total     = balance + exposure
            exp_pct   = exposure / total if total > 0 else 1.0

            print(f"💰 Balance: ${balance:.2f} | Exposure: ${exposure:.2f} "
                  f"({exp_pct:.0%}) | Total: ${total:.2f}")

            if exp_pct >= MAX_EXPOSURE_PCT:
                print(f"⛔ Exposure {exp_pct:.0%} ≥ {MAX_EXPOSURE_PCT:.0%} — skipping new trades")
                await asyncio.sleep(SCAN_INTERVAL_MINS * 60)
                continue

            # ── Phase 3: BTC price + rolling volatility ────────────────────
            try:
                async with httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
                    r.raise_for_status()
                    btc = float(r.json()["data"]["amount"])
            except Exception as e:
                print(f"⚠️  BTC price failed: {e} — skipping cycle")
                await asyncio.sleep(SCAN_INTERVAL_MINS * 60)
                continue

            btc_prices.append(btc)
            vol_pm     = estimate_vol(list(btc_prices))
            vol_ann    = vol_pm * math.sqrt(525_600)
            vol_1h_pct = vol_pm * math.sqrt(60) * 100

            print(f"₿  BTC: ${btc:,.0f} | σ={vol_ann:.0%}/yr ({vol_1h_pct:.2f}%/hr) "
                  f"[{len(btc_prices)}/{VOL_LOOKBACK} samples]")

            # ── Phase 3b: Re-check positions with updated BTC + vol ────────
            await monitor_positions(db, kalshi, btc=btc, vol_pm=vol_pm)

            # ── Phase 4: Scan markets ──────────────────────────────────────
            markets = await kalshi.get_btc_markets()
            now_ts  = time.time()
            candidates = []

            for m in markets:
                ticker = m.get("ticker", "")
                if ticker in held:
                    continue

                try:
                    close_dt  = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
                    mins_left = (close_dt.timestamp() - now_ts) / 60
                except Exception:
                    continue

                if not (TIME_MIN_MINS <= mins_left <= TIME_MAX_MINS):
                    continue

                yes_ask = m.get("yes_ask") or 0
                yes_bid = m.get("yes_bid") or 0
                no_ask  = m.get("no_ask")  or 0
                no_bid  = m.get("no_bid")  or 0
                floor_s = m.get("floor_strike")
                cap_s   = m.get("cap_strike")

                # Require real market (not just a default MM placeholder)
                if not yes_bid or yes_bid <= 0:
                    continue
                if not floor_s or not cap_s:
                    continue
                if yes_ask <= 0 or yes_ask >= 100:
                    continue

                # Model probability
                model_prob = range_probability(btc, floor_s, cap_s, vol_pm, mins_left)

                # Mid-price for display only
                mid_prob = (yes_bid + yes_ask) / 200.0

                # Determine side and compute edge against EXECUTION price (ask)
                # This accounts for the spread — no more phantom edges
                no_ask_price = no_ask if (no_ask is not None and 0 < no_ask < 100) else (100 - yes_bid)

                edge_yes = model_prob - (yes_ask / 100.0)          # edge if we BUY YES at ask
                edge_no  = (1 - model_prob) - (no_ask_price / 100.0)  # edge if we BUY NO at ask

                if edge_yes >= MIN_EDGE:
                    side = "YES"
                    price_cents = yes_ask
                    edge = edge_yes
                elif edge_no >= MIN_EDGE:
                    side = "NO"
                    price_cents = no_ask_price
                    edge = edge_no
                else:
                    continue

                if price_cents <= 0 or price_cents >= 100:
                    continue

                # Position description
                if btc > cap_s:
                    btc_pos = f"BTC {(btc-cap_s)/btc*100:.1f}% above cap"
                elif btc < floor_s:
                    btc_pos = f"BTC {(floor_s-btc)/btc*100:.1f}% below floor"
                else:
                    btc_pos = "BTC inside range"

                candidates.append({
                    "ticker":       ticker,
                    "side":         side,
                    "price_cents":  price_cents,
                    "model_prob":   model_prob,
                    "implied_prob": mid_prob,
                    "exec_prob":    price_cents / 100.0,
                    "edge":         edge,
                    "abs_edge":     abs(edge),
                    "mins_left":    mins_left,
                    "volume":       m.get("volume", 0),
                    "floor":        floor_s,
                    "cap":          cap_s,
                    "btc_pos":      btc_pos,
                    "yes_ask":      yes_ask,
                    "no_bid":       no_bid,
                })

            # Rank by absolute edge (best edge first)
            candidates.sort(key=lambda x: x["abs_edge"], reverse=True)

            print(f"🔍 {len(candidates)} candidate(s) with ≥{MIN_EDGE:.0%} edge "
                  f"(from {len(markets)} KXBTC markets)")

            if not candidates:
                hrs = [c.get("close_time","") for c in markets[:3]]
                # Show what the top markets actually look like
                liquid = [m for m in markets if (m.get("yes_bid") or 0) > 0]
                if liquid:
                    top = liquid[:3]
                    print("   Top liquid markets (no sufficient edge):")
                    for m in top:
                        ya  = m.get("yes_ask",0) or 0
                        yb  = m.get("yes_bid",0) or 0
                        f_s = m.get("floor_strike")
                        c_s = m.get("cap_strike")
                        if f_s and c_s:
                            mp  = range_probability(btc, f_s, c_s, vol_pm,
                                                    (datetime.fromisoformat(
                                                     m["close_time"].replace("Z","+00:00")
                                                    ).timestamp() - now_ts) / 60)
                            print(f"   {m['ticker'][-25:]}: "
                                  f"model={mp:.1%} market={(yb+ya)/2:.0f}¢ "
                                  f"edge={(mp-(yb+ya)/200):+.1%}")
                else:
                    print("   No liquid markets found at all.")

            # ── Phase 5: Execute ───────────────────────────────────────────
            trades_this_cycle = 0

            for c in candidates:
                if trades_this_cycle >= MAX_TRADES_PER_CYCLE:
                    break
                if await db.was_recently_analyzed(c["ticker"], hours=2):
                    continue

                ticker      = c["ticker"]
                side        = c["side"]
                price_cents = c["price_cents"]
                entry_price = price_cents / 100.0
                quantity    = max(1, int(MAX_TRADE_USD * 100) // price_cents)

                reason = (f"{c['btc_pos']} | model={c['model_prob']:.1%} "
                          f"exec={c['exec_prob']:.1%} mid={c['implied_prob']:.1%} "
                          f"edge={c['edge']:+.1%} "
                          f"| {c['mins_left']:.0f}min [{ticker.split('-')[1] if '-' in ticker else ''}]")

                print(f"\n🎯 {side} {ticker}")
                print(f"   {reason}")
                print(f"   @ {price_cents}¢  qty={quantity}  vol={c['volume']}")

                if LIVE_MODE:
                    result = await kalshi.place_market_order(ticker, side, MAX_TRADE_USD, price_cents)
                    if result is None:
                        print(f"   ⚠️  Order rejected")
                        continue

                pos = Position(
                    market_id=ticker,
                    side=side,
                    entry_price=entry_price,
                    quantity=quantity,
                    timestamp=datetime.now(),
                    rationale=reason,
                    confidence=min(0.99, 0.60 + c["abs_edge"] * 2),
                    live=LIVE_MODE,
                    status="open",
                    strategy="vol_edge",
                    stop_loss_price=round(entry_price * (1 - STOP_LOSS_PCT), 4),
                    take_profit_price=None,
                    max_hold_hours=168,  # hold up to 7 days for weekly markets
                )
                await db.add_position(pos)
                write_trade_log("ENTER", ticker, side, entry_price, MAX_TRADE_USD, reason=reason)

                held.add(ticker)
                trades_this_cycle += 1
                print(f"   ✅ Opened")

                await db.record_market_analysis(
                    market_id=ticker,
                    decision_action=side,
                    confidence=pos.confidence,
                    cost_usd=0.0,
                )

            print(f"\n💤 Sleeping {SCAN_INTERVAL_MINS} min...")
            await asyncio.sleep(SCAN_INTERVAL_MINS * 60)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"❌ Cycle error: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run_bot())
