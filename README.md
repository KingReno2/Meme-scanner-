# Meme Scanner (alert-only)

Scans Solana for early tokens matching King's filter criteria and sends ranked
alerts to Telegram. **Never buys or sells anything — you copy the CA into
Trojan yourself.**

## What it checks
- Market cap: $35k–$500k
- Liquidity: ≥ $5k
- Age: 1–48 hours old
- Rug signals via RugCheck.xyz: mint authority, freeze authority, top-10
  holder concentration
- Volume/liquidity ratio as an organic-activity signal

Everything is scored 0–100. Only tokens scoring ≥ 60 (edit `MIN_SCORE_TO_ALERT`
in `scanner.py` to tune this) get sent to Telegram.

## Setup (10 minutes)

1. **Create a GitHub repo** and push these files to it (same pattern as
   KingTipx — new repo, e.g. `meme-scanner`).

2. **Add your secrets** — go to the repo's `Settings → Secrets and variables
   → Actions → New repository secret`, and add three secrets:
   - `HELIUS_API_KEY` — your Helius key
   - `TG_BOT_TOKEN` — your Telegram bot token
   - `TG_CHAT_ID` — your Telegram chat ID (`8175187384`)

   Do **not** paste these into the code files — the workflow reads them from
   GitHub Secrets, which are encrypted and never shown in logs.

3. **Enable Actions** if prompted (repo → Actions tab → "I understand, enable").

4. That's it. It'll run automatically every 10 minutes. You can also trigger
   a manual run any time from the Actions tab → "Meme Scanner" → "Run workflow".

## Tuning it after you see real alerts
- Too many low-quality alerts → raise `MIN_SCORE_TO_ALERT` (try 70)
- Too few alerts → lower it, or widen `MIN_AGE_HOURS`/`MAX_AGE_HOURS`
- Want tighter mcap range → edit `MIN_MCAP` / `MAX_MCAP` at the top of
  `scanner.py`

## Known limitations (v1)
- Token discovery relies on DexScreener's public "trending/boosted" feeds —
  this catches tokens already gaining attention, not the very first minute
  of a brand-new launch. That's intentional (matches the "survived past the
  rug window" band we discussed), but it means ultra-early snipes won't show
  up here.
- RugCheck.xyz data isn't available for every token — if it's missing, the
  scanner penalizes the score rather than blocking the alert outright, since
  a real signal being absent isn't the same as a token being unsafe.
- No wallet-quality/smart-money layer yet — that's the natural next add-on
  once this baseline is running and you've seen how the alerts perform.

## Regenerating your bot token
Since the token was shared in chat, you can invalidate it any time via
BotFather → `/revoke` → pick your bot → get a new token → update the
`TG_BOT_TOKEN` secret in GitHub. Takes under a minute.
