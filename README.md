# LP Sentinel

LP Sentinel is a lightweight, always-on monitor for DeFi liquidity pools. It pulls pool stats from DeFiLlama, publishes risk-tiered LP recommendations to Discord, and continuously health-checks the most recent picks. If any recommended pool degrades, it alerts and immediately posts a refreshed list.

**Monitoring-only:** no wallet access, no trading, no transactions.

---

## Bots

### 🟢 Stable Weekly (Long-Term)
**Goal:** “check once a week” stablecoin pools  
- Only **stable–stable** pairs (e.g., USDC–USDT)
- Weekly posting cadence (interval-based)
- Constant health checks + auto-refresh on failure

### 🟡 Medium-Term
**Goal:** moderate yield with moderate risk  
- Typically **stable–base** and **base–base** pairs
- Posts on a multi-day/weekly interval (configurable)
- Constant health checks + auto-refresh on failure

### 🔴 Short-Term (Active / Daily)
**Goal:** higher yield, higher churn  
- Higher activity pools with faster rotation
- Daily posting cadence (interval-based)
- Constant health checks + auto-refresh on failure

---

## Health Checks (Continuous)

Each bot monitors the **latest posted recommendations** on every scan tick using signals like:
- TVL drop %
- 7d volume drop %
- net APY collapse %
- reported IL (when available)
- absolute liquidity floors (where configured)

**Behavior:** if any current pick fails a check → **alert + refreshed picks are posted immediately** (next scan tick).

---

## Data Source

- **DeFiLlama Pools API** (`https://yields.llama.fi/pools`)

