import asyncio
import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv

sys.path.append(os.getcwd())

from src.clients.kalshi_client import KalshiClient
from src.utils.database import DatabaseManager, Position
from src.utils.noaa_client import NOAAClient
from src.strategies.weather_strategy import (
    WeatherStrategy, ENTRY_THRESHOLD, EXIT_THRESHOLD, MAX_POSITION_USD, LOCATIONS
)

load_dotenv()

LIVE_MODE = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
SCAN_INTERVAL_MINS = 2
MAX_TRADES_PER_CYCLE = 5

async def monitor_weather_positions(db: DatabaseManager, kalshi: KalshiClient):
    """Checks open weather positions to see if exit threshold (>=45c) is met."""
    positions = await db.get_open_positions()
    if not positions:
        return

    weather_positions = [p for p in positions if p.strategy == "weather_edge"]
    if not weather_positions:
        return

    print(f"\n📋 Checking {len(weather_positions)} open weather position(s)...")
    for pos in weather_positions:
        try:
            market = await kalshi.get_market(pos.market_id)
            if not market:
                continue
                
            status = market.get("status", "")
            result = market.get("result", "")

            # Settlement checking
            if status in ("settled", "closed", "finalized") and result:
                exit_value = 1.0 if pos.side.upper() == result.upper() else 0.0
                pnl = (exit_value - pos.entry_price) * pos.quantity
                await db.close_position_with_pnl(pos, exit_value)
                outcome = "WIN" if pnl > 0 else "LOSS"
                print(f"  {'✅' if pnl > 0 else '❌'} SETTLED {outcome}: "
                      f"{pos.market_id} → PnL: {'+' if pnl >= 0 else ''}${pnl:.2f}")
                continue

            # Take Profit Checking
            yes_bid = market.get("yes_bid", 0) or 0
            if pos.side == "YES" and (yes_bid / 100.0) >= EXIT_THRESHOLD:
                exit_price = yes_bid / 100.0
                pnl = (exit_price - pos.entry_price) * pos.quantity
                if LIVE_MODE:
                    r = await kalshi.close_position(pos.market_id, "YES", pos.quantity, yes_bid)
                    if r is not None:
                        await db.close_position_with_pnl(pos, exit_price)
                        print(f"  💰 EXIT threshold reached: {pos.market_id} | PnL: +${pnl:.2f}")
                else:
                    await db.close_position_with_pnl(pos, exit_price)
                    print(f"  💰 EXIT (paper): {pos.market_id} | PnL: +${pnl:.2f}")
                    
        except Exception as e:
            print(f"  ⚠️  Error checking {pos.market_id}: {e}")

async def run_weather_bot():
    print(f"🌦️ Kalshi Weather Bot — {'LIVE' if LIVE_MODE else 'PAPER'} mode")
    print(f"   Entry: <{ENTRY_THRESHOLD * 100:.0f}¢ | Exit: >={EXIT_THRESHOLD * 100:.0f}¢")
    print(f"   Scan: {SCAN_INTERVAL_MINS}min | Max pos: ${MAX_POSITION_USD:.2f}\n")

    db = DatabaseManager()
    await db.initialize()
    
    weather_api_key = os.getenv("KALSHI_WEATHER_API_KEY", os.getenv("KALSHI_API_KEY"))
    weather_priv_key = os.getenv("KALSHI_WEATHER_PRIVATE_KEY", os.getenv("KALSHI_PRIVATE_KEY"))
    
    # Weather markets exclusively live on Kalshi's elections platform now.
    kalshi = KalshiClient(
        api_key=weather_api_key,
        private_key_str=weather_priv_key,
        base_url="https://api.elections.kalshi.com"
    )
    noaa = NOAAClient()

    while True:
        try:
            print(f"\n{'='*60}")
            print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Scanning Kalshi Weather Markets...")

            await monitor_weather_positions(db, kalshi)
            
            # Fetch Kalshi weather markets
            markets = await kalshi.get_weather_markets()
            print(f"  Found {len(markets)} weather markets.")
            if not markets:
                await asyncio.sleep(SCAN_INTERVAL_MINS * 60)
                continue

            # Cache NOAA forecasts so we don't spam 6 requests every 2 minutes unless needed
            forecasts_cache = {}
            for loc in LOCATIONS:
                forecasts_cache[loc] = await noaa.get_forecast(loc)

            candidates = []
            
            for m in markets:
                ticker = m.get("ticker", "")
                yes_ask = m.get("yes_ask", 0) or 0
                
                # Filter strictly by entry price
                if yes_ask <= 0 or (yes_ask / 100.0) >= ENTRY_THRESHOLD:
                    continue

                info = WeatherStrategy.parse_market_info(m)
                loc = info["location"]
                date = info["date_str"]  # Needs to match NOAA's YYYY-MM-DD
                
                if not loc or not date:
                    continue
                    
                loc_forecasts = forecasts_cache.get(loc, {})
                daily_fc = loc_forecasts.get(date)
                
                if not daily_fc:
                    continue
                    
                forecast_high = daily_fc.get("high")
                
                if WeatherStrategy.is_forecast_match(info["bucket_low"], info["bucket_high"], forecast_high):
                    # We have a candidate! Fetch history to check safeguards
                    history = await kalshi.get_market_history(ticker)
                    passed, reason = WeatherStrategy.check_safeguards(m, history)
                    
                    if passed:
                        candidates.append({
                            "market": m,
                            "forecast_high": forecast_high,
                            "price": yes_ask,
                            "info": info
                        })
                    else:
                        print(f"  ⏭️ {ticker} (Forecast Match: {forecast_high}°F) skipped: {reason}")

            # Execute Trades
            trades_this_cycle = 0
            for c in candidates:
                if trades_this_cycle >= MAX_TRADES_PER_CYCLE:
                    break
                    
                m = c["market"]
                ticker = m.get("ticker")
                price_cents = c["price"]
                
                # Check if we already hold it
                held_positions = await db.get_open_positions()
                if any(p.market_id == ticker for p in held_positions):
                    continue

                entry_price = price_cents / 100.0
                quantity = max(1, int(MAX_POSITION_USD * 100) // price_cents)
                reason = f"NOAA forecast {c['forecast_high']}°F hits bucket {c['info']['bucket_low']}-{c['info']['bucket_high']}"

                print(f"  🔥 BUYING {ticker} at {price_cents}¢ (Qty: {quantity})")
                print(f"     Reason: {reason}")

                if LIVE_MODE:
                    result = await kalshi.place_market_order(ticker, "YES", MAX_POSITION_USD, price_cents)
                    if not result:
                        continue
                        
                pos = Position(
                    market_id=ticker,
                    side="YES",
                    entry_price=entry_price,
                    quantity=quantity,
                    timestamp=datetime.now(),
                    rationale=reason,
                    confidence=0.90, # 90% confidence given NOAA accuracy
                    live=LIVE_MODE,
                    status="open",
                    strategy="weather_edge"
                )
                await db.add_position(pos)
                trades_this_cycle += 1

            if trades_this_cycle == 0:
                print("  No executable opportunities found this cycle.")

            print(f"\n💤 Sleeping {SCAN_INTERVAL_MINS} min...")
            await asyncio.sleep(SCAN_INTERVAL_MINS * 60)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"❌ Cycle error: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(run_weather_bot())
