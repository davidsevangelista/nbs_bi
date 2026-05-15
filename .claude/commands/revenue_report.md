Generate a consolidated NBS P&L revenue report for the given time period.

## Usage
```
/revenue_report <start_date> <end_date>
/revenue_report last30
/revenue_report <YYYY-MM>        # full calendar month
```

**Examples:**
- `/revenue_report 2026-04-01 2026-04-30`
- `/revenue_report last30`
- `/revenue_report 2026-04`

## Instructions

Parse `$ARGUMENTS` to determine `START` and `END` dates (ISO strings, e.g. `"2026-04-01"` and `"2026-04-30"`):
- If `$ARGUMENTS` is `last30`: `END` = today, `START` = today minus 30 days
- If `$ARGUMENTS` matches `YYYY-MM` (single month): `START` = first day of that month, `END` = last day of that month
- If `$ARGUMENTS` has two dates separated by a space: use them directly as `START` and `END`

Then run the following Python script (via Bash) from the repo root, substituting `START` and `END`:

```python
import sys
sys.path.insert(0, ".")
from nbs_bi.clients.queries import ClientQueries, _to_exclusive_end
from nbs_bi.clients.campaigns import load_ad_spend_from_db
from nbs_bi.config import READONLY_DATABASE_URL, ADS_DATABASE_URL, INCLUDE_SWAP_FEES
from sqlalchemy import create_engine, text
import pandas as pd

START = "<START>"
END   = "<END>"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

cq = ClientQueries(start_date=START, end_date=END)
fx = cq.fx_rate()

CONV_SQL = """
SELECT direction::TEXT AS direction, COUNT(*) AS n_txns,
    SUM(COALESCE(fee_amount_brl,0)+COALESCE(spread_revenue_brl,0))::FLOAT/100.0       AS revenue_brl,
    SUM(COALESCE(fee_amount_usdc,0)+COALESCE(spread_revenue_usdc,0))::FLOAT/1000000.0 AS revenue_usdc
FROM conversion_quotes WHERE used=TRUE AND created_at>=:start AND created_at<:end
GROUP BY direction
"""
CARD_FEE_SQL = "SELECT COUNT(*) n, COALESCE(SUM(amount_usdc::FLOAT),0) v FROM card_annual_fees WHERE status='paid' AND paid_at>=:start AND paid_at<:end"
BILLING_SQL  = "SELECT COUNT(*) n, COALESCE(SUM(amount::FLOAT/1000000.0),0) v FROM billing_charges WHERE status='settled' AND created_at>=:start AND created_at<:end"
CASHBACK_SQL = "SELECT COUNT(*) n, COALESCE(SUM(reward_usd_value::FLOAT),0) v FROM cashback_rewards WHERE status='completed' AND created_at>=:start AND created_at<:end"
REVSHARE_SQL = "SELECT COUNT(*) n, COALESCE(SUM(reward_usd_value::FLOAT),0) v FROM revenue_share_rewards WHERE status='completed' AND created_at>=:start AND created_at<:end"

engine = create_engine(READONLY_DATABASE_URL, pool_pre_ping=True)
params = {"start": START, "end": _to_exclusive_end(END)}

with engine.connect() as conn:
    conv_df = pd.read_sql(text(CONV_SQL), conn, params=params)
    caf     = pd.read_sql(text(CARD_FEE_SQL), conn, params=params).iloc[0]
    bil     = pd.read_sql(text(BILLING_SQL),  conn, params=params).iloc[0]
    cbk     = pd.read_sql(text(CASHBACK_SQL), conn, params=params).iloc[0]
    rvs     = pd.read_sql(text(REVSHARE_SQL), conn, params=params).iloc[0]

def _conv_usd(row, fx):
    return float(row["revenue_brl"]) / fx + float(row["revenue_usdc"])

onramp_row  = conv_df[conv_df.direction=="brl_to_usdc"]
offramp_row = conv_df[conv_df.direction=="usdc_to_brl"]
onramp_usd  = _conv_usd(onramp_row.iloc[0],  fx) if not onramp_row.empty  else 0.0
offramp_usd = _conv_usd(offramp_row.iloc[0], fx) if not offramp_row.empty else 0.0
onramp_n    = int(onramp_row.iloc[0]["n_txns"])  if not onramp_row.empty  else 0
offramp_n   = int(offramp_row.iloc[0]["n_txns"]) if not offramp_row.empty else 0

card_fee_usd = float(caf["v"])
billing_usd  = float(bil["v"])
cashback     = float(cbk["v"])
rev_share    = float(rvs["v"])
gross        = onramp_usd + offramp_usd + card_fee_usd + billing_usd
costs        = cashback + rev_share
net          = gross - costs

spend_df = load_ad_spend_from_db(ADS_DATABASE_URL)
if spend_df is not None:
    mask   = (spend_df["date"] >= START) & (spend_df["date"] <= END)
    win    = spend_df[mask]
    meta_spend   = float(win[win.platform=="meta"]["daily_spend_usd"].sum())
    google_spend = float(win[win.platform=="google"]["daily_spend_usd"].sum())
    meta_days    = int((win.platform=="meta").sum())
    google_days  = int((win.platform=="google").sum())
else:
    meta_spend = google_spend = meta_days = google_days = 0.0

total_ads     = meta_spend + google_spend
net_after_ads = net - total_ads
roas          = gross / total_ads if total_ads else 0.0

W = 60
SEP  = "─" * W
SEP2 = "═" * W

def row(label, txns, usd):
    t = f"{txns:,}" if txns is not None else ""
    return f"  {label:<34}  {t:>7}  ${usd:>10,.2f}"

def row_cost(label, txns, usd):
    t = f"{txns:,}" if txns is not None else ""
    return f"  {label:<34}  {t:>7}  -${usd:>9,.2f}"

days = (pd.Timestamp(END) - pd.Timestamp(START)).days + 1
print(f"\n{'NBS — P&L Summary':^{W}}")
print(f"{f'{START}  →  {END}  ({days} days)':^{W}}")
print(SEP2)
print(f"  {'Line Item':<34}  {'Txns':>7}  {'USD':>11}")
print(SEP2)
print("  REVENUE")
print(SEP)
print(row("Onramp  (BRL → USDC)",       onramp_n,        onramp_usd))
print(row("Offramp (USDC → BRL)",       offramp_n,       offramp_usd))
print(row("Card annual fees",            int(caf["n"]),   card_fee_usd))
print(row("Billing charges (card txs)", int(bil["n"]),   billing_usd))
print(SEP)
print(f"  {'GROSS REVENUE':<34}  {'':>7}  ${gross:>10,.2f}")
print(SEP)
print("\n  DIRECT COSTS")
print(SEP)
print(row_cost("Cashback paid",          int(cbk["n"]), cashback))
print(row_cost("Revenue share paid",     int(rvs["n"]), rev_share))
print(SEP)
print(f"  {'NET REVENUE':<34}  {'':>7}  ${net:>10,.2f}")
print(SEP)
print("\n  MARKETING SPEND")
print(SEP)
print(row("Meta Ads",   meta_days,   meta_spend))
print(row("Google Ads", google_days, google_spend))
print(SEP)
print(f"  {'TOTAL ADS SPEND':<34}  {'':>7}  -${total_ads:>9,.2f}")
print(SEP)
print()
print(SEP2)
print(f"  {'NET AFTER ADS':<34}  {'':>7}  ${net_after_ads:>10,.2f}")
print(SEP2)
print(f"\n  Gross margin:   {net/gross*100:.1f}%  (before ads)")
print(f"  Net margin:     {net_after_ads/gross*100:.1f}%  (after ads)")
if total_ads:
    print(f"  ROAS (gross):   {roas:.2f}x  (${gross:,.0f} / ${total_ads:,.0f} spend)")
print(f"  FX rate:        R${fx:.4f}/USDC (median over window)")
print(f"  Swap fees:      {'included' if INCLUDE_SWAP_FEES else 'excluded'}")
```

Print the output as a code block so the table formatting is preserved.
