#!/usr/bin/env python3
# LP Sentinel — continuous pool health + weekly safe picks

import json
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests

# ===================== USER CONTROLS =====================

AUTO_WEEKLY_POST = True          # enable automatic weekly post
FORCE_POST_NOW = True           # flip True to force an immediate post
WEEKLY_INTERVAL_DAYS = 2

SCAN_INTERVAL_MIN = 20

# =========================================================

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1465227282997317695/BTTH9Btv6_HUElVjSqFwRxaTI9lYLlLQHULdekuqWkLwfmKxK65Uqlc3AEFTVyuhK6iy"
POOLS_URL = "https://yields.llama.fi/pools"

STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")

RECOMMEND_N = 5
CHAINS = {"Ethereum", "Arbitrum", "Optimism", "Base", "Solana", "BSC"}

STABLE_HINTS = ("USDC", "USDT", "DAI", "LUSD", "FRAX", "CRVUSD", "PYUSD", "USDE")
BASE_HINTS   = ("ETH", "WETH", "BTC", "WBTC", "STETH", "CBETH", "SOL", "BNB")

PRIMARY_FILTERS = dict(
    min_tvl=100_000_000,
    min_vol7d=25_000_000,
    max_apy=30,
    max_il7d=1.0,
    allowed_types={"stable-stable", "base-base"},
)

FALLBACK_FILTERS = dict(
    min_tvl=75_000_000,
    min_vol7d=15_000_000,
    max_apy=35,
    max_il7d=1.5,
    allowed_types={"stable-stable", "base-base", "stable-base"},
)

REWARD_HAIRCUT = 0.25

TANK_RULES = dict(
    max_tvl_drop_pct=25.0,
    max_vol7d_drop_pct=40.0,
    max_il7d=2.0,
    max_net_apy_drop_pct=50.0,
)

DISCORD_MAX_LEN = 1900
TIMEOUT = 20
USER_AGENT = "lp-sentinel/1.1"

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
    for i in range(0, len(msg), DISCORD_MAX_LEN):
        chunk = msg[i:i + DISCORD_MAX_LEN]
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk}, timeout=10)
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
    if stable >= 2 and base == 0:
        return "stable-stable"
    if stable >= 1 and base >= 1:
        return "stable-base"
    if base >= 2 and stable == 0:
        return "base-base"
    return "exotic"

def net_apy(p: Dict[str, Any]) -> float:
    base = num(p.get("apyBase"))
    rew = num(p.get("apyReward"))
    if p.get("apyBase") is not None or p.get("apyReward") is not None:
        return base + REWARD_HAIRCUT * rew
    return num(p.get("apy"))

def score(p: Dict[str, Any]) -> float:
    return (
        0.6 * net_apy(p)
        + math.log10(max(num(p.get("tvlUsd")), 1))
        + math.log10(max(num(p.get("volumeUsd7d")), 1))
    )

def pool_snapshot(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pool": p.get("pool"),
        "chain": p.get("chain"),
        "project": p.get("project"),
        "symbol": p.get("symbol"),
        "tvlUsd": num(p.get("tvlUsd")),
        "volumeUsd7d": num(p.get("volumeUsd7d")),
        "il7d": p.get("il7d"),
        "netApy": net_apy(p),
        "apy": num(p.get("apy")),
        "ts": now_ts(),
    }

def filter_pool(p: Dict[str, Any], rules: Dict[str, Any]) -> bool:
    if p.get("chain") not in CHAINS:
        return False
    if num(p.get("tvlUsd")) < rules["min_tvl"]:
        return False
    if num(p.get("volumeUsd7d")) < rules["min_vol7d"]:
        return False
    if num(p.get("apy")) > rules["max_apy"]:
        return False
    if p.get("il7d") is not None and float(p.get("il7d")) > rules["max_il7d"]:
        return False
    if classify(p.get("symbol", "")) not in rules["allowed_types"]:
        return False
    return True

def pick_pools(pools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    primary = [p for p in pools if filter_pool(p, PRIMARY_FILTERS)]
    fallback = [p for p in pools if filter_pool(p, FALLBACK_FILTERS)]
    primary.sort(key=score, reverse=True)
    fallback.sort(key=score, reverse=True)
    picks = primary[:]
    if len(picks) < RECOMMEND_N:
        picks.extend(fallback[: RECOMMEND_N - len(picks)])
    return picks[:RECOMMEND_N]

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

def format_picks(picks: List[Dict[str, Any]], title: str) -> str:
    lines = [f"🟡 **{title} — {datetime.now().strftime('%Y-%m-%d')}**", ""]
    for p in picks:
        s = pool_snapshot(p)
        lines.append(
            f"• **{s['project']}** | {s['chain']} | `{s['symbol']}`\n"
            f"  Net~{s['netApy']:.2f}% | TVL ${s['tvlUsd']:,.0f} | "
            f"Vol7d ${s['volumeUsd7d']:,.0f} | il7d {s['il7d'] or 'n/a'}"
        )
    return "\n".join(lines)

def format_alert(bad: List[Tuple[Dict[str, Any], List[str]]]) -> str:
    lines = [f"🔴 **LP ALERT — {datetime.now().strftime('%Y-%m-%d %H:%M')}**", ""]
    for s, r in bad:
        lines.append(
            f"• **{s['project']}** | `{s['symbol']}`\n"
            f"  Reasons: {', '.join(r)}"
        )
    return "\n".join(lines)

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH) as f:
        return json.load(f)

def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)

def should_post_weekly(last_ts: int | None) -> bool:
    if not AUTO_WEEKLY_POST:
        return False
    if last_ts is None:
        return True
    return (now_ts() - last_ts) >= WEEKLY_INTERVAL_DAYS * 86400

# ===================== MAIN =====================

def main():
    state = load_state()
    last_post = state.get("last_weekly_post_ts")
    current = state.get("current_recs", [])

    global FORCE_POST_NOW

    while True:
        try:
            pools = fetch_pools()
            idx = {p["pool"]: p for p in pools if p.get("pool")}

            # ---- Weekly / manual post ----
            if FORCE_POST_NOW or should_post_weekly(last_post):
                picks = pick_pools(pools)
                post_to_discord(format_picks(picks, "MID-TERM SAFE LP PICKS(EVERY FEW DAYS)"))
                current = [pool_snapshot(p) for p in picks]
                last_post = now_ts()
                FORCE_POST_NOW = False
                state.update({
                    "last_weekly_post_ts": last_post,
                    "current_recs": current,
                })
                save_state(state)

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
                post_to_discord(format_alert(bad))
                picks = pick_pools(pools)
                post_to_discord(format_picks(picks, "AUTO-REFRESHED PICKS (FAILURE)"))
                current = [pool_snapshot(p) for p in picks]

            state["current_recs"] = current
            save_state(state)

        except Exception as e:
            try:
                post_to_discord(f"⚠️ Sentinel error: `{type(e).__name__}: {str(e)[:160]}`")
            except Exception:
                pass

        time.sleep(SCAN_INTERVAL_MIN * 60)

if __name__ == "__main__":
    main()


