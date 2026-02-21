
import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure src is importable
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
load_dotenv()

from src.utils.database import DatabaseManager

async def main():
    db = DatabaseManager()
    positions = await db.get_open_positions()
    print(f"\n📂 ACTIVE TRADES ({len(positions)}):")
    print("-" * 60)
    for p in positions:
        print(f"ID: {p.id} | {p.market_id} | {p.side} | Price: ${p.entry_price:.2f} | {p.timestamp.strftime('%H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())
