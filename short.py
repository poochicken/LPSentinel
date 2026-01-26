#!/usr/bin/env python3
"""
LP Sentinel — SHORT TERM (Check Daily)
Higher yield, higher activity, faster rotation.
"""

import json
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

import requests

# ===================== USER CONTROLS =====================

AUTO_DAILY_POST = True
FORCE_POST_NOW = False

DAILY_INTERVAL_HOURS = 24
SCAN_INTERVAL_MIN = 5

# =========================================================

DISCORD_WEBHOOK_URL = "xxxxxxxxx"
POOLS_URL = "https://yields.llama.fi/pools"
STATE_PATH = os.path.join(os.path.dirname(__file__), "state_short_daily.json")

RECOMMEND_N = 5
CHAINS = {"Ethereum", "Arbitrum", "Optimism", "Base", "Solana", "BSC"}

STABLE_HINTS = ("USDC", "USDT", "DAI", "LUSD", "FRAX", "CRVUSD", "USDE")
BASE_HINTS   = ("ETH", "WETH", "BTC", "WBTC", "STETH", "CBETH", "SOL", "BNB")

SHORT_FILTERS = dict(
    min_tvl=30_000_000,
    min_vol7d=20_000_000,
    max_apy=80,
    max_il7d=3.0,
    allowed_types={"stable-base", "base-base"},
)

REWARD_HAIRCUT = 0.20

TANK_RULES = dict(
    max_tvl_drop_pct=20.0,
    max_vol7d_drop_pct=30.0,
    max_il7d=3.5,
    max_net_apy_drop_pct=40.0,
)

DISCORD_MAX_LEN = 1900
TIMEOUT = 20
USER_AGENT = "lp-sentinel-short/1.0"

# ===================== HELPERS =====================

def now_ts() -> int:
    return int(time.time())

def num(x) -> float:
    return x if isinstance(x, (int, float)) else 0.0

def safe_get_json(url: str) -> Dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    for i in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == 2:
                raise
            time.sleep(2)

def fetch_pools() -> List[Dict[str, Any]]:
    return safe_get_json(POOLS_URL).get("data", [])

def post_to_discord(msg: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL not set")
    for i in range(0, len(msg), DISCORD_MAX_LEN):
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": msg[i:i + DISCORD_MAX_LEN]},
            timeout=10,
        )
        r.raise_for_status()
        time.sleep(0.2)

def tokenize(symbol: str) -> str:
    s = (symbol or "").upper()
    for sep in ("-", "/", " ", "_", "+"):
        s = s.replace(sep, "|")
    return s

def classify(symbol: str) -> str:
    s = tokenize(symbol)
    stable = sum(h in s for h in STABLE_HINTS)
    base = sum(b in s for b in BASE_HINTS)
    if stable >= 1 and base >= 1:
        return "stable-base"
    if base >= 2 and stable == 0:
        return "base-base"
    return "other"

def net_apy(p: Dict[str, Any]) -> float:
    base = num(p.get("apyBase"))
    rew = num(p.get("apyReward"))
    if p.get("apyBase") is not None or p.get("apyReward") is not None:
        return base + REWARD_HAIRCUT * rew
    return num(p.get("apy"))

def score_short(p: Dict[str, Any]) -> float:
    return (
        0.7 * net_apy(p)
        + math.log10(max(num(p.get("tvlUsd")), 1))
        + math.log10(max(num(p.get("volumeUsd7d")), 1))
    )

def pool_snapshot(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pool": p.get("pool"),
        "project": p.get("project"),
        "chain": p.get("chain"),
        "symbol": p.get("symbol"),
        "tvlUsd": num(p.get("tvlUsd")),
        "volumeUsd7d": num(p.get("volumeUsd7d")),
        "il7d": p.get("il7d"),
        "netApy": net_apy(p),
        "apy": num(p.get("apy")),
        "ts": now_ts(),
    }

def filter_pool(p: Dict[str, Any]) -> bool:
    if p.get("chain") not in CHAINS:
        return False
    if classify(p.get("symbol", "")) not in SHORT_FILTERS["allowed_types"]:
        return False
    if num(p.get("tvlUsd")) < SHORT_FILTERS["min_tvl"]:
        return False
    if num(p.get("volumeUsd7d")) < SHORT_FILTERS["min_vol7d"]:
        return False
    if num(p.get("apy")) > SHORT_FILTERS["max_apy"]:
        return False
    il7d = p.get("il7d")
    if il7d is not None and float(il7d) > SHORT_FILTERS["max_il7d"]:
        return False
    return True

def pick_short_pools(pools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cands = [p for p in pools if filter_pool(p)]
    cands.sort(key=score_short, reverse=True)
    return cands[:RECOMMEND_N]

def tank_reasons(prev: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    r = []
    if prev["tvlUsd"] > 0:
        d = (prev["tvlUsd"] - cur["tvlUsd"]) / prev["tvlUsd"] * 100
        if d >= TANK_RULES["max_tvl_drop_pct"]:
            r.append(f"TVL ↓ {d:.1f}%")
    if prev["volumeUsd7d"] > 0:
        d = (prev["volumeUsd7d"] - cur["volumeUsd7d"]) / prev["volumeUsd7d"] * 100
        if d >= TANK_RULES["max_vol7d_drop_pct"]:
            r.append(f"Vol7d ↓ {d:.1f}%")
    if cur["il7d"] is not None and float(cur["il7d"]) > TANK_RULES["max_il7d"]:
        r.append(f"il7d {cur['il7d']:.2f}%")
    if prev["netApy"] > 0:
        d = (prev["netApy"] - cur["netApy"]) / prev["netApy"] * 100
        if d >= TANK_RULES["max_net_apy_drop_pct"]:
            r.append(f"net APY ↓ {d:.1f}%")
    return r

def should_post_daily(last_ts: Optional[int]) -> bool:
    if not AUTO_DAILY_POST:
        return False
    if last_ts is None:
        return True
    return (now_ts() - last_ts) >= DAILY_INTERVAL_HOURS * 3600

# ===================== MAIN =====================

def main():
    state = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)

    last_post = state.get("last_post_ts")
    current = state.get("current_recs", [])

    global FORCE_POST_NOW

    while True:
        try:
            pools = fetch_pools()
            idx = {p["pool"]: p for p in pools if p.get("pool")}

            # ---- Daily / forced post ----
            if FORCE_POST_NOW or should_post_daily(last_post):
                picks = pick_short_pools(pools)
                post_to_discord(
                    f"🟡 **SHORT-TERM LP PICKS — {datetime.now().strftime('%Y-%m-%d')}**\n"
                    "Mode: ACTIVE / CHECK DAILY\n\n" +
                    "\n".join(
                        f"• **{p['project']}** | {p['chain']} | `{p['symbol']}`\n"
                        f"  Net~{net_apy(p):.2f}% | TVL ${num(p.get('tvlUsd')):,.0f} | "
                        f"Vol7d ${num(p.get('volumeUsd7d')):,.0f}"
                        for p in picks
                    )
                )
                current = [pool_snapshot(p) for p in picks]
                last_post = now_ts()
                FORCE_POST_NOW = False

            # ---- Continuous health checks ----
            bad = []
            refreshed = []

            for prev in current:
                pid = prev["pool"]
                if pid not in idx:
                    continue
                cur = pool_snapshot(idx[pid])
                refreshed.append(cur)
                reasons = tank_reasons(prev, cur)
                if reasons:
                    bad.append((cur, reasons))

            if bad:
                post_to_discord(
                    "🔴 **SHORT-TERM ALERT**\n\n" +
                    "\n".join(
                        f"• **{s['project']}** | `{s['symbol']}` → {', '.join(r)}"
                        for s, r in bad
                    )
                )
                picks = pick_short_pools(pools)
                post_to_discord(
                    "🟡 **AUTO-REFRESHED SHORT PICKS**\n\n" +
                    "\n".join(
                        f"• **{p['project']}** | `{p['symbol']}`"
                        for p in picks
                    )
                )
                current = [pool_snapshot(p) for p in picks]
            else:
                current = refreshed or current

            state = {
                "last_post_ts": last_post,
                "current_recs": current,
            }
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)

        except Exception as e:
            try:
                post_to_discord(f"⚠️ Short bot error: `{type(e).__name__}: {str(e)[:160]}`")
            except Exception:
                pass

        time.sleep(SCAN_INTERVAL_MIN * 60)

if __name__ == "__main__":
    main()
