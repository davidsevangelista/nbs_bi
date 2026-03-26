# SPEC — `nbs_bi.cards`: Card Cost Center Simulation

> Edit this file to request specific changes. Each section is a capability.

---

## Overview

Models the complete cost structure of the Rain card program (provider: Signify Holdings / rain.xyz). The goal is to:

1. Reproduce any invoice exactly from its input parameters
2. Understand which cost drivers have the largest impact
3. Calculate the weighted average cost per transaction
4. Fit a linear model to project future monthly costs
5. Run what-if scenarios to support pricing and growth decisions

---

## Cost Categories (from Invoice NKEMEJLO-0008, Feb 2026)

| Category | Fee Line | Driver | Unit Price |
|---|---|---|---|
| Fixed | Base Program Fee | flat/month | $1,000.00 |
| Per-card | Virtual Cards Fee | n_active_cards | $0.20 |
| Per-transaction | Transaction Fee | n_transactions | $0.075 |
| Volume (bps) | Network Passthrough Volume Fee | tx_volume_usd | 14.7 bps (0.00147) |
| Security | 3D Secure Transactions Fee | n_3ds | $0.04 |
| Card tier | Visa Product Type Fee — Infinite | n_infinite_txs | $1.70 |
| Card tier | Visa Product Type Fee — Platinum | n_platinum_txs | $0.25 |
| Tokenization | ApplePay count | n_applepay_txs | $0.03 |
| Tokenization | ApplePay amount | applepay_volume_usd | $0.0015 |
| Tokenization | GooglePay count | n_googlepay_txs | $0.03 |
| Compliance | Share Token | n_share_tokens | $1.25 |
| Network | Account Verification - Domestic | n_verify_domestic | $0.0075 |
| Network | Account Verification - International | n_verify_intl | $0.09 |
| Network | Chip Authentication - International | n_chip_auth_intl | $0.04 |
| Network | Network Passthrough Tx Cost | n_transactions | $0.20 |
| Network | Network Passthrough 3DS Cost | n_3ds | $0.02 |
| Network | Cross Border Transaction Fee | n_cross_border | $0.01 |

**Reference invoice total: $6,693.58 (6,885 transactions → $0.972/tx)**

---

## Visa Card Tiers — Infinite vs. Platinum

Each client (card) is issued either a **Visa Infinite** or a **Visa Platinum** card. These are distinct product tiers — a client has one or the other, never both.

| Tier | Fee per unit | Feb 2026 qty | Feb 2026 cost |
|---|---|---|---|
| Visa Infinite | $1.70 | 884 | $1,502.80 |
| Visa Platinum | $0.25 | 829 | $207.25 |

**What drives `n_infinite_txs` and `n_platinum_txs`?**

The invoice qty for each tier represents the number of **billable events** (likely transactions) attributed to cards of that tier in the billing period. Key points:

- A client assigned Infinite: every transaction they make increments `n_infinite_txs` at $1.70/event
- A client assigned Platinum: every transaction they make increments `n_platinum_txs` at $0.25/event
- Feb 2026: 884 + 829 = 1,713 tier-attributed events vs. 6,885 total transactions — the remaining ~5,172 transactions are not billed under a product type fee (standard Visa, or exempt categories — to be confirmed with Rain)
- The **Infinite tier is 6.8× more expensive per event** than Platinum — the client mix between tiers is a critical cost lever

**Simulation implications:**

- Shifting clients from Infinite → Platinum for the same transaction volume saves $(1.70 − 0.25) = $1.45 per event
- The tier split ratio (`n_infinite_txs / (n_infinite_txs + n_platinum_txs)`) is a key scenario variable
- Future improvement: connect to client database to derive per-tier transaction counts directly rather than treating them as independent inputs

**To be confirmed with Rain:**
- Is the fee billed per transaction, per active card-month, or per authorization attempt?
- Which transaction categories are exempt from the product type fee (explains the 1,713 vs 6,885 gap)?
- Can clients be migrated between tiers, and is there a minimum commitment per tier?

---

## Planned Interface

```python
from nbs_bi.cards.models import CardCostModel
from nbs_bi.cards.simulator import CardCostSimulator

# Build model from invoice actuals
model = CardCostModel.from_invoice("data/invoices/Invoice-NKEMEJLO-0008-actuals.json")

# Cost breakdown
breakdown = model.cost_breakdown()

# Weighted average cost per transaction
cost_per_tx = model.cost_per_transaction()

# Sensitivity: which driver changes the total most per unit
sensitivity = model.sensitivity_analysis()

# Simulate a scenario
sim = CardCostSimulator(model)
scenario = sim.run(n_transactions=10_000, n_active_cards=800, n_infinite_txs=1_200)

# Linear projection: given next month's expected inputs, predict cost
projection = sim.project(n_transactions=12_000, tx_volume_usd=300_000)
```

---

## CLI Output

Running `python -m nbs_bi.cards.simulator` (or `nbs-cards`) prints a rich report to the terminal **and** saves a `.md` report to `data/cards_simulation/`. Filename pattern: `card_simulation_<period>.md`.

---

## Open Questions / Future Requests

- [x] ~~What is the Visa Infinite vs Platinum split rule?~~ → Per-client card tier; see Visa Card Tiers section above
- [ ] Is the `applepay_volume_usd` the transaction amount in USD (×117,184 in Feb)?
- [ ] What drives `n_share_tokens` (compliance)? Is it new user activations?
- [ ] Which transaction categories are exempt from the Visa product type fee (explains 1,713 vs 6,885 gap)?
- [ ] Can clients be migrated between Infinite and Platinum tiers? Minimum commitments?
- [ ] Add support for multiple invoice months to fit the linear model
