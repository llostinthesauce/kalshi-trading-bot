"""
Simplified Dashboard with PnL Tracking
Displays portfolio data and unrealized paper profits.
"""

import asyncio
from datetime import datetime
from src.utils.database import DatabaseManager
from src.clients.kalshi_client import KalshiClient

class SimpleDashboard:
    def __init__(self):
        self.db = DatabaseManager()
        self.kalshi = KalshiClient()

    async def show(self):
        while True:
            try:
                print("\033[2J\033[H", end="")
                print(f"📊 KALSHI TRADING DASHBOARD - {datetime.now().strftime('%H:%M:%S')}")
                print("=" * 80)
                
                # Positions
                positions = await self.db.get_open_positions()
                print(f"\n📂 Active Positions: {len(positions)}")
                
                total_pnl = 0
                if positions:
                    print(f"{'Ticker':<45} | {'Side':<5} | {'Entry':<7} | {'Curr':<7} | {'PnL':<8}")
                    print("-" * 85)
                    
                    for p in positions:
                        curr_price = 0.5 # Default
                        try:
                            # Fetch specific market for accurate price
                            curr_m = await self.kalshi.get_market(p.market_id)
                            # Use mid price or last price for valuation (convert cents to dollars)
                            # Logic: (Ask + Bid) / 2 / 100
                            ask = curr_m.get('yes_ask') or curr_m.get('last_price') or 50
                            bid = curr_m.get('yes_bid') or curr_m.get('last_price') or 50
                            curr_price = (ask + bid) / 200.0
                        except Exception:
                            # Fallback if market closed or fetch failed
                            curr_price = 0.5
                        
                        # PnL calculation
                        if p.side == "YES":
                            pnl = (curr_price - p.entry_price) * p.quantity
                        else:
                            # For NO, value is (1 - YES_price)
                            pnl = ((1.0 - curr_price) - (1.0 - p.entry_price)) * p.quantity
                        
                        total_pnl += pnl
                        pnl_str = f"${pnl:+.2f}"
                        # Truncate ticker less aggressively for better visibility
                        display_ticker = (p.market_id[:42] + "..") if len(p.market_id) > 44 else p.market_id
                        print(f"{display_ticker:<45} | {p.side:<5} | ${p.entry_price:<6.2f} | ${curr_price:<6.2f} | {pnl_str:<8}")
                
                print("-" * 85)
                print(f"💰 Total Unrealized Paper PnL: ${total_pnl:+.2f}")
                
                # Recent Analysis
                analyses = await self.db.get_recent_analyses(limit=5)
                print(f"\n🧠 Recent Grok Analysis:")
                for a in analyses:
                    conf = a.get('confidence') if a.get('confidence') is not None else 0.0
                    decision = a.get('decision_action') if a.get('decision_action') else "UNKNOWN"
                    print(f" - {a.get('market_id')[:50]}: {decision} ({conf:.1%})")
                
                # Cost
                cost = await self.db.get_daily_ai_cost()
                print(f"\n💸 Daily AI Spending: ${cost:.2f}")
                
                print("\n" + "=" * 80)
                print("🔄 Updating every 60s... Ctrl+C to exit")
                await asyncio.sleep(60)
            except Exception as e:
                print(f"Dashboard error: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    dash = SimpleDashboard()
    asyncio.run(dash.show())
