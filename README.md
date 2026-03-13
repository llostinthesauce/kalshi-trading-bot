# Kalshi Trading Bot 🚀

A high-conviction, automated trading system for Kalshi prediction markets.

## 🛠 Project Scope
The project has been refactored for **reliability and transparency**. It moves away from over-engineered multi-agent systems to a focused, single-loop execution engine that prioritizes statistical edge and capital preservation.

### Core Capabilities:
*   **Real-Time Analysis:** Automated web-research and probability estimation via Anthropic/xAI (for BTC volatility) and deterministic NOAA API targeting (for weather markets).
*   **Two-Pronged Execution:** Run the statistical analysis code (`main.py`) alongside the deterministic NOAA matching code (`weather_main.py`).
*   **Modular Architecture:** Seamlessly toggle between **Paper Trading** (virtual $1,000 balance) and **Live Trading** via `.env`.
*   **Capital Guardrails:** Automated exposure limits and risk caps (default $1 per trade) prevent portfolio over-extension.
*   **Live Monitoring:** A terminal-based output providing real-time PnL tracking, reasoning, and automated position scaling.

---

## 📈 Active Strategies

### 1. BTC Volatility AI Strategy (`main.py`)
*   **Focus:** Identifies short-term BTC volatility movements on Kalshi's `KXBTC` series.
*   **Logic:** Anthropics/xAI evaluates the likelihood of the market closing within its bounds. The bot calculates expected value (EV) and required edge before buying heavily-discounted or premium contracts.
*   **Trigger:** Executes if the model calculates an **Edge ≥ 8%**. Includes stop-loss and time-decay algorithms.

### 2. Kalshi Weather Deterministic Strategy (`weather_main.py`)
*   **Focus:** Predicts daily high temperatures for 6 major US cities using the National Weather Service (NOAA).
*   **Logic:** Matches physical grid points to the experimental Kalshi `KXHIGHT` and `KXHIGH` series.
*   **Trigger:** Buys any matching contract if the exact NOAA forecast falls gracefully inside the Kalshi bucket and the contract is priced **<15¢**. Triggers auto-sells at **≥45¢**.

---

## 🏗 System Architecture
```
kalshi-ai-trading-bot/
├── main.py               # AI BTC Volatility Execution Loop
├── weather_main.py       # Deterministic NOAA Weather Loop
├── dashboard.py          # Legacy Live portfolio monitoring
├── .env                  # API keys and Risk Configuration
├── src/
│   ├── clients/          # Kalshi (RSA signing), NOAA, and XAI/Anthropic clients
│   ├── strategies/       # Weather matcher, position sizing algorithms
│   └── utils/            # SQLite Database & Structured Logging
└── logs/                 # Cycle-by-cycle activity logs
```

---

## 🚦 How to Run

1.  **Configure:**
    *   Initialize `.env`: `cp env.template .env`
    *   Add your Kalshi keys to `.env`. Turn `LIVE_TRADING_ENABLED=true` to switch off paper logging mode.
2.  **Start BTC Bot:** `python3 main.py`
3.  **Start Weather Bot (Open in a second terminal tab):** `python3 weather_main.py`

*Note: The bots run in background mode and maintain state independently in `trading_system.db`.*

