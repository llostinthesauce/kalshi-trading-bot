import pytest
import asyncio
from src.utils.noaa_client import NOAAClient

@pytest.mark.asyncio
async def test_noaa_client_nyc():
    """Verify NOAAClient can fetch a forecast for NYC."""
    client = NOAAClient()
    
    # NYC
    forecasts = await client.get_forecast("NYC")
    
    # Assert we got some data back (NOAA usually returns ~7 days of forecasts)
    assert len(forecasts) > 0, "Should have returned forecast days"
    
    # Check that today or tomorrow has a valid structure
    # The keys are YYYY-MM-DD strings
    first_date = list(forecasts.keys())[0]
    first_day_forecast = forecasts[first_date]
    
    assert "high" in first_day_forecast
    assert "low" in first_day_forecast
    
    # It's possible the 'high' for the very first day is None if the day has already passed
    # but the structure should be there.

@pytest.mark.asyncio
async def test_noaa_client_invalid_location():
    """Verify NOAAClient handles invalid locations gracefully."""
    client = NOAAClient()
    forecasts = await client.get_forecast("INVALID_CITY")
    assert forecasts == {}
