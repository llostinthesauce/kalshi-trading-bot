"""
Refactored Kalshi API Client
Supports direct Private Key string from .env and buy_max_cost orders.
"""

import asyncio
import base64
import json
import time
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

from src.utils.logging_setup import TradingLoggerMixin


class KalshiClient(TradingLoggerMixin):
    """Simplified Kalshi API client."""
    
    def __init__(self, api_key: Optional[str] = None, private_key_str: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("KALSHI_API_KEY")
        self.private_key_str = private_key_str or os.getenv("KALSHI_PRIVATE_KEY")
        self.base_url = base_url or "https://api.elections.kalshi.com"
        
        # Load private key from string
        self.private_key = self._load_private_key(self.private_key_str)
        
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0
        )
        
        self.logger.info("KalshiClient initialized")

    def _load_private_key(self, key_str: str):
        """Parse RSA private key from PEM string."""
        if not key_str:
            self.logger.error("No private key string provided")
            return None
        try:
            # Clean up the string (handle literal \n and real newlines)
            formatted_key = key_str.replace("\\n", "\n").replace('"', '').strip().encode()
            return serialization.load_pem_private_key(
                formatted_key,
                password=None,
                backend=default_backend()
            )
        except Exception as e:
            self.logger.error(f"Failed to load private key: {e}")
            return None

    def _get_auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """Generate mandatory Kalshi auth headers."""
        timestamp = str(int(time.time() * 1000))
        msg = f"{timestamp}{method.upper()}{path}"
        
        signature = self.private_key.sign(
            msg.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json"
        }

    async def get_balance(self) -> float:
        """Return available balance in USD."""
        headers = self._get_auth_headers("GET", "/trade-api/v2/portfolio/balance")
        resp = await self.client.get("/trade-api/v2/portfolio/balance", headers=headers)
        resp.raise_for_status()
        return resp.json().get("balance", 0) / 100.0

    async def get_active_markets(self, limit: int = 200) -> List[Dict]:
        """Fetch currently tradeable markets using cursor pagination."""
        path = "/trade-api/v2/markets"
        all_markets: List[Dict] = []
        cursor = None
        page_size = min(200, limit)

        while len(all_markets) < limit:
            params = {"status": "open", "limit": page_size}
            if cursor:
                params["cursor"] = cursor
            headers = self._get_auth_headers("GET", path)
            resp = await self.client.get(path, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            page = data.get("markets", [])
            all_markets.extend(page)
            cursor = data.get("cursor")
            if not cursor or not page:
                break

        return all_markets[:limit]

    async def get_markets_by_categories(self, categories: List[str], limit: int = 500) -> List[Dict]:
        """
        Fetch open markets for the given event categories via the events API.
        Uses with_nested_markets=true so each event includes its market contracts.
        Much more efficient than paginating through all markets when sports parlays dominate.
        """
        path = "/trade-api/v2/events"
        all_markets: List[Dict] = []
        seen_tickers: set = set()

        for category in categories:
            cursor = None
            while len(all_markets) < limit:
                params = {
                    "status": "open",
                    "limit": 200,
                    "category": category,
                    "with_nested_markets": "true",
                }
                if cursor:
                    params["cursor"] = cursor
                headers = self._get_auth_headers("GET", path)
                resp = await self.client.get(path, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                events = data.get("events", [])

                for event in events:
                    for m in event.get("markets", []):
                        ticker = m.get("ticker", "")
                        if ticker and ticker not in seen_tickers:
                            seen_tickers.add(ticker)
                            all_markets.append(m)

                cursor = data.get("cursor")
                if not cursor or not events:
                    break

        return all_markets[:limit]

    async def get_btc_markets(self) -> List[Dict]:
        """Fetch open Bitcoin price range markets (KXBTC series).
        Returns all open KXBTC markets — time filtering happens in main.py."""
        path = "/trade-api/v2/events"
        all_markets: List[Dict] = []
        seen_tickers: set = set()

        params = {
            "status": "open",
            "series_ticker": "KXBTC",
            "with_nested_markets": "true",
            "limit": 200,
        }
        headers = self._get_auth_headers("GET", path)
        resp = await self.client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        for event in data.get("events", []):
            for m in event.get("markets", []):
                ticker = m.get("ticker", "")
                if ticker and ticker not in seen_tickers:
                    seen_tickers.add(ticker)
                    all_markets.append(m)

        return all_markets

    async def get_weather_markets(self) -> List[Dict]:
        """Fetch open weather markets via the events API category."""
        path = "/trade-api/v2/events"
        all_markets = []
        seen_tickers = set()

        cursor = None
        while True:
            params = {
                "status": "open",
                "limit": 200,
                "category": "Climate and Weather",
                "with_nested_markets": "true",
            }
            if cursor:
                params["cursor"] = cursor
            headers = self._get_auth_headers("GET", path)
            resp = await self.client.get(path, params=params, headers=headers)
            
            if resp.status_code != 200:
                break
                
            data = resp.json()
            events = data.get("events", [])

            for event in events:
                # The EVENT title contains the location (e.g. 'Highest temperature in Miami today?')
                # The MARKET title contains just the bucket (e.g. '71° or above')
                # But Kalshi also associates a series ticker like KXHIGHT.*
                event_title = event.get("title", "").upper()
                series = event.get("series_ticker", "").upper()
                
                # Kalshi uses KXHIGHMIA, KXHIGHTDC, etc for daily highs
                if "KXHIGH" in series or "KXHIGHT" in series or ("HIGH" in event_title and "TEMP" in event_title):
                    for m in event.get("markets", []):
                        ticker = m.get("ticker", "")
                        if ticker and ticker not in seen_tickers:
                            seen_tickers.add(ticker)
                            # Embed the event title into the market dict so the strategy can parse the location
                            m["event_title"] = event.get("title")
                            all_markets.append(m)

            cursor = data.get("cursor")
            if not cursor or not events:
                break

        return all_markets

    async def get_market_history(self, ticker: str, limit: int = 100) -> List[Dict]:
        """Fetch candlestick or trade history for a specific market to detect trends."""
        path = f"/trade-api/v2/markets/{ticker}/history"
        params = {"limit": min(limit, 1000)}
        headers = self._get_auth_headers("GET", path)
        resp = await self.client.get(path, params=params, headers=headers)
        
        # 404 means no history or invalid ticker
        if resp.status_code == 404:
            return []
            
        resp.raise_for_status()
        return resp.json().get("history", [])

    async def get_market(self, ticker: str) -> Dict:
        """Fetch details for a specific market ticker (authenticated)."""
        path = f"/trade-api/v2/markets/{ticker}"
        headers = self._get_auth_headers("GET", path)
        resp = await self.client.get(path, headers=headers)
        resp.raise_for_status()
        return resp.json().get("market", {})

    async def get_positions(self) -> List[Dict]:
        """Fetch current positions from Kalshi to check settlement status."""
        path = "/trade-api/v2/portfolio/positions"
        headers = self._get_auth_headers("GET", path)
        resp = await self.client.get(path, headers=headers)
        resp.raise_for_status()
        return resp.json().get("market_positions", [])

    async def close_position(self, ticker: str, side: str, quantity: int, price_cents: int) -> Dict:
        """Market-sell an existing position to close immediately.
        Uses type=market to guarantee fill rather than resting on the book."""
        import uuid
        path = "/trade-api/v2/portfolio/orders"
        price_field = "yes_price" if side.lower() == "yes" else "no_price"
        payload = {
            "ticker": ticker,
            "action": "sell",
            "type": "market",
            "side": side.lower(),
            price_field: max(1, price_cents - 2),  # sell slightly below bid to guarantee fill
            "count": quantity,
            "client_order_id": str(uuid.uuid4()),
        }
        headers = self._get_auth_headers("POST", path)
        resp = await self.client.post(path, headers=headers, json=payload)
        result = resp.json()
        if not resp.is_success:
            print(f"  ⚠️ CLOSE REJECTED [{resp.status_code}] {ticker} {side} — {result}")
            return None
        print(f"  ✅ POSITION CLOSED {ticker} {side} qty={quantity}")
        return result

    async def place_market_order(self, ticker: str, side: str, amount_usd: float, price_cents: int = 50) -> Dict:
        """Place a resting limit order. count is derived from budget/price for cost control."""
        path = "/trade-api/v2/portfolio/orders"
        import uuid

        # Derive contract count from budget — this bounds total cost without buy_max_cost
        # (buy_max_cost triggers FOK behavior which causes rejections on thin books)
        safe_price = max(1, price_cents)
        count = max(1, int(amount_usd * 100) // safe_price)

        price_field = "yes_price" if side.lower() == "yes" else "no_price"

        payload = {
            "ticker": ticker,
            "action": "buy",
            "type": "limit",
            "side": side.lower(),
            price_field: price_cents,
            "count": count,
            "client_order_id": str(uuid.uuid4())
        }
        
        headers = self._get_auth_headers("POST", path)
        resp = await self.client.post(path, headers=headers, json=payload)
        result = resp.json()
        if not resp.is_success:
            print(f"  ⚠️ ORDER REJECTED [{resp.status_code}] {ticker} {side} — {result}")
            return None
        print(f"  ✅ ORDER PLACED {ticker} {side} count={count} max_cost=${amount_usd:.2f}")
        return result

    async def close(self):
        await self.client.aclose()
