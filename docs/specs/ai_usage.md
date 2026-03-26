# SPEC — `nbs_bi.ai_usage`: AI Interaction Analytics

> Edit this file to request specific changes.

---

## Overview

Tracks AI feature usage within the consumer app: interaction counts, model costs, feature adoption, and cost-per-user.

---

## Planned Capabilities

- **Volume KPIs**: daily/monthly AI interaction counts, tokens consumed
- **Cost tracking**: cost per interaction, cost per user, monthly AI spend (USD)
- **Model breakdown**: usage and cost split by model (e.g. Claude Haiku vs. Sonnet)
- **Feature adoption**: which AI features are being used (financial advice, chat, automation)
- **User segmentation**: power users vs. casual, engagement correlation with retention
- **Efficiency metrics**: tokens per interaction, cache hit rate

---

## Data Schema (TBD)

Expected columns:
- `interaction_id`, `user_id`
- `feature` (e.g. financial_advisor, chat, anomaly_detection)
- `model_id` (e.g. claude-haiku-4-5, claude-sonnet-4-6)
- `input_tokens`, `output_tokens`, `cache_read_tokens`
- `cost_usd` (computed from token counts × model pricing)
- `latency_ms`
- `created_at`

---

## Open Questions

- [ ] Is AI usage billed via the Anthropic API directly? Which account?
- [ ] What AI features are currently live in the app?
- [ ] Is there a per-user AI cost budget?
