# SPEC — `nbs_bi.swaps`: Swap Analytics

> Edit this file to request specific changes.

---

## Overview

Analytics for DEX and in-app token swap operations. Tracks volumes, revenue, slippage, and protocol costs.

---

## Planned Capabilities

- **Volume KPIs**: monthly swap volumes by token pair
- **Revenue**: protocol fees or spread earned per swap
- **Slippage analysis**: average/median/p95 slippage per token pair
- **Protocol costs**: gas fees (if on-chain), DEX routing fees
- **Top pairs**: most-swapped pairs by volume and count
- **User segmentation**: swap frequency, average swap size

---

## Data Schema (TBD)

Expected columns:
- `swap_id`, `user_id`
- `token_in`, `amount_in`, `token_out`, `amount_out`
- `exchange_rate`, `slippage_pct`
- `protocol_fee_usd`, `gas_fee_usd`
- `dex_provider` (e.g. Uniswap, 1inch)
- `chain` (e.g. Ethereum, Polygon, Solana)
- `status`, `created_at`

---

## Open Questions

- [ ] Which chains and DEXs does NBS route through?
- [ ] Is swap revenue a spread or a fixed fee?
- [ ] Are swaps on-chain (gas costs) or via a custodial provider?
