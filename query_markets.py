import asyncio
import json
import sys
import os
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.getcwd())
from src.clients.kalshi_client import KalshiClient

async def main():
    c = KalshiClient()
    
    # Try getting high temp markets for NYC or Chicago
    # Sometimes they are under 'Weather' category
    markets = await c.get_active_markets(limit=1000)
    
    weather_markets = []
    seen_series = set()
    for m in markets:
        ticker = m.get('ticker', '').upper()
        # usually Kalshi tickers are SERIESTICKER-YYMMDD-STRIKE
        series = ticker.split('-')[0]
        title = m.get('title', '').upper()
        if series not in seen_series:
            seen_series.add(series)
            if 'WEATHER' in title or 'TEMPERATURE' in title or 'TEMP' in title or 'HOT' in title or 'COLD' in title or 'CHICAGO' in title or 'MIAMI' in title or 'YORK' in title:
                weather_markets.append({
                    'series': series,
                    'title': m.get('title'),
                    'category': m.get('category')
                })
            
    print(json.dumps(weather_markets[:20], indent=2))
    print(f"Total weather markets found: {len(weather_markets)}")

if __name__ == "__main__":
    asyncio.run(main())
