import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

# Locations matching Polymarket/Kalshi typical weather markets.
# Coordinates are approximations that fall within the NOAA grids for these cities/airports.
LOCATIONS = {
    "NYC": {"lat": 40.7769, "lon": -73.8740, "name": "New York City (LaGuardia)"},
    "Chicago": {"lat": 41.9742, "lon": -87.9073, "name": "Chicago (O'Hare)"},
    "Seattle": {"lat": 47.4502, "lon": -122.3088, "name": "Seattle (Sea-Tac)"},
    "Atlanta": {"lat": 33.6407, "lon": -84.4277, "name": "Atlanta (Hartsfield)"},
    "Dallas": {"lat": 32.8998, "lon": -97.0403, "name": "Dallas (DFW)"},
    "Miami": {"lat": 25.7959, "lon": -80.2870, "name": "Miami (MIA)"},
}

NOAA_API_BASE = "https://api.weather.gov"

class NOAAClient:
    """Async client for the US National Weather Service (NOAA) API."""

    def __init__(self):
        self.headers = {
            "User-Agent": "KalshiWeatherBot/1.0",
            "Accept": "application/geo+json",
        }
        # Cache grid endpoints to save an API call
        self._grid_cache = {}

    async def get_forecast(self, location_code: str) -> Dict[str, Dict[str, Optional[int]]]:
        """
        Gets the forecast for a specific location.
        Returns a dictionary mapping date strings (YYYY-MM-DD) to 'high' and 'low' temps.
        Example: {'2026-02-22': {'high': 75, 'low': 55}, ...}
        """
        if location_code not in LOCATIONS:
            logging.error(f"Unknown location code: {location_code}")
            return {}

        loc = LOCATIONS[location_code]
        forecast_url = await self._get_forecast_url(location_code, loc["lat"], loc["lon"])
        
        if not forecast_url:
            return {}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(forecast_url, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logging.error(f"Failed to fetch NOAA forecast for {location_code} from {forecast_url}: {e}")
            return {}

        periods = data.get("properties", {}).get("periods", [])
        forecasts = {}

        for period in periods:
            start_time = period.get("startTime", "")
            if not start_time:
                continue

            date_str = start_time[:10]
            temp = period.get("temperature")
            is_daytime = period.get("isDaytime", True)

            if date_str not in forecasts:
                forecasts[date_str] = {"high": None, "low": None}

            if is_daytime:
                forecasts[date_str]["high"] = temp
            else:
                forecasts[date_str]["low"] = temp

        return forecasts

    async def _get_forecast_url(self, location_code: str, lat: float, lon: float) -> Optional[str]:
        """Resolves lat/lon to a specific NWS grid forecast URL."""
        if location_code in self._grid_cache:
            return self._grid_cache[location_code]

        points_url = f"{NOAA_API_BASE}/points/{lat},{lon}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(points_url, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
                
            forecast_url = data.get("properties", {}).get("forecast")
            if forecast_url:
                self._grid_cache[location_code] = forecast_url
                return forecast_url
        except Exception as e:
            logging.error(f"Failed to get NOAA grid for {location_code} ({lat}, {lon}): {e}")
            
        return None
