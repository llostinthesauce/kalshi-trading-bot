"""
Enhanced Grok Analyst
Uses Grok-4-1-fast-reasoning with deep research instructions.
"""

import os
import json
import asyncio
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from xai_sdk import AsyncClient
from xai_sdk.chat import user as xai_user

from src.utils.logging_setup import TradingLoggerMixin


class GrokAnalyst(TradingLoggerMixin):
    """Analyst using Grok-4-1-fast-reasoning with research capabilities."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("XAI_API_KEY")
        self.client = AsyncClient(api_key=self.api_key)
        self.model = "grok-4-1-fast-reasoning"

    async def analyze_market(self, market_data: Dict[str, Any]) -> Optional[Dict]:
        """
        Queries Grok for a trade decision with full market context.
        Returns decision, confidence, and reasoning. No probability field.
        Edge calculation is NOT done — confidence-only gate in main.py.
        """
        title = market_data.get("title", "Unknown")
        yes_ask = market_data.get("yes_ask", 50)
        yes_bid = market_data.get("yes_bid")
        no_ask = market_data.get("no_ask")
        volume = market_data.get("volume_24h", 0)
        close_time = market_data.get("close_time", "unknown")
        category = market_data.get("category", "unknown")
        last_price = market_data.get("last_price")
        open_interest = market_data.get("open_interest")
        subtitle = market_data.get("subtitle")
        floor_strike = market_data.get("floor_strike")
        cap_strike = market_data.get("cap_strike")
        live_btc_price = market_data.get("live_btc_price")

        # Use mid-price as implied probability (more accurate than ask alone)
        if yes_bid is not None and yes_ask is not None:
            implied_prob = round((yes_bid + yes_ask) / 2)
        elif last_price is not None:
            implied_prob = last_price
        else:
            implied_prob = yes_ask

        # Compute human-readable time remaining
        try:
            if isinstance(close_time, str):
                close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                now_aware = datetime.now(timezone.utc)
                hours_left = (close_dt - now_aware).total_seconds() / 3600
                if hours_left < 0:
                    close_display = "already closed"
                elif hours_left < 2:
                    close_display = f"{hours_left:.1f} hours"
                elif hours_left < 48:
                    close_display = f"{hours_left:.0f} hours"
                else:
                    close_display = f"{hours_left / 24:.1f} days"
            else:
                close_display = str(close_time)
        except Exception:
            close_display = str(close_time)

        btc_price_str = f"${live_btc_price:,.0f}" if live_btc_price else "unknown (search required)"
        print(f"\n🧠 [Grok] Analyzing: {title} | BTC: {btc_price_str} | Ask: {yes_ask}¢ | Vol: {volume}")

        range_str = subtitle or (f"${floor_strike:,.0f} – ${cap_strike:,.0f}" if floor_strike and cap_strike else "unknown range")

        # Build price section — use authoritative price if available, else ask Grok to search
        if live_btc_price:
            price_context = f"""AUTHORITATIVE BTC PRICE (from Binance live feed): ${live_btc_price:,.2f}
Use this price as ground truth. Do NOT search for a different price — this is the real current price.
Your job is to reason about whether BTC at ${live_btc_price:,.0f} will end up in the range by close."""
        else:
            price_context = """BTC price not provided. Search for the current BTC/USD price on Binance or CoinGecko."""

        # Pre-compute distance strings for the prompt
        if live_btc_price and floor_strike and cap_strike:
            floor_diff = live_btc_price - floor_strike
            cap_diff = live_btc_price - cap_strike
            floor_str = f"${abs(floor_diff):,.0f} ({abs(floor_diff)/live_btc_price*100:.1f}%) {'above' if floor_diff > 0 else 'below'}"
            cap_str   = f"${abs(cap_diff):,.0f} ({abs(cap_diff)/live_btc_price*100:.1f}%) {'above' if cap_diff > 0 else 'below'}"
            btc_str   = f"${live_btc_price:,.0f}"
        else:
            floor_str = cap_str = "calculate"
            btc_str = "search required"

        prompt = f"""You are a crypto trading analyst evaluating a Bitcoin price range prediction market.
You ONLY recommend NO or SKIP. Never recommend YES.

Market closes in: {close_display}
Price range this contract covers: {range_str}
  (floor: ${floor_strike:,.0f} | cap: ${cap_strike:,.0f})
Market-implied probability of YES: ~{implied_prob}%
Yes ask: {yes_ask}¢ | Yes bid: {yes_bid}¢ | No ask: {no_ask}¢
Volume: {volume} contracts | Open interest: {open_interest}

{price_context}

STEP 1 — Confirm current BTC position relative to range:
- BTC price: {btc_str}
- Distance from floor (${floor_strike:,.0f}): {floor_str}
- Distance from cap   (${cap_strike:,.0f}): {cap_str}
- Search for BTC price 1 hour ago to establish trend direction (rising/flat/falling)
- Search for today's high and low to gauge intraday volatility in dollar terms

STEP 2 — Assess the situation:
- Is BTC currently outside the range? If so, which side and how far?
- What direction is BTC trending — toward the range or away?
- Given today's intraday volatility, is a move into the range plausible in {close_display}?

STEP 3 — Decide:
- NO   → true YES probability is meaningfully LESS than {implied_prob}%, AND BTC is moving away or far enough that reversal is implausible
- SKIP → BTC is inside range, moving toward range, edge is unclear, or confidence is below 0.85

The market pays you {100 - yes_ask}¢/contract to say NO. Only trade with clear edge.
DO recommend NO when: (a) BTC is >2% from nearest boundary AND (b) trending away or flat AND (c) time makes reversal implausible.

Confidence guide:
  - 0.90+: BTC >4% from nearest boundary, flat or moving away, time is short
  - 0.85–0.89: BTC 2-4% from nearest boundary, clearly moving away
  - Below 0.85: SKIP

Return ONLY valid JSON (no markdown, no extra text):
{{
    "decision": "NO" | "SKIP",
    "confidence": 0.0-1.0,
    "current_btc_price": <number>,
    "pct_from_nearest_boundary": <float>,
    "trend": "away" | "toward" | "flat",
    "key_evidence": "One sentence: BTC price, % from nearest boundary, trend direction.",
    "reasoning": "2-3 sentences: where BTC is, where it needs to go, why this is NO/SKIP."
}}"""

        try:
            chat = self.client.chat.create(
                model=self.model,
                temperature=0.1
            )
            chat.append(xai_user(prompt))

            print("💭 Grok is researching the web...", end="\r")

            # Reasoning models can take 60-90s; wrap with timeout so we fail fast
            response = await asyncio.wait_for(chat.sample(), timeout=90.0)
            content = response.content

            # Extract JSON
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                print(f"❌ Grok returned unparsable content for {title}")
                return None

            data = json.loads(json_match.group(0))

            # Force YES → SKIP (extra safety — prompt already says no YES)
            if data.get("decision") == "YES":
                data["decision"] = "SKIP"

            # Console output for transparency
            decision = data.get("decision", "SKIP")
            conf = data.get("confidence", 0)
            dist = data.get("pct_from_nearest_boundary")
            trend = data.get("trend", "?")
            dist_str = f" | {dist:.1f}% from boundary [{trend}]" if dist else ""

            print(f"✅ Result: {decision} (Conf: {conf:.1%}{dist_str})")
            print(f"📝 Rationale: {data.get('reasoning')}")

            return data

        except asyncio.TimeoutError:
            print(f"⏱️ Grok timed out on {title} (>90s) — skipping")
            return None
        except Exception as e:
            self.logger.error("Grok analysis failed", error=str(e))
            print(f"❌ Grok error: {e}")
            return None
