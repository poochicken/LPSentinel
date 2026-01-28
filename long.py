#!/usr/bin/env python3
"""
LP Sentinel (Stable Weekly)
- Recommends ONLY stable-stable pools (USDC/USDT/DAI/etc).
- Posts weekly (interval-based, no time window).
- Constant health checks; if a posted pool degrades -> alert + auto-refresh picks.
"""

import json
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

import requests

# ===================== USER CONTROLS =====================

# If True: auto-post every WEEKLY_INTERVAL_DAYS.
AUTO_WEEKLY_POST = True

# Set True to force a post on the next loop tick; it resets to False after posting.
FORCE_POST_NOW = False

# Weekly cadence (days)
WEEKLY_INTERVAL_DAYS = 7

# Scan cadence (minutes). Health checks run every scan.
SCAN_INTERVAL_MIN = 20

# =========================================================

# IMPORTANT: Put webhook in an env var to avoid leaking it.
# In Terminal:
#   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/...."
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1465233466630344868/4KtMSExOnfM7jLBCNI5D3zDqNO3WsXn8UjD1YwP5NnVXdWnsoQuWhXiO0nk2xB2UGdQL"

# Universe source
POOLS_URL = "https://yields.llama.fi/pools"

# State file
STATE_PATH = os.path.join(os.path.dirname(__file__), "state_stable_weekly.json")

# How many pools to recommend
RECOMMEND_N = 5

# Chains to include (edit as you like)
CHAINS = {"Ethereum", "Arbitrum", "Optimism", "Base", "Solana", "BSC"}

# Stable hints (used to classify pool symbol text)
STABLE_HINTS = (
    "USDC", "USDT", "DAI", "LUSD", "FRAX", "CRVUSD", "PYUSD", "USDE",
    "USD0", "USDB", "GUSD", "TUSD", "SUSD",
)

# ----- Stable-only selection filters -----
# These are intentionally "boring-friendly":
# - lower min_vol7d than your medium bot
# - APY cap modest (stable pools shouldn't be insane)
STABLE_FILTERS = dict(
    min_tvl=50_000_000,     # stable pools can be safe with lower TVL than volatile pools
    min_vol7d=3_000_000,    # stable-stable volume is often modest
    max_apy=20,             # if it's claiming 80% on stables, assume it's sketchy/noisy
    max_il7d=0.5,           # stable-stable IL should be near 0 when reported
    allowed_types={"stable-stable"},
)

# Reward haircut to compute "net" APY when rewards exist
REWARD_HAIRCUT = 0.25

# ----- Tank detection thresholds (stable pools should be calmer) -----
TANK_RULES = dict(
    max_tvl_drop_pct=20.0,       # stable pools shouldn't see huge TVL cliff without reason
    max_vol7d_drop_pct=50.0,     # volume can fluctuate; allow more variance
    max_il7d=1.0,                # stable IL should not spike
    max_net_apy_drop_pct=60.0,   # incentives can end; still worth alerting
    min_tvl_absolute=20_000_000, # if TVL falls under this, alert regardless
)

# Discord payload limit
DISCORD_MAX_LEN = 1900

# Requests
TIMEOUT = 20
USER_AGENT = "lp-sentinel-stable/1.0"

# ===================== HELPERS =====================

def now_ts() -> int:
    return int(time.time())

def now_str(detailed: bool = False) -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M" if detailed else "%Y-%m-%d")

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
            time.sleep(2 * (i + 1))

def fetch_pools() -> List[Dict[str, Any]]:
    return safe_get_json(POOLS_URL).get("data", [])

def post_to_discord(msg: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL env var is not set.")

    for i in range(0, len(msg), DISCORD_MAX_LEN):
        chunk = msg[i:i + DISCORD_MAX_LEN]
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk}, timeout=10)
        if r.status_code >= 400:
            raise RuntimeError(f"Discord error {r.status_code}: {r.text[:300]}")
        time.sleep(0.2)

def tokenize(symbol: str) -> str:
    s = (symbol or "").upper()
    for sep in ("-", "/", " ", "_", "+"):
        s = s.replace(sep, "|")
    return s

def classify(symbol: str) -> str:
    """
    Stable-stable classifier: if symbol contains 2+ stable hints -> stable-stable.
    This is intentionally strict for "check once a week" mode.
    """
    s = tokenize(symbol)
    stable = sum(h in s for h in STABLE_HINTS)
    if stable >= 2:
        return "stable-stable"
    return "other"

def net_apy(p: Dict[str, Any]) -> float:
    base = num(p.get("apyBase"))
    rew = num(p.get("apyReward"))
    if p.get("apyBase") is not None or p.get("apyReward") is not None:
        return base + REWARD_HAIRCUT * rew
    return num(p.get("apy"))

def score_stable(p: Dict[str, Any]) -> float:
    """
    Conservative stable score:
    - net APY matters, but not too much
    - prefer deep liquidity and real usage
    """
    tvl = max(num(p.get("tvlUsd")), 1.0)
    vol7d = max(num(p.get("volumeUsd7d")), 1.0)
    net = net_apy(p)
    return (0.5 * net) + math.log10(tvl) + 0.7 * math.log10(vol7d)

def pool_snapshot(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pool": p.get("pool"),
        "chain": p.get("chain"),
        "project": p.get("project"),
        "symbol": p.get("symbol"),
        "tvlUsd": num(p.get("tvlUsd")),
        "volumeUsd7d": num(p.get("volumeUsd7d")),
        "il7d": p.get("il7d"),  # may be None
        "netApy": net_apy(p),
        "apy": num(p.get("apy")),
        "ts": now_ts(),
    }

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r") as f:
        return json.load(f)

def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)

def should_post_weekly(last_ts: Optional[int]) -> bool:
    if not AUTO_WEEKLY_POST:
        return False
    if last_ts is None:
        return True
    return (now_ts() - last_ts) >= WEEKLY_INTERVAL_DAYS * 86400

def filter_pool(p: Dict[str, Any], rules: Dict[str, Any]) -> bool:
    if p.get("chain") not in CHAINS:
        return False
    if classify(p.get("symbol", "")) not in rules["allowed_types"]:
        return False
    if num(p.get("tvlUsd")) < rules["min_tvl"]:
        return False
    if num(p.get("volumeUsd7d")) < rules["min_vol7d"]:
        return False
    if num(p.get("apy")) > rules["max_apy"]:
        return False
    il7d = p.get("il7d")
    if il7d is not None and float(il7d) > rules["max_il7d"]:
        return False
    return True

def pick_stable_pools(pools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cands = [p for p in pools if filter_pool(p, STABLE_FILTERS)]
    cands.sort(key=score_stable, reverse=True)
    return cands[:RECOMMEND_N]

def tank_reasons(prev: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    reasons = []

    prev_tvl = float(prev.get("tvlUsd", 0))
    cur_tvl = float(cur.get("tvlUsd", 0))

    # absolute TVL floor
    if cur_tvl > 0 and cur_tvl < TANK_RULES["min_tvl_absolute"]:
        reasons.append(f"TVL low (${cur_tvl:,.0f} < ${TANK_RULES['min_tvl_absolute']:,.0f})")

    # TVL drop
    if prev_tvl > 0:
        drop = (prev_tvl - cur_tvl) / prev_tvl * 100.0
        if drop >= TANK_RULES["max_tvl_drop_pct"]:
            reasons.append(f"TVL ↓ {drop:.1f}%")

    # Vol7d drop
    prev_vol = float(prev.get("volumeUsd7d", 0))
    cur_vol = float(cur.get("volumeUsd7d", 0))
    if prev_vol > 0:
        drop = (prev_vol - cur_vol) / prev_vol * 100.0
        if drop >= TANK_RULES["max_vol7d_drop_pct"]:
            reasons.append(f"Vol7d ↓ {drop:.1f}%")

    # IL spike
    cur_il = cur.get("il7d")
    if cur_il is not None and float(cur_il) > TANK_RULES["max_il7d"]:
        reasons.append(f"il7d {float(cur_il):.2f}%")

    # Net APY collapse
    prev_net = float(prev.get("netApy", 0))
    cur_net = float(cur.get("netApy", 0))
    if prev_net > 0:
        drop = (prev_net - cur_net) / prev_net * 100.0
        if drop >= TANK_RULES["max_net_apy_drop_pct"]:
            reasons.append(f"net APY ↓ {drop:.1f}%")

    return reasons

def build_pool_index(pools: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    idx = {}
    for p in pools:
        pid = p.get("pool")
        if pid:
            idx[pid] = p
    return idx

def format_weekly_message(picks: List[Dict[str, Any]], label: str) -> str:
    lines = [
        f"🟢 **{label} — {now_str()}**",
        f"Mode: STABLE-STABLE ONLY | Universe: DeFiLlama pools",
        f"Chains: {', '.join(sorted(CHAINS))}",
        "",
    ]
    for p in picks:
        s = pool_snapshot(p)
        lines.append(
            f"• **{s['project']}** | {s['chain']} | `{s['symbol']}`\n"
            f"  Net~{s['netApy']:.2f}% | APY:{s['apy']:.2f}% | "
            f"TVL:${s['tvlUsd']:,.0f} | Vol7d:${s['volumeUsd7d']:,.0f} | il7d:{s['il7d'] if s['il7d'] is not None else 'n/a'}"
        )
    return "\n".join(lines)

def format_tank_alert(bad: List[Tuple[Dict[str, Any], List[str]]]) -> str:
    lines = [
        f"🔴 **STABLE LP ALERT — {now_str(detailed=True)}**",
        "One or more weekly stable picks degraded:",
        "",
    ]
    for snap, reasons in bad:
        lines.append(
            f"• **{snap['project']}** | {snap['chain']} | `{snap['symbol']}`\n"
            f"  Reasons: {', '.join(reasons)}\n"
            f"  Now: TVL ${snap['tvlUsd']:,.0f} | Vol7d ${snap['volumeUsd7d']:,.0f} | net~{snap['netApy']:.2f}% | il7d:{snap['il7d'] if snap['il7d'] is not None else 'n/a'}"
        )
    return "\n".join(lines)

# ===================== MAIN LOOP =====================

def main():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError(
            "Set DISCORD_WEBHOOK_URL as an environment variable.\n"
            "Example: export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'\n"
        )

    state = load_state()
    last_weekly_ts = state.get("last_weekly_post_ts")  # int
    current_recs = state.get("current_recs", [])       # list of snapshots

    global FORCE_POST_NOW

    while True:
        try:
            pools = fetch_pools()
            idx = build_pool_index(pools)

            # ----- Weekly or forced post -----
            if FORCE_POST_NOW or should_post_weekly(last_weekly_ts):
                picks = pick_stable_pools(pools)
                if not picks:
                    post_to_discord(
                        f"⚠️ **STABLE WEEKLY** — {now_str()}\n"
                        "No pools matched stable-only filters. Consider lowering min_tvl/min_vol7d."
                    )
                else:
                    post_to_discord(format_weekly_message(picks, "WEEKLY STABLE LP PICKS (CHECK ONCE/WEEK)"))

                    current_recs = [pool_snapshot(p) for p in picks]
                    last_weekly_ts = now_ts()
                    FORCE_POST_NOW = False

                    state["last_weekly_post_ts"] = last_weekly_ts
                    state["current_recs"] = current_recs
                    save_state(state)

            # ----- Constant health checks on last posted recs -----
            bad: List[Tuple[Dict[str, Any], List[str]]] = []
            refreshed: List[Dict[str, Any]] = []

            for prev in current_recs:
                pid = prev.get("pool")
                if not pid or pid not in idx:
                    continue
                cur_snap = pool_snapshot(idx[pid])
                refreshed.append(cur_snap)
                reasons = tank_reasons(prev, cur_snap)
                if reasons:
                    bad.append((cur_snap, reasons))

            if bad:
                post_to_discord(format_tank_alert(bad))

                # Auto-refresh and re-post new stable picks immediately
                picks = pick_stable_pools(pools)
                if picks:
                    post_to_discord(format_weekly_message(picks, "AUTO-REFRESHED STABLE PICKS (AFTER ALERT)"))
                    current_recs = [pool_snapshot(p) for p in picks]
                else:
                    post_to_discord("⚠️ Auto-refresh found no stable pools matching filters.")

            else:
                # keep state snapshots up to date even if healthy
                current_recs = refreshed or current_recs

            state["current_recs"] = current_recs
            save_state(state)

        except Exception as e:
            # Minimal failure notice
            try:
                post_to_discord(f"⚠️ Stable Sentinel error: `{type(e).__name__}: {str(e)[:180]}`")
            except Exception:
                pass

        time.sleep(SCAN_INTERVAL_MIN * 60)

if __name__ == "__main__":
    main()
