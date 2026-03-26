# SPEC — `nbs_bi.reporting`: Cross-Module Reporting

> Edit this file to request specific changes.

---

## Overview

Consolidates outputs from all modules into unified reports, dashboards, and exports. Supports monthly review meetings and strategic planning.

---

## Planned Capabilities

- **Monthly cost center report**: total costs by module (cards, on-ramp, swaps, AI)
- **Cost per user**: blended cost across all services per active user
- **Projection report**: next-month cost estimates using fitted linear models
- **Waterfall chart data**: cost evolution month-over-month
- **Export**: CSV, Excel (`.xlsx`), and JSON formats
- **CLI report**: `nbs-report --month 2026-02`

---

## Open Questions

- [ ] Who is the primary audience for reports? (CEO, CFO, ops team)
- [ ] Is there a preferred BI tool to export to? (Metabase, Superset, Looker, etc.)
- [ ] Should reports include BRL/USD dual currency columns?
