"""
Meme Scanner — Solana early-token alert bot
Finds tokens matching King's filter criteria and pushes ranked alerts to Telegram.
ALERT ONLY — never buys or sells anything automatically.
"""

import os
import json
import time
import requests
from datetime import datetime, timezone

# ---------- CONFIG (edit these to tune the filter) ----------
MIN_MCAP = 35_000
MAX_MCAP = 50_000
MIN_LIQUIDITY = 5_000
MIN_AGE_HOURS = 1
MAX_AGE_HOURS = 48
MIN_SCORE_TO_ALERT = 60  # 0-100 scale, tune after seeing real results
SENT_TOKENS_FILE = "data/sent_tokens.json"

# ---------- SECRETS (from environment / GitHub Actions secrets) ----------
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

DEXSCREENER_BASE = "https://api.dexscreener.com"
RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"


def load_sent_tokens():
    if os.path.exists(SENT_TOKENS_FILE):
        with open(SENT_TOKENS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_tokens(sent):
    os.makedirs(os.path.dirname(SENT_TOKENS_FILE), exist_ok=True)
    # keep the file from growing forever — cap at last 2000 entries
    trimmed = list(sent)[-2000:]
    with open(SENT_TOKENS_FILE, "w") as f:
        json.dump(trimmed, f)


def get_candidate_addresses():
    """Pull fresh Solana token addresses from DexScreener's public discovery endpoints."""
    addresses = set()
    for endpoint in ["token-profiles/latest/v1", "token-boosts/latest/v1"]:
        try:
            r = requests.get(f"{DEXSCREENER_BASE}/{endpoint}", timeout=15)
            r.raise_for_status()
            data = r.json()
            for item in data:
                if item.get("chainId") == "solana" and item.get("tokenAddress"):
                    addresses.add(item["tokenAddress"])
        except Exception as e:
            print(f"[warn] failed to fetch {endpoint}: {e}")
    return addresses


def get_pair_data(token_address):
    """Get liquidity, mcap, volume, age for a token from DexScreener."""
    try:
        r = requests.get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{token_address}", timeout=15)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs") or []
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return None
        # use the highest-liquidity pair for this token
        best = max(sol_pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0)
        return best
    except Exception as e:
        print(f"[warn] failed pair data for {token_address}: {e}")
        return None


def get_rugcheck_report(token_address):
    """Pull mint/freeze authority + holder concentration + risk score."""
    try:
        r = requests.get(f"{RUGCHECK_BASE}/tokens/{token_address}/report", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        print(f"[warn] rugcheck failed for {token_address}: {e}")
        return None


def score_token(pair, rug_report):
    """Return (score 0-100, reasons dict, hard_fail bool)."""
    reasons = {}
    hard_fail = False

    liquidity = (pair.get("liquidity") or {}).get("usd", 0) or 0
    real_mcap = pair.get("marketCap")
    fdv = pair.get("fdv")
    used_fdv_fallback = not real_mcap and fdv
    mcap = real_mcap or fdv or 0
    if used_fdv_fallback:
        reasons["mcap_is_fdv_fallback"] = True
    volume24 = (pair.get("volume") or {}).get("h24", 0) or 0
    created_ms = pair.get("pairCreatedAt")
    age_hours = None
    if created_ms:
        age_hours = (time.time() * 1000 - created_ms) / 3_600_000

    # --- hard filters ---
    if liquidity < MIN_LIQUIDITY:
        hard_fail = True
        reasons["liquidity_fail"] = f"${liquidity:,.0f} < ${MIN_LIQUIDITY:,.0f}"
    if not (MIN_MCAP <= mcap <= MAX_MCAP):
        hard_fail = True
        reasons["mcap_fail"] = f"${mcap:,.0f} outside ${MIN_MCAP:,.0f}-${MAX_MCAP:,.0f}"
    if age_hours is not None and not (MIN_AGE_HOURS <= age_hours <= MAX_AGE_HOURS):
        hard_fail = True
        reasons["age_fail"] = f"{age_hours:.1f}h outside {MIN_AGE_HOURS}-{MAX_AGE_HOURS}h window"

    if hard_fail:
        return 0, reasons, True

    score = 0

    # liquidity strength (0-20)
    liq_score = min(20, (liquidity / MIN_LIQUIDITY) * 5)
    score += liq_score

    # mcap position — reward lower end of range (more upside room) (0-15)
    mcap_position = 1 - ((mcap - MIN_MCAP) / (MAX_MCAP - MIN_MCAP))
    mcap_score = mcap_position * 15
    if used_fdv_fallback:
        mcap_score *= 0.5  # FDV is a weaker signal than real circulating mcap
    score += mcap_score

    # volume/liquidity ratio — organic trading activity (0-20)
    if liquidity > 0:
        vol_liq_ratio = volume24 / liquidity
        score += min(20, vol_liq_ratio * 10)

    # rug check signals (0-30)
    if rug_report:
        risk_score = rug_report.get("score", 50)  # rugcheck: lower = safer
        rug_component = max(0, 30 - (risk_score / 100) * 30)
        score += rug_component

        mint_auth = rug_report.get("mintAuthority")
        freeze_auth = rug_report.get("freezeAuthority")
        if mint_auth is None:
            reasons["mint_renounced"] = True
        else:
            score -= 10
            reasons["mint_authority_active"] = "WARNING: dev can mint more supply"
        if freeze_auth is None:
            reasons["freeze_disabled"] = True
        else:
            score -= 10
            reasons["freeze_authority_active"] = "WARNING: dev can freeze wallets"

        top_holders = rug_report.get("topHolders", [])
        if top_holders:
            top10_pct = sum(h.get("pct", 0) for h in top_holders[:10])
            reasons["top10_holder_pct"] = round(top10_pct, 1)
            if top10_pct > 50:
                score -= 15
                reasons["concentration_warning"] = "top 10 wallets hold >50% supply"
    else:
        reasons["rugcheck_unavailable"] = True
        score -= 10  # no data = penalize, can't verify safety

    score = max(0, min(100, score))
    reasons["liquidity_usd"] = round(liquidity)
    reasons["mcap_usd"] = round(mcap)
    reasons["volume24h_usd"] = round(volume24)
    reasons["age_hours"] = round(age_hours, 1) if age_hours else None

    return round(score), reasons, False


def format_alert(token_address, pair, score, reasons):
    name = pair.get("baseToken", {}).get("name", "Unknown")
    symbol = pair.get("baseToken", {}).get("symbol", "?")
    url = pair.get("url", f"https://dexscreener.com/solana/{token_address}")

    flags = []
    if reasons.get("mint_authority_active"):
        flags.append("⚠️ mint authority active")
    if reasons.get("freeze_authority_active"):
        flags.append("⚠️ freeze authority active")
    if reasons.get("concentration_warning"):
        flags.append("⚠️ top10 holders >50%")
    if reasons.get("rugcheck_unavailable"):
        flags.append("⚠️ rugcheck data unavailable")
    if reasons.get("mcap_is_fdv_fallback"):
        flags.append("⚠️ mcap shown is FDV (fully diluted), not circulating — real mcap unavailable, treat this number as an upper bound, not what's actually trading")
    flags_text = "\n".join(flags) if flags else "✅ no red flags found"

    msg = (
        f"🎯 *Score: {score}/100*\n"
        f"{name} ({symbol})\n\n"
        f"`{token_address}`\n"
        f"_(tap to copy into Trojan)_\n\n"
        f"💧 Liquidity: ${reasons.get('liquidity_usd', 0):,}\n"
        f"📊 Market Cap: ${reasons.get('mcap_usd', 0):,}\n"
        f"📈 24h Volume: ${reasons.get('volume24h_usd', 0):,}\n"
        f"🕐 Age: {reasons.get('age_hours', '?')}h\n"
        f"👥 Top 10 hold: {reasons.get('top10_holder_pct', '?')}%\n\n"
        f"{flags_text}\n\n"
        f"[View on DexScreener]({url})"
    )
    return msg


def send_telegram_alert(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("[error] missing Telegram credentials, skipping send")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[error] telegram send failed: {r.text}")
    except Exception as e:
        print(f"[error] telegram send exception: {e}")


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting scan...")
    sent = load_sent_tokens()
    candidates = get_candidate_addresses()
    print(f"Found {len(candidates)} candidate addresses")

    alerts_sent = 0
    for addr in candidates:
        if addr in sent:
            continue

        pair = get_pair_data(addr)
        if not pair:
            continue

        rug_report = get_rugcheck_report(addr)
        score, reasons, hard_fail = score_token(pair, rug_report)

        if hard_fail:
            continue

        if score >= MIN_SCORE_TO_ALERT:
            msg = format_alert(addr, pair, score, reasons)
            send_telegram_alert(msg)
            alerts_sent += 1
            print(f"[alert] {addr} scored {score}")

        sent.add(addr)
        time.sleep(0.5)  # be polite to free APIs

    save_sent_tokens(sent)
    print(f"Scan complete. {alerts_sent} alerts sent, {len(candidates)} candidates checked.")


if __name__ == "__main__":
    main()
    
