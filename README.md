LP Sentinel

LP Sentinel is a lightweight monitoring system for DeFi liquidity pools.
It continuously scans pool data, classifies risk, and publishes LP recommendations across different time horizons, with automatic health checks and alerts.

This project is monitoring-only. It does not manage funds, execute trades, or connect to wallets.

What It Does

Scans DeFi liquidity pools using DeFiLlama data

Filters pools by liquidity, volume, yield, and risk profile

Publishes LP recommendations on different cadences

Continuously monitors recommended pools

Alerts and auto-refreshes picks when conditions deteriorate

The focus is risk awareness and time efficiency, not chasing maximum APY.

Bot Tiers

LP Sentinel is split into independent bots, each designed for a different risk tolerance.

🟢 Stable Weekly (Long-Term)

Stable–stable pools only (e.g. USDC–USDT)

No price exposure, no range management

Posts once per week

Continuous health checks

Intended to be checked once weekly

🟡 Medium-Term

Stable–base and base–base pools

Moderate yield, moderate risk

Reviewed every few days

Alerts on meaningful degradation

🔴 Short-Term (Active)

Higher-yield, high-activity pools

Faster scan cadence

Daily recommendations

Aggressive alerts and rotation

Requires active monitoring

Health Monitoring

All bots track active recommendations using:

TVL changes

7-day volume changes

Net APY collapse

Reported impermanent loss (when available)

Absolute liquidity thresholds

If a pool fails health checks:

An alert is posted

New pools are selected automatically

Recommendations are refreshed immediately

Data Source

DeFiLlama — cross-chain aggregated pool data

Discord webhooks for notifications

Design Philosophy

Separate pools by risk category, not just APY

Favor liquidity and usage over incentives

Automate monitoring, not decision-making

Optimize for less time watching dashboards

LP Sentinel is meant to help you stay informed without babysitting pools.
