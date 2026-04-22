# CHANGELOG

All notable changes to `nbs_bi` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

## [1.1.0] — 2026-04-21

Multi-invoice support for the Cards tab: invoice selector, aggregate evolution sub-tab, auto-detected invoice total. Three missing Clients tab spec items implemented. Two bug fixes (leaderboard missing user_id; billed vs modelled total discrepancy).

### Added
- `cards/invoice_parser.py` — `invoice_total_usd: float = 0.0` and `base_program_fee: float = 0.0` fields added to `CardInvoiceInputs`; default `0.0` keeps existing JSON files loadable without re-parse
- `cards/preprocess_invoices.py` — `_TOTAL_PATTERNS` list; `parse_invoice_text()` now extracts the invoice grand total via regex ("Amount due $X" / "Total $X") and stores it as `invoice_total_usd` in the output JSON; logs a warning if no pattern matches
- `reporting/cards.py` — invoice selectbox in `_render_costs()`: when multiple invoices are present, a dropdown lets the user pick which period to drill into (defaults to latest); the full history is still passed to `CardSection` for trend chart
- `reporting/cards.py` — `_fig_cost_driver_stacked(history)`: stacked bar chart, one column per period, stacked by all 17 cost line items; adds a grey "Outros (não modelado)" bar for the gap between `invoice_total_usd` and the modelled sum
- `reporting/cards.py` — `_fig_driver_delta(history)`: horizontal bar showing Δ USD vs prior period per line item; ROSE bars = cost rose, EMERALD bars = cost fell; sorted by absolute impact; only rendered with ≥ 2 invoices
- `reporting/cards.py` — `_render_evolution()` method on `CardAnalyticsSection`: KPI row (billed total, MoM Δ, cost/tx, transactions, active cards); reuses `_fig_trend()`; adds `_fig_cost_driver_stacked()`; adds `_fig_driver_delta()`; cross-invoice summary table with `billed_total`, `total` (modelled), `unmodelled`, and all 17 line items
- `reporting/cards.py` — 4th sub-tab "📈 Evolução" wired into `CardAnalyticsSection.render()`
- `reporting/dashboard.py` — `_latest_rain_invoice_total()` function: reads `invoice_total_usd` from the latest parsed JSON (falls back to computed model total if field is absent or zero); replaces the hardcoded `_RAIN_INVOICE_TOTAL_USD = 7857.40` constant
- `reporting/dashboard.py` — sidebar now shows reference invoice ID and period ("Invoice de referência: NKEMEJLO-0009 (2026-03)")
- `reporting/clients.py` — `_fig_revenue_histogram(segments)`: revenue-per-user distribution with log x-axis; uses numpy log-spaced bins, geometric-mean bar centers; only positive-revenue users included
- `reporting/clients.py` — Multi-product Users KPI in LTV & Cohorts tab: count of users with `n_products >= 2` from `product_adoption`; shown as absolute + % of users delta
- `reporting/clients.py` — Top 10% Revenue Share KPI: top-decile users' share of total revenue from `segments["net_revenue_usd"]` (full population, not just leaderboard top 50)
- `reporting/clients.py` — LTV & Cohorts KPI row expanded from 3 to 5 columns

### Changed
- `reporting/cards.py` — `_render_kpis()`: "Monthly Cost" renamed "Custo Real (Invoice)"; value is `invoice_total_usd` when > 0 (falls back to modelled total); delta shows modelled total when there is a gap
- `reporting/cards.py` — `_fig_trend()`: total-cost line now uses `invoice_total_usd` (actual billed) instead of `cost_breakdown().total` (modelled), so March correctly shows higher than February
- `reporting/cards.py` — `_render_evolution()` KPIs: MoM delta computed from billed totals
- `reporting/dashboard.py` — `main()` calls `_latest_rain_invoice_total()` to get invoice total dynamically; `_sidebar()` now accepts `invoice_id` and `invoice_period` parameters
- `reporting/cards.py` — all `st.plotly_chart()` calls given unique `key=` arguments to prevent Streamlit duplicate-element-ID error when all sub-tabs render simultaneously

### Fixed
- **Billed vs modelled total confusion** — `_render_kpis()` was showing the modelled total ($6,357.39 for March) as "Monthly Cost", making March appear cheaper than February ($6,693.57) even though Rain billed $7,857.40. Now shows the actual billed total.
- **`revenue_leaderboard()` missing user_id** — `clients/models.py`: `user_id` was absent from the column list despite the docstring promising a masked user_id. Added and masked (`str[:8] + "..."`). Fixes `test_revenue_leaderboard_masked`.
- **Plotly duplicate element ID** — `CardAnalyticsSection.render()` renders all 4 sub-tabs at once; `_render_costs()` and `_render_evolution()` both called `st.plotly_chart(_fig_trend(...))`, triggering Streamlit's auto-ID collision error. Fixed by adding unique `key=` to every `st.plotly_chart` call in the file.

## [1.0.0] — 2026-04-21

All 6 dashboard tabs complete. Full Meta Ads ROI analysis with cumulative cohort revenue tracking. Daily active user metric now covers all revenue-generating activity. Two data correctness bugs fixed.

### Added
- `reporting/marketing.py` — `MetaAdsSection` (Tab 6 "Marketing - Ads"):
  - Auto-discovers most-recent Rain CSV from `data/nbs_corp_card/` (no manual upload required)
  - `_TRACKING_START = "2026-04-12"` gates all spend data; only most-recent campaign shown in charts
  - `_build_cumulative_spend()` — cumulative spend with campaign-start markers
  - `_build_channel_comparison()` — Meta Ads row merged with acquisition-source breakdown
  - Charts: cumulative spend vs cohort revenue, ad spend vs cohort revenue bar, CAC comparison, daily signups + spend
  - KPI strip: total spend, cohort users, cohort revenue, ROAS, full-cohort CAC
  - Channel comparison table
- `clients/campaigns.py` — `_DAILY_COHORT_REVENUE_SQL`: per-day revenue from campaign-cohort users across all sources (onramp, card fees, billing charges, swaps, payouts, minus cashback and revenue share); FX from median `effective_rate`
- `clients/campaigns.py` — `CampaignAnalyzer.cumulative_revenue(campaign_id)`: queries daily cohort revenue, fills zero-revenue days, returns `date / daily_rev_usd / cum_rev_usd` from campaign start through today
- `clients/campaigns.py` — `_COHORT_REVENUE_SQL` expanded: added `swap_rev`, `payout_rev`, `cashback_cost`, `rev_share_cost` CTEs; `total_revenue_usd` now includes swaps + payouts − cashback − revenue share
- `onramp/queries.py` — 5 new active-user query methods: `card_transactions_active()`, `card_fees_active()`, `billing_charges_active()`, `swaps_active()`, `payouts_active()`; each returns `(user_id, created_at)` for uniform daily-count merging
- `docs/specs/marketing.md` — full PRD for the Marketing - Ads tab
- `tests/reporting/test_marketing.py` — 12 tests (7 happy path + 5 edge cases)

### Changed
- `reporting/dashboard.py` — 5 tabs → 6 tabs; added `_tab_marketing()` wired to `MetaAdsSection`
- `reporting/clients.py` — removed Campaign ROI sub-tab (moved to dedicated Marketing tab); `ClientSection` now has 5 sub-tabs (LTV & Cohorts, Acquisition, Segments, Founders Club, Product Adoption)
- `reporting/theme.py` — `panel()` defaults changed to `legend=dict(orientation="h", y=-0.2)` and `margin=dict(t=40, b=60, l=10, r=10)`; fixes title/legend overlap across all dashboard charts
- `reporting/overview.py` — chart title "Usuários Ativos Diários" (no qualifier); dark CSS KPI cards (`_kpi_card`, `_kpi_strip`) replacing plain `st.metric`; added `_last_day()` and `_window_avg()` helpers; imports `fmt_usd`
- `onramp/report.py` — `_build_active_daily()` accepts 7 sources: PIX deposits, PIX transfers, card txs, card fees, billing charges, swaps, payouts; `build()` fetches all 7 before calling it
- `reporting/ramp.py`, `reporting/cards.py`, `reporting/clients.py`, `reporting/overview.py` — `use_container_width=True` → `width="stretch"` (Streamlit deprecation)
- `_fig_cumulative_spend()` — now accepts `cum_rev_df`; overlays green cumulative-revenue line on red spend line; title updated to "Cumulative Meta Ads Spend vs Cohort Revenue (USD)"
- `_try_upload()` in `MetaAdsSection` — stores `analyzer` in returned dict so `render()` can call `cumulative_revenue()` without re-instantiating

### Fixed
- **Blank volume chart** — `onramp/models.py _clean()`: `from_amount_brl` and `to_amount_brl` are NULL on the opposite-direction row (onramp/offramp); `NaN + value = NaN` made `volume_brl` all-NaN for every conversion. Fixed by adding `.fillna(0.0)` to each operand before summing. Same fix applied to `volume_usdc`.
- **Timezone `UserWarning`** — DB timestamps are tz-aware (UTC); calling `.dt.to_period()` directly raised `"Converting to PeriodArray/Index representation will drop timezone information"`. Fixed by normalising all datetime columns to UTC-naive in `_clean()` via `pd.to_datetime(..., utc=True).dt.tz_convert(None)`; same pattern applied inline in `report.py` (`_build_revenue_monthly`, `_build_cohort`) and `clients/models.py` (`signup_month` computation).

## [0.9.1] — 2026-04-21

### Added
- `reporting/theme.py` — shared visual module: colour constants (`BLUE`, `EMERALD`, `AMBER`, `ROSE`, `TEAL`, `VIOLET`, `PLOT_BG`, `GRID`, `TEXT`, `TEXT_MUTED`, `BG`, `SOURCE_COLORS`), `panel()` layout helper, `fmt_brl()` / `fmt_usd()` / `fmt_usd_precise()` monetary formatters, `mask_user_id()` PII helper, `rgba()` colour utility; imported by all four reporting modules
- `_resample_conv()` in `reporting/ramp.py` — resamples daily conversion data to weekly (`W-MON`) or monthly (`MS`) buckets for the volume chart granularity toggle
- `_mom_annotations()` in `reporting/ramp.py` and `reporting/overview.py` — computes month-over-month percentage change labels (e.g. `+12.3%`) for bar chart annotation traces
- Granularity radio toggle (`Diaria / Semanal / Mensal`) on the Ramp volume chart — `st.radio` above the bar chart drives `_resample_conv`

### Changed
- `reporting/cards.py` — `_fig_breakdown()` replaced horizontal bar with `go.Waterfall`; cost components shown as sequential relative bars with a green Total bar; removes local `_mask_user_id`; imports `fmt_usd`, `fmt_usd_precise`, `mask_user_id` from `theme`; removed unused `ModuleType` import
- `reporting/ramp.py` — imports all colours, formatters, and `mask_user_id` from `theme`; removed local `_hex_to_rgb` (no longer needed); `_fig_spread_histogram()` now adds `add_vline` overlays for mean (solid) and median (dotted) per side with percentage labels; `_fig_revenue_monthly()` includes an invisible Scatter trace for MoM delta annotations; KPI strip uses `fmt_brl()`; tab labels changed to ASCII-safe strings (no emoji)
- `reporting/overview.py` — imports colours, `panel`, `fmt_brl`, `rgba` from `theme`; `_fig_volume_monthly()` includes MoM delta annotation trace; `_fig_active_users()` uses `rgba()` for fill colour; KPI strip uses `fmt_brl()`; removed local `_rgba()`, `_panel()`, `PLOT_BG`, `GRID`, `TEXT` constants
- `reporting/clients.py` — imports all colours, `fmt_usd`, `mask_user_id`, `panel`, `SOURCE_COLORS` from `theme`; removed ~30 lines of duplicated constants; `_fig_founders_scatter()` now applies `mask_user_id()` to hover text (fixes PII leak); `_fig_ltv_heatmap()` changed from `colorscale="Blues"` to `colorscale="YlGn"` anchored at `zmin=0` (zero LTV cells now visually distinct from missing data); campaign ROI metrics use `fmt_usd()`

### Fixed
- **PII leak** — `_fig_founders_scatter()` in `clients.py` was passing raw `user_id` UUIDs to Plotly hover text; now masked via `mask_user_id()` before render
- **Colour inconsistency** — `ramp.py` used `"#2196F3"` (Material blue) while other files used `"#2563EB"` (Tailwind blue); all files now use `BLUE = "#2563EB"` from `theme`
- **Monetary display inconsistency** — `overview.py` showed revenue as `R$ 8500` (zero decimals) while `ramp.py` showed `R$ 8500.00` (two decimals); both now use `fmt_brl()` → zero decimals for BRL headline figures
- **Stale test imports** — `tests/reporting/test_cards.py` imported `_mask_user_id` from `cards` (removed); updated to import from `theme.mask_user_id`; breakdown tests updated for `go.Waterfall` (was `go.Bar`); `tests/reporting/test_ramp.py` imports updated to remove deleted functions (`_fig_pnl`, `_fig_position`, `_hex_to_rgb`); added tests for `_mom_annotations`, `_resample_conv`, `_fig_spread_histogram`, `_fig_new_vs_returning`

## [0.9.0] — 2026-04-20

### Added
- `onramp/queries.py` — `_USER_ATTRIBUTION_SQL` (joins `users` → `user_registrations` → `referral_codes` → `founders`, no date filter); `_run_static()` helper for date-independent queries; `user_attribution() -> pd.DataFrame` returns acquisition_source, referral_code_name, is_founder per user
- `onramp/models.py` — 4 new methods: `revenue_by_direction()` (monthly fee+spread by onramp/offramp), `user_behavior()` (unique users, repeat rate, avg conversions), `monthly_new_vs_returning()`, `spread_stats()` (raw spread_percentage rows for histogram)
- `onramp/report.py` — 5 new keys: `user_attribution`, `user_behavior`, `spread_stats`, `revenue_by_direction`, `new_vs_returning`; `top_users` enriched with attribution columns (n=50); attribution fetched with `user_attribution()`
- `reporting/ramp.py` — restructured into 4 subtabs: 📈 Visão Geral (volume + PIX), 💰 Receita (by direction + monthly), 👥 Clientes (top N slider 5–50 + new vs returning), 📊 FX & Volume (FX rate + spread histogram); KPI strip updated (unique users + repeat rate; USDC position removed); new figure builders: `_fig_revenue_by_direction`, `_fig_new_vs_returning`, `_fig_spread_histogram`
- `reporting/overview.py` — `OverviewSection`: Tab 1 cross-module executive summary; 6-metric KPI strip (users, KYC rate, active rate, conversions, volume BRL, revenue BRL); 4 charts: monthly revenue (stacked area), monthly volume (stacked bar), daily active users, activation funnel
- 11 new unit tests in `tests/onramp/test_models.py` (user_behavior ×4, revenue_by_direction ×3, monthly_new_vs_returning ×2, spread_stats ×2); 48 onramp tests total, 134 across all modules

### Fixed
- `onramp/queries.py` — `_USER_ATTRIBUTION_SQL`: corrected column names to match production schema (`source_type` not `acquisition_source`, `attributed_referral_code_id` not `referral_code_id`, `public_name` not `name`, table `founders` not `founders_club`)

## [0.8.0] — 2026-04-20

### Added
- `clients/queries.py` — `_REVENUE_GENERATING_SQL`: all-time UNION across 8 activity tables (`conversion_quotes`, `card_annual_fees`, `billing_charges`, `swap_transactions`, `unblockpay_payouts`, `cashback_rewards`, `pix_transfers`, `revenue_share_rewards`) with no date filter; `revenue_generating_count() -> int` method returns the distinct user count (~2,407 in prod)
- `clients/models.py` — `activation_funnel()` method: returns `{total_users, kyc_done, active_users}` where `active_users` comes from `revenue_generating_count()` (all-time, not windowed); matches the "Revenue-Generating" stage of the prod dashboard funnel
- `reporting/clients.py` — `_fig_activation_funnel()`: horizontal 3-stage bar chart (All Users → KYC Done → Active); `_fig_product_adoption_bars()`: horizontal bars for the 4 top-level product categories; both rendered in the Product Adoption sub-tab
- `reporting/cards.py` — module-level helpers for CSV-driven tier pricing: `_parse_tier_csv()`, `_tier_breakdown()`, `_fig_tx_histogram()`, `_fig_tier_revenue()`, `_render_tier_results()`
- `data/card_fees/card_fees_template.csv` — tier definitions as the editable source of truth: F1–F3 flat ($0.25/$0.50/$1.00 over $0–$10/$10–$20/$20+) and P1–P10 pct (2.00%→1.00% over $0–$72+)

### Changed
- `clients/queries.py` — `_ONRAMP_REVENUE_SQL` → `_CONVERSION_REVENUE_SQL`: now returns separate `onramp_revenue_brl` (direction=`brl_to_usdc`) and `offramp_revenue_brl` (direction=`usdc_to_brl`) columns; `onramp_revenue()` → `conversion_revenue()`; `_ONRAMP_MONTHLY_SQL` → `_CONVERSION_MONTHLY_SQL` with column renamed `conversion_revenue_brl`; `onramp_monthly()` → `conversion_monthly()`; added `kyc_level` to `_COHORT_BASE_SQL`
- `clients/models.py` — `_compute_revenues()` splits BRL→USD conversion into `onramp_revenue_usd` and `offramp_revenue_usd`; both contribute to `net_revenue_usd` (offramp was previously missing); `product_adoption()` uses new taxonomy: `has_conversion` (any direction), `has_card` (annual fee OR tx fee merged), `has_swap`, `has_crossborder` (Unblockpay); `n_products` counts these 4 categories; `revenue_leaderboard()` now includes `offramp_revenue_usd` column
- `clients/report.py` — `build()` now includes `activation_funnel` key
- `reporting/cards.py` — `CardAnalyticsSection` simplified from 7 subtabs to 2: "Padrões de Uso" (daily timeline + weekly heatmap) and "Faixas de Preço" (CSV-driven tier pricing); tier revenue calculated on rolling last-30-calendar-days of actual transactions (no extrapolation factor); metrics and chart labels updated to "30d" terminology; removed `_render_coverage`, `_render_b2b` methods
- `tests/clients/test_models.py` — updated fixtures (`_make_cohort_base` adds `kyc_level`, `_make_onramp` adds `offramp_revenue_brl`/`offramp_volume_usdc`); mock updated to `conversion_revenue`/`conversion_monthly`/`revenue_generating_count`; 8 new tests (activation funnel ×4, product adoption ×4); 23 tests total, all passing; 112 tests total across all modules

## [0.7.0] — 2026-04-20

### Added
- `nbs_bi/clients/campaigns.py` — `CampaignAnalyzer`: Meta Ads ROI analysis from Rain company card CSV:
  - `load_ad_spend(csv_path, merchant_prefix='FACEBK')` — loads and aggregates daily spend from Rain CSV; filters by merchant name prefix
  - `_detect_campaigns(spend_df, gap_days=7)` — splits spend into contiguous windows separated by >7-day gaps
  - `CampaignAnalyzer.roi_summary()` — per-campaign DataFrame: cohort users, transacting rate, total revenue, ROAS, CAC (full cohort), CAC (incremental above organic baseline)
  - `CampaignAnalyzer.daily_context()` — daily signups + ad spend for dashboard charts; tags campaign vs organic days
- `nbs_bi/reporting/clients.py` — added 6th sub-tab "Campaign ROI":
  - `_fig_campaign_daily`: dual-axis chart — daily signups (bar) + ad spend line
  - `_fig_campaign_roi`: grouped bar — spend vs cohort revenue per campaign
  - `_fig_campaign_cac`: grouped bar — CAC full vs incremental per campaign
  - `_render_campaigns()`: KPI cards (total spend, cohort revenue, users, ROAS); file uploader for CSV; summary table with formatted values
- 17 unit tests in `tests/clients/test_campaigns.py` (DB-free, `_run` injected)

### Findings — Meta Ads ROI (data as of 2026-04-20)
- 3 campaigns detected from `rain-transactions-export-2026-04-20.csv`: campaign_1 (Feb 15-16, $891), campaign_2 (Feb 26-Mar 4, $501), campaign_3 (Apr 14-20, $715)
- campaign_2: ROAS 2.10× — only confirmed positive-return campaign; $501 spend → $1,052 cohort revenue from 276 users
- campaign_1: ROAS 0.12× — $891 spend, 58 cohort users, $111 revenue (cohort still early in lifecycle)
- campaign_3: ROAS 0.41× (too recent); incremental CAC ~$10.07/user vs ~15/day organic baseline

## [0.6.0] — 2026-04-20

### Added
- `nbs_bi/clients/queries.py` — `ClientQueries`: 11 parameterised SQL queries covering user cohort base (with attribution inference), onramp revenue (period + monthly time-series), card fees, card transactions, billing charges, cashback, revenue share, swaps, payouts, and FX rate; Parquet cache + `_scale_brl` helper
- `nbs_bi/clients/models.py` — `ClientModel`: master user DataFrame joining all revenue/cost streams; unified USD LTV with BRL→USD FX conversion; pro-rata Rain invoice card cost allocation; `revenue_leaderboard`, `product_adoption`, `acquisition_summary`, `referral_code_summary`, `founders_report`, `at_risk_users`, `cohort_ltv`, `ltv_by_source`, `cac_breakeven` methods
- `nbs_bi/clients/segments.py` — `ClientSegments`: champion/active/at-risk/dormant RFM classification; `segment_summary`, `founders_vs_non_founders`, `referral_performance`
- `nbs_bi/clients/report.py` — `ClientReport`: orchestrates all analyses into a structured dict; `to_json_api()` for future API consumption
- `nbs_bi/reporting/clients.py` — `ClientSection`: 5-tab Streamlit dashboard (LTV & Cohorts, Acquisition, Segments, Founders Club, Product Adoption); CAC breakeven slider; Plotly figure builders for cohort heatmap, LTV curves, acquisition bars, funnel, segment donut, founders scatter, adoption heatmap
- `nbs_bi/clients/__init__.py` — exposes `ClientModel`, `ClientReport`
- `billing_charges` integrated as direct card tx fee revenue (USDC micros ÷ 1,000,000, `status='settled'`)
- Tests: 38 tests across `test_queries.py`, `test_models.py`, `test_segments.py` (fixture-based, no DB)

### Changed
- `nbs_bi/reporting/dashboard.py` — Tab 5 wired to `ClientSection`; sidebar adds Rain Invoice Total input; `_sidebar` returns invoice total; new `_load_client_report` cached loader
- `docs/specs/clients.md` — resolved all 8 open questions; added LTV/CAC Analysis section with cohort matrix spec, CAC breakeven formula, attribution inference rule, and JSON API export note

## [0.5.0] — 2026-04-20

### Added
- `nbs_bi/cards/analytics.py` — pure data-layer module for card spend analytics (live DB via SQLAlchemy):
  - `load_card_transactions()` — DB fetch with date filters; `build_daily()`, `bin_transactions()` — aggregations
  - `fee_comparison()`, `monthly_revenue()` — fee-model comparisons for Models A/B/C/D
  - `ewma_forecast()` — EWMA demand forecast (span=7) with 95% CI for count and volume
  - `build_scenarios()`, `threshold_sweep()`, `compute_combinations()` — B2B growth scenarios and Model C optimisation
  - `flat_pct_monthly_revenue()`, `flat_pct_coverage_metrics()` — invoice-coverage math for flat + percentage card fee structures
  - Plotly figure builders: `fig_daily_timeline`, `fig_weekly_patterns`, `fig_distribution`, `fig_fee_comparison`, `fig_forecast`, `fig_summary_table`, `fig_b2b_projection`, `fig_threshold_sweep`, `fig_combo_heatmap`, `fig_combo_lines`, `fig_coverage_bar`, `fig_coverage_heatmap`
- `nbs_bi/reporting/ramp.py` — `RampSection`: Streamlit + Plotly Tab 2 renderer; 7 charts — BRL conversion volume, monthly fee vs spread revenue, implicit FX rate with p10/p90 band, USDC position, cumulative PnL, top users (masked), PIX IN/OUT flows
- `nbs_bi/reporting/cards.py`:
  - `CardSection` (Tab 3): cost breakdown, sensitivity (+10%), cost-per-tx trend, top spenders
  - `CardAnalyticsSection` (Tab 4): 8-tab interactive dashboard (patterns, distribution & models, EWMA forecast, indicators, invoice coverage, B2B projection, Model C threshold, combination grid)
- `nbs_bi/reporting/dashboard.py` — Streamlit entry point: 5 tabs, global sidebar date picker, 1-hour `@st.cache_data`, DB connection status
- `OnrampReport._build_revenue_monthly()` — monthly fee vs spread revenue; exposed as `revenue_monthly` key in `build()` return dict
- `docs/specs/card_usage_forecast.md` — spec for standalone forecast script (HTML output, 7 sections, EWMA + 95% CI)
- Unit tests: `tests/cards/test_analytics.py`, `tests/onramp/test_report.py` (23 tests), `tests/reporting/test_ramp.py`, `tests/reporting/test_cards.py`

### Changed
- Card Analytics dashboard expanded from 7 to 8 tabs — "Cobertura Invoice" tab added between Indicators and B2B Projection
- Monthly card analytics extrapolation uses inclusive observed calendar days (off-by-one fix)
- `nbs_bi.cards.__init__` lazy-loads `CardCostSimulator` — analytics imports never blocked by absent `scikit-learn`
- `nbs_bi.reporting.cards` importable in non-UI environments without `streamlit`
- `pyproject.toml`: added `streamlit>=1.32`, `plotly>=5.20` to runtime dependencies

### Findings
- Live DB (`2026-02-01` → `2026-04-13`): `$0.30 + 1.00%` → $4,495.99/month → 67.17% coverage of February Rain invoice ($6,693.58)
- Breakeven: ~1.87% variable (with $0.30 fixed) or ~$0.64 fixed (with 1.00% variable)

## [0.4.0] — 2026-04-09

### Added
- `nbs_bi/cards/analytics.py` — pure data-layer module for card spend analytics:
  - `load_card_transactions()` — DB fetch via SQLAlchemy with date filters
  - `build_daily()`, `bin_transactions()`, `fee_comparison()`, `monthly_revenue()` — aggregations and fee-model comparisons (Models A/B/C/D)
  - `ewma_forecast()` — EWMA demand forecast with 95% CI for count and volume
  - `build_scenarios()`, `threshold_sweep()`, `compute_combinations()` — B2B growth scenarios and Model C threshold optimisation
  - Plotly figure builders: `fig_daily_timeline`, `fig_weekly_patterns`, `fig_distribution`, `fig_fee_comparison`, `fig_forecast`, `fig_summary_table`, `fig_b2b_projection`, `fig_threshold_sweep`, `fig_combo_heatmap`, `fig_combo_lines`
- `nbs_bi/reporting/ramp.py` — `RampSection`: Streamlit + Plotly rendering for the On/Off Ramp tab (Tab 2); 7 charts covering volume, monthly revenue split, implicit FX rate with p10/p90 band, USDC position, cumulative PnL, top users (user IDs masked), and PIX IN/OUT flows
- `nbs_bi/reporting/cards.py` — two section classes:
  - `CardSection` (Tab 3): cost breakdown, sensitivity (+10%), cost-per-tx trend, top spenders
  - `CardAnalyticsSection` (Tab 4): 7-tab interactive dashboard driven by live DB data — usage patterns, distribution & models, EWMA forecast, indicators, B2B projection, Model C threshold, and combination grid; sidebar controls for threshold slider and editable B2B scenario table
- `nbs_bi/reporting/dashboard.py` — Streamlit entry point with 5 tabs (Overview, On/Off Ramp, Card Costs, Card Analytics, Clients), global date picker, 1-hour cached DB loads, and DB connection status indicator
- `OnrampReport._build_revenue_monthly()` — monthly fee vs spread revenue breakdown added to the report output dict as `revenue_monthly`
- Unit tests for `reporting/ramp.py` figure builders and helpers (`tests/reporting/test_ramp.py`)
- Unit tests for `reporting/cards.py` figure builders (`tests/reporting/test_cards.py`)
- Unit tests for `onramp/report.py` (`tests/onramp/test_report.py`)

### Changed
- `pyproject.toml`: added `sqlalchemy>=2.0`, `psycopg2-binary>=2.9`, `pyarrow>=15.0`, `streamlit>=1.32`, `plotly>=5.20` to runtime dependencies
- `OnrampReport.build()` return dict now includes `revenue_monthly` key alongside existing keys

## [0.3.0] — 2026-03-26

### Added
- `docs/` directory with structured project documentation
- `docs/PROGRESS.md` — phase tracker (moved from root)
- `docs/specs/` — module specs moved from `nbs_bi/<module>/SPEC.md` into dedicated doc files: `cards.md`, `transactions.md`, `onramp.md`, `swaps.md`, `ai_usage.md`, `reporting.md`
- `docs/dev/scaffold_project.md` — full developer workflow guide (setup, dev cycle, pre-commit checklist)
- `docs/dev/new_project_prompt.md` — reusable Claude Code prompt template to bootstrap new projects with the same structure

### Changed
- `CLAUDE.md`: added Documentation section with a reference table linking to all docs; updated Git Discipline to point to `docs/PROGRESS.md`; updated "What NOT to Do" to reference `docs/specs/` instead of in-module SPEC files

### Removed
- Root-level `PROGRESS.md` (moved to `docs/PROGRESS.md`)
- Per-module `SPEC.md` files from `nbs_bi/cards/`, `nbs_bi/transactions/`, `nbs_bi/onramp/`, `nbs_bi/swaps/`, `nbs_bi/ai_usage/`, `nbs_bi/reporting/` (moved to `docs/specs/`)

## [0.2.0] — 2026-03-25

### Added
- `CardCostModel.from_invoice(path)` classmethod — loads inputs from a JSON file, matching the planned SPEC interface
- `data/invoices/Invoice-NKEMEJLO-0008-actuals.json` — structured actuals for the February 2026 Rain invoice
- `notebooks/cards.ipynb` — full analysis notebook: cost breakdown, cost/tx decomposition, sensitivity chart, scenario comparison, deterministic and regression projections, volume cost curve
- `_build_markdown_report()` in `simulator.py` — renders a simulation report as a clean Markdown document
- CLI (`python -m nbs_bi.cards.simulator`) now saves output to `data/cards_simulation/card_simulation_<period>.md` in addition to terminal output
- `data/cards_simulation/` to `.gitignore` (generated output)
- **Visa Card Tiers** section in `cards/SPEC.md` — documents that Infinite ($1.70/event) and Platinum ($0.25/event) are per-client card assignments, not generic transaction buckets; notes the $1.45/event savings from Infinite → Platinum migration; flags the unresolved 1,713 vs 6,885 billing gap for confirmation with Rain
- `matplotlib>=3.8` added to package dependencies

### Changed
- `CardCostSimulator.__init__` now accepts either `CardCostModel` or `CardInvoiceInputs` — matches `CardCostSimulator(model)` from the SPEC
- `CardCostSimulator.project()` no longer raises when called without a fitted regression — falls back to the deterministic rate model, making it usable from day one with a single invoice month
- `cards/SPEC.md` cost table: "Product type" category renamed to "Card tier"; Visa Infinite/Platinum fee lines clarified
- `cards/SPEC.md` open questions: Visa tier question marked resolved; two new questions added (billing gap, tier migration rules); CLI output section added

### Fixed
- `nbs_bi/cards/__init__.py` exported non-existent `InvoiceLineItem` — corrected to `CardInvoiceInputs`
- `pyproject.toml` build backend corrected from `setuptools.backends.legacy:build` to `setuptools.build_meta`
- Test `test_project_without_fit_raises` updated to reflect new fallback behaviour of `project()`
- Unused `CardFeeRates` import removed from test module

---

## [0.1.0] — 2026-03-25

### Added
- Project scaffolding: `CLAUDE.md`, `README.md`, `PROGRESS.md`, `CHANGELOG.md`
- `pyproject.toml` with package metadata, dependencies (`numpy`, `pandas`, `scikit-learn`, `pydantic`, `rich`, `tabulate`, `python-dotenv`), and dev tooling (`pytest`, `ruff`, `pip-audit`)
- Installable Python package `nbs_bi` with submodules: `cards`, `transactions`, `onramp`, `swaps`, `ai_usage`, `reporting`
- `SPEC.md` for each module defining scope, planned interface, data schema, and open questions
- `nbs_bi/cards/invoice_parser.py` — `CardInvoiceInputs` dataclass with validation and `from_february_2026()` factory
- `nbs_bi/cards/models.py` — `CardFeeRates`, `CostBreakdown`, `CardCostModel` with full breakdown, cost/tx, sensitivity analysis, and cost contribution percentage
- `nbs_bi/cards/simulator.py` — `CardCostSimulator` with scenario runner, `compare_scenarios`, `fit_linear_model`, `project`, `baseline_report`, and `main()` CLI entrypoint
- 16 unit tests in `tests/cards/test_simulator.py` validating against the February 2026 invoice ($6,693.58)
- Invoice reference: Rain NKEMEJLO-0008, February 2026 — $6,693.58 USD, 6,885 transactions, $0.972/tx
- `nbs-cards` CLI script entrypoint via `pyproject.toml`

---

## Format Reference

```
## [x.y.z] — YYYY-MM-DD

### Added      ← new features
### Changed    ← changes to existing behavior
### Deprecated ← soon-to-be removed
### Removed    ← removed features
### Fixed      ← bug fixes
### Security   ← security patches
```
