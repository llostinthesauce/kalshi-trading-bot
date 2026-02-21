import re
from datetime import datetime
import time

# Strategy Settings
ENTRY_THRESHOLD = 0.15      # 15 cents
EXIT_THRESHOLD = 0.45       # 45 cents
MAX_POSITION_USD = 1.00     # $1.00 per trade
LOCATIONS = ["NYC", "Chicago", "Seattle", "Atlanta", "Dallas", "Miami"]

# Safeguards
SLIPPAGE_MAX_CENT = 3       # e.g., max 3 cent spread
TIME_TO_RESOLUTION_MIN_HOURS = 2
PRICE_DROP_THRESHOLD = 0.10 # 10% drop over recent history for trend detection

class WeatherStrategy:
    """Core logic for finding discrepancies between NOAA forecasts and Kalshi market prices."""
    
    @staticmethod
    def parse_market_info(market: dict) -> dict:
        """
        Parses Kalshi's weather market title & ticker to extract location, date, and temperature bucket.
        Kalshi market titles usually look like: 'Will the maximum temperature be 52-53° on Feb 22, 2026?'
        The parent event title (which we injected as 'event_title') usually looks like: 'Highest temperature in Seattle on Feb 22, 2026?'
        """
        # The location is usually only in the event title, not the individual market title
        market_title = market.get("title", "").upper()
        event_title = market.get("event_title", "").upper()
        ticker = market.get("ticker", "").upper()
        subtitle = market.get("subtitle", "").upper() # Keep subtitle for bucket parsing
        
        info = {
            "location": None,
            "date_str": None,
            "bucket_low": None,
            "bucket_high": None,
            "is_above": False,
            "is_below": False
        }

        # 1. Location Matching
        for loc in LOCATIONS:
            if loc.upper() in event_title or loc.upper() in market_title: # Check both event_title and market_title
                info["location"] = loc
                break
                
        # Handle NYC specifically if it uses New York
        if not info["location"] and ("NEW YORK" in event_title or "NEW YORK" in market_title):
            info["location"] = "NYC"
            
        # 2. Determine bucket from market_title (subtitle is often blank now)
        # Formats: 
        # "54-55°" or "54 - 55"
        range_match = re.search(r'(\d+)\s*-\s*(\d+)°?', market_title)
        if range_match and int(range_match.group(1)) < 2000: # avoid matching the year 2026
            info["bucket_low"] = int(range_match.group(1))
            info["bucket_high"] = int(range_match.group(2))
        else:
            # ">55°" or "HIGHER"
            high_match = re.search(r'>(\d+)°?|(\d+).*HIGHER|ABOVE|MORE', market_title)
            if high_match:
                val = high_match.group(1) or high_match.group(2)
                info["bucket_low"] = int(val)
                info["bucket_high"] = 999
            else:
                # "<48°" or "LOWER"
                low_match = re.search(r'<(\d+)°?|(\d+).*LOWER|BELOW|LESS', market_title)
                if low_match:
                    val = low_match.group(1) or low_match.group(2)
                    info["bucket_low"] = -999
                    info["bucket_high"] = int(val)
                    
        # 4. Extract Date
        date_match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d+)', event_title)
        if not date_match:
            date_match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d+)', market_title)
            
        if date_match:
            month_str = date_match.group(1).title() # "Feb" instead of "FEB" for strptime
            day_str = date_match.group(2)
            try:
                # Assuming the year is 2026 for now, will need to make this dynamic
                dt = datetime.strptime(f"{month_str} {day_str} 2026", "%b %d %Y")
                info["date_str"] = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        return info

    @staticmethod
    def is_forecast_match(bucket_low: int, bucket_high: int, forecast_high: int) -> bool:
        """Checks if the NOAA forecast hits the target Kalshi bucket exactly."""
        if bucket_low is None or bucket_high is None or forecast_high is None:
            return False
        return bucket_low <= forecast_high <= bucket_high

    @staticmethod
    def check_safeguards(market: dict, history: list) -> tuple:
        """
        Applies trend detection and time decay safeguards.
        Returns: (bool passed, str reason)
        """
        # Time decay
        close_time = market.get("close_time")
        if close_time:
            try:
                dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                hours_left = (dt.timestamp() - time.time()) / 3600.0
                if hours_left < TIME_TO_RESOLUTION_MIN_HOURS:
                    return False, f"Resolves in {hours_left:.1f}h - too soon"
            except Exception:
                pass
                
        yes_ask = market.get("yes_ask", 0) or 0
        yes_bid = market.get("yes_bid", 0) or 0
        
        # Spread
        if (yes_ask - yes_bid) > SLIPPAGE_MAX_CENT:
            return False, f"Spread too wide ({yes_ask - yes_bid}c)"
            
        # Trend Detection
        # We look for a recent price drop as a stronger entry signal (buy the dip)
        if history and len(history) >= 2:
            # history is ordered oldest to newest in Kalshi usually, let's grab newest vs older
            recent_price = history[-1].get("yes_price", yes_ask)
            
            # Find a price from ~24 hours ago (or oldest available if < 24h)
            old_price = history[0].get("yes_price", recent_price)
            if old_price > 0:
                change = (recent_price - old_price) / float(old_price)
                if change > PRICE_DROP_THRESHOLD:
                    return False, f"Price trend is upward ({change:.1%}) - no dip to buy"
                    
        return True, "Passed safeguards"
