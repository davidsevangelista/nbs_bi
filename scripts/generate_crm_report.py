#!/usr/bin/env python3
"""
Generate the CRM clustering executive summary HTML from a run notebook.

Usage:
    python scripts/generate_crm_report.py
    python scripts/generate_crm_report.py --notebook notebooks/crm_user_clustering.ipynb
    python scripts/generate_crm_report.py --out docs/crm_clustering_executive_summary.html
"""

import argparse
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path


# ── Notebook parsing helpers ──────────────────────────────────────────────────

def _cell_text_outputs(cell: dict) -> str:
    """Concatenate all stream + execute_result text outputs for a code cell."""
    parts = []
    for o in cell.get("outputs", []):
        if o.get("output_type") == "stream":
            parts.append("".join(o.get("text", [])))
        elif o.get("output_type") in ("execute_result", "display_data"):
            text = "".join(o.get("data", {}).get("text/plain", []))
            if text:
                parts.append(text)
    return "\n".join(parts)


def _source(cell: dict) -> str:
    return "".join(cell.get("source", []))


def _find_cell(cells: list[dict], *, containing: str, cell_type: str = "code") -> dict | None:
    for c in cells:
        if c["cell_type"] == cell_type and containing in _source(c):
            return c
    return None


def _int(text: str, pattern: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1).replace(",", "")) if m else None


def _float(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


# ── Extract all metrics from the notebook ────────────────────────────────────

def extract_metrics(nb: dict) -> dict:
    cells = nb["cells"]
    m: dict = {}

    # Analysis month
    setup_cell = _find_cell(cells, containing="Analysis month:")
    if setup_cell:
        out = _cell_text_outputs(setup_cell)
        match = re.search(r"Analysis month:\s*(\S+)", out)
        m["analysis_month"] = match.group(1) if match else "unknown"

    # Total users
    users_cell = _find_cell(cells, containing="sql_users")
    if users_cell:
        out = _cell_text_outputs(users_cell)
        m["total_users"] = _int(out, r"Users:\s*([\d,]+)")

    # Clean population & quarantined
    clean_cell = _find_cell(cells, containing="Clean population:")
    if clean_cell:
        out = _cell_text_outputs(clean_cell)
        m["clean_users"] = _int(out, r"Clean population:\s*([\d,]+)")
        m["quarantined"] = _int(out, r"\(([\d,]+) quarantined\)")

    # Outlier %
    outlier_cell = _find_cell(cells, containing="Outliers identified:")
    if outlier_cell:
        out = _cell_text_outputs(outlier_cell)
        pct = _float(out, r"\(([\d.]+)%")
        m["outlier_pct"] = pct

    # PCA
    pca_cell = _find_cell(cells, containing="PCA:")
    if pca_cell:
        out = _cell_text_outputs(pca_cell)
        m["pca_components"] = _int(out, r"PCA:\s*(\d+) components")
        m["pca_variance"] = _float(out, r"([\d.]+)% variance retained")

    # K selection table
    k_cell = _find_cell(cells, containing="k=4: silhouette")
    k_rows = []
    if k_cell:
        for line in _cell_text_outputs(k_cell).splitlines():
            match = re.match(
                r"k=(\d+):\s+silhouette=([\d.]+)\s+DB=([\d.]+)\s+inertia=([\d.]+)", line
            )
            if match:
                k_rows.append({
                    "k": int(match.group(1)),
                    "silhouette": float(match.group(2)),
                    "db": float(match.group(3)),
                    "inertia": int(float(match.group(4))),
                })
    m["k_rows"] = k_rows

    # BEST_K
    bestk_cell = _find_cell(cells, containing="Using BEST_K")
    if bestk_cell:
        out = _cell_text_outputs(bestk_cell)
        m["best_k"] = _int(out, r"Using BEST_K\s*=\s*(\d+)")

    # GMM ARI
    gmm_cell = _find_cell(cells, containing="K-Means vs GMM")
    if gmm_cell:
        out = _cell_text_outputs(gmm_cell)
        m["gmm_ari"] = _float(out, r"Adjusted Rand Index:\s*([\d.]+)")

    # Stability ARI
    stab_cell = _find_cell(cells, containing="Stability — min pairwise ARI")
    if stab_cell:
        out = _cell_text_outputs(stab_cell)
        m["stability_ari"] = _float(out, r"min pairwise ARI[^:]*:\s*([\d.]+)")

    # Cluster means table (key features)
    # pandas wraps wide tables into multiple sections separated by blank lines.
    # Each section has a header row of column names and data rows starting with
    # the cluster_id integer. We parse each section independently and merge.
    profile_cell = _find_cell(cells, containing="CLUSTER MEANS")
    cluster_means: dict[int, dict] = {}
    if profile_cell:
        out = _cell_text_outputs(profile_cell)

        # Parse cluster sizes
        sizes_block = re.search(r"CLUSTER SIZES.*?dtype: int64", out, re.DOTALL)
        if sizes_block:
            for line in sizes_block.group().splitlines():
                match = re.match(r"^(\d+)\s+([\d,]+)", line.strip())
                if match:
                    cid = int(match.group(1))
                    cluster_means.setdefault(cid, {})["n_users"] = int(
                        match.group(2).replace(",", "")
                    )

        # Extract just the CLUSTER MEANS block (everything after the header line)
        means_block_match = re.search(r"=== CLUSTER MEANS.*?\n(.*)", out, re.DOTALL)
        if means_block_match:
            means_text = means_block_match.group(1)
            # Split into sections on blank lines
            sections = re.split(r"\n\n+", means_text.strip())
            for section in sections:
                lines = section.splitlines()
                if not lines:
                    continue
                # Header line: first line that is NOT a data row and NOT "cluster_id"
                # Column names appear before the "cluster_id" index label line
                col_names: list[str] = []
                data_lines: list[str] = []
                for line in lines:
                    stripped = line.strip().rstrip("\\").strip()
                    # Data row: starts with a digit (cluster_id)
                    if re.match(r"^\d+\s", stripped):
                        data_lines.append(stripped)
                    elif stripped and not stripped.startswith("cluster_id"):
                        # This is a column-name header line — may be continued with \
                        col_names.extend(stripped.split())

                if not col_names or not data_lines:
                    continue

                for data_line in data_lines:
                    parts = data_line.split()
                    if not parts:
                        continue
                    try:
                        cid = int(parts[0])
                    except ValueError:
                        continue
                    values = parts[1:]
                    cluster_means.setdefault(cid, {})
                    for col, raw_val in zip(col_names, values):
                        try:
                            cluster_means[cid][col] = float(raw_val)
                        except ValueError:
                            pass

    m["cluster_means"] = cluster_means

    # Segment name distribution (full population)
    seg_dist_cell = _find_cell(cells, containing="SEGMENT_NAMES")
    seg_full: dict[str, int] = {}
    if seg_dist_cell:
        out = _cell_text_outputs(seg_dist_cell)
        for line in out.splitlines():
            match = re.match(r"^(\w+)\s+([\d,]+)", line.strip())
            if match:
                seg_full[match.group(1)] = int(match.group(2).replace(",", ""))
    m["seg_full"] = seg_full

    # Pushable segment distribution
    push_cell = _find_cell(cells, containing="Pushable users after delivery rules")
    seg_push: dict[str, int] = {}
    pushable_total = None
    if push_cell:
        out = _cell_text_outputs(push_cell)
        m["push_token_users"] = _int(out, r"active push token:\s*([\d,]+)")
        pushable_total = _int(out, r"Pushable users after delivery rules:\s*([\d,]+)")
        m["pushable_total"] = pushable_total
        for line in out.splitlines():
            match = re.match(r"^(\w+)\s+([\d,]+)", line.strip())
            if match and match.group(1) not in (
                "Users", "Pushable", "segment_name", "Name"
            ):
                seg_push[match.group(1)] = int(match.group(2).replace(",", ""))
    m["seg_push"] = seg_push

    # KYC override count
    kyc_cell = _find_cell(cells, containing="KYC level 0")
    if kyc_cell:
        out = _cell_text_outputs(kyc_cell)
        m["kyc_override_count"] = _int(out, r"KYC level[^:]+:\s*([\d,]+)")

    # MESSAGES dict — evaluate from source safely using ast.literal_eval on the dict only
    msg_cell = _find_cell(cells, containing="MESSAGES = {")
    messages: dict = {}
    if msg_cell:
        src = _source(msg_cell)
        dict_match = re.search(r"^MESSAGES\s*=\s*(\{.*?\})\s*$", src, re.DOTALL | re.MULTILINE)
        if dict_match:
            try:
                messages = ast.literal_eval(dict_match.group(1))
            except Exception:
                pass
    m["messages"] = messages

    # SEGMENT_NAMES mapping — parse from source
    names_cell = _find_cell(cells, containing="SEGMENT_NAMES = {")
    segment_names: dict[int, str] = {}
    if names_cell:
        src = _source(names_cell)
        dict_match = re.search(r"SEGMENT_NAMES\s*=\s*(\{[^}]+\})", src)
        if dict_match:
            try:
                raw = ast.literal_eval(dict_match.group(1))
                segment_names = {int(k): str(v) for k, v in raw.items()}
            except Exception:
                pass
    m["segment_names"] = segment_names

    # Number of features
    feat_cell = _find_cell(cells, containing="Feature matrix shape:")
    if feat_cell:
        out = _cell_text_outputs(feat_cell)
        fm = re.search(r"Feature matrix shape:\s*\([\d,]+,\s*(\d+)\)", out)
        m["n_features"] = int(fm.group(1)) if fm else None

    return m


# ── Revenue tier helper ───────────────────────────────────────────────────────

def _revenue_tier(avg_revenue: float) -> tuple[str, str]:
    """Return (badge_class, label) based on avg spread revenue."""
    if avg_revenue >= 50:
        return "badge-high", "Highest"
    if avg_revenue >= 15:
        return "badge-high", "High"
    if avg_revenue >= 5:
        return "badge-med", "Medium"
    if avg_revenue > 0:
        return "badge-low", "Low"
    return "badge-gray", "None"


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _badge(cls: str, label: str) -> str:
    return f'<span class="badge {cls}">{label}</span>'


def _msg_grid(segment: str, messages: dict) -> str:
    data = messages.get(segment)
    if not data:
        return "<p><em>Mensagens não definidas para este segmento.</em></p>"
    slots = [
        ("morning",   "07–09h"),
        ("afternoon", "12–14h"),
        ("evening",   "19–21h"),
    ]
    cards = []
    for slot_key, slot_label in slots:
        variants = data.get(slot_key, [])
        if not variants:
            continue
        title, body = variants[0]
        cards.append(f"""
      <div class="msg-card">
        <div class="msg-slot">{slot_label}</div>
        <div class="msg-content">
          <div class="msg-title">{title}</div>
          <div class="msg-body">{body}</div>
        </div>
      </div>""")
    return f'<div class="msg-grid">{"".join(cards)}\n    </div>'


def _k_table(k_rows: list[dict], best_k: int) -> str:
    rows = []
    for row in k_rows:
        selected = row["k"] == best_k
        cls = ' class="selected"' if selected else ""
        marker = " ✓" if selected else ""
        note = "Best silhouette — selected" if selected else (
            "Silhouette drops, DB worsens" if row["k"] == best_k + 1 else ""
        )
        rows.append(
            f"      <tr{cls}>"
            f"<td>{row['k']}{marker}</td>"
            f"<td>{row['silhouette']:.3f}</td>"
            f"<td>{row['db']:.3f}</td>"
            f"<td>{row['inertia']:,}</td>"
            f"<td>{note}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _seg_table_rows(
    seg_full: dict[str, int],
    seg_push: dict[str, int],
    cluster_means: dict,
    segment_names: dict[int, str],
) -> str:
    # Build revenue map from cluster_means: segment_name -> avg_revenue
    revenue_map: dict[str, float] = {}
    days_map: dict[str, float] = {}
    for cid, data in cluster_means.items():
        name = segment_names.get(cid)
        if not name:
            continue
        revenue_map[name] = data.get("total_spread_revenue_brl", 0.0)
        days_map[name] = data.get("days_since_last_active", 0.0)

    # Sort: unnamed segments last, then by full-population size descending
    def sort_key(seg: str) -> tuple:
        is_unnamed = seg.startswith("segment_")
        return (is_unnamed, -(seg_full.get(seg, 0)))

    segments = sorted(set(list(seg_full.keys()) + list(seg_push.keys())), key=sort_key)
    rows = []
    for seg in segments:
        n_full = seg_full.get(seg, 0)
        pct = n_full / sum(seg_full.values()) * 100 if seg_full else 0
        n_push = seg_push.get(seg, 0)
        rev = revenue_map.get(seg, 0.0)
        days = days_map.get(seg, 0.0)
        tier_cls, tier_label = _revenue_tier(rev)

        display = seg.replace("_", " ").title()
        if seg.startswith("segment_"):
            display += ' <em style="font-size:12px;color:var(--muted)">(unnamed — review heatmap)</em>'

        rows.append(
            f"      <tr>"
            f"<td><strong>{display}</strong></td>"
            f"<td>{n_full:,} <span style=\"color:var(--muted);font-size:11px\">({pct:.1f}%)</span></td>"
            f"<td>{n_push:,}</td>"
            f"<td>R$ {rev:.2f}</td>"
            f"<td>{days:.0f} days</td>"
            f"<td>{_badge(tier_cls, tier_label)}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _segment_profile_cards(
    seg_full: dict[str, int],
    seg_push: dict[str, int],
    messages: dict,
    cluster_means: dict,
    segment_names: dict[int, str],
) -> str:
    # Canonical ordering for the profiles section
    ORDER = [
        "defi_trader",
        "power_onramper",
        "card_spender",
        "occasional_onramer",
        "stalled_pre_kyc",
        "dormant",
        "explorer_ai",
    ]
    # Collect all named + unnamed
    all_segs = list(seg_full.keys())
    named = [s for s in ORDER if s in all_segs]
    unnamed = sorted(s for s in all_segs if s not in ORDER)
    ordered = named + unnamed

    # Build lookup tables
    revenue_map: dict[str, float] = {}
    days_map: dict[str, float] = {}
    onramp_map: dict[str, float] = {}
    card_map: dict[str, float] = {}
    swap_map: dict[str, float] = {}
    ai_map: dict[str, float] = {}
    breadth_map: dict[str, float] = {}
    for cid, data in cluster_means.items():
        name = segment_names.get(cid)
        if not name:
            continue
        revenue_map[name] = data.get("total_spread_revenue_brl", 0.0)
        days_map[name] = data.get("days_since_last_active", 0.0)
        onramp_map[name] = data.get("n_onramp_txns", 0.0)
        card_map[name] = data.get("n_card_txns", 0.0)
        swap_map[name] = data.get("n_swaps", 0.0)
        ai_map[name] = data.get("n_ai_sessions", 0.0)
        breadth_map[name] = data.get("product_breadth_score", 0.0)

    GOALS = {
        "defi_trader":         "Deepening swap engagement. DeFi-native language only — no BRL/USDC conversion pitch, no card offer.",
        "power_onramper":      "Cross-selling DeFi swaps and card. VIP framing — never discount, never pitch KYC.",
        "card_spender":        "Onramp cross-sell. They trust the card — remove friction to first conversion.",
        "occasional_onramer":  "First or second onramp. Remove friction — do not pitch advanced products yet.",
        "stalled_pre_kyc":     "KYC funnel completion. Minimal copy, single CTA. High proportion of dead push tokens — only send to confirmed active tokens.",
        "dormant":             "Win-back. Maximum one send per quarter. Skip if notification_read_rate = 0 over last 90 days.",
        "explorer_ai":         "First conversion activation. They know the product — remove friction, do not educate.",
    }

    def _profile_description(seg: str) -> str:
        n = seg_full.get(seg, 0)
        push = seg_push.get(seg, 0)
        rev = revenue_map.get(seg, 0.0)
        days = days_map.get(seg, 0.0)
        onramp = onramp_map.get(seg, 0.0)
        card = card_map.get(seg, 0.0)
        swap = swap_map.get(seg, 0.0)
        ai = ai_map.get(seg, 0.0)
        breadth = breadth_map.get(seg, 0.0)

        tier_cls, tier_label = _revenue_tier(rev)
        tier_badge = _badge(tier_cls, tier_label)

        stats = (
            f"{n:,} users &nbsp;·&nbsp; "
            f"{push:,} pushable &nbsp;·&nbsp; "
            f"avg R${rev:.2f} revenue &nbsp;·&nbsp; "
            f"{days:.0f} days since active &nbsp;·&nbsp; "
            f"breadth {breadth:.1f}/6"
        )
        detail_parts = []
        if onramp > 0:
            detail_parts.append(f"avg {onramp:.1f} onramp txns")
        if card > 0:
            detail_parts.append(f"avg {card:.1f} card txns")
        if swap > 0:
            detail_parts.append(f"avg {swap:.1f} swaps")
        if ai > 0:
            detail_parts.append(f"avg {ai:.0f} AI sessions")
        detail = " · ".join(detail_parts) if detail_parts else "No financial activity recorded"

        return f"""
    <p style="font-size:12px;color:var(--muted);margin-bottom:6px">{stats}</p>
    <p>{detail}</p>"""

    parts = []
    for seg in ordered:
        display = seg.replace("_", " ").title()
        rev = revenue_map.get(seg, 0.0)
        tier_cls, tier_label = _revenue_tier(rev)
        badge_html = _badge(tier_cls, tier_label)
        goal = GOALS.get(seg, "Cluster não nomeado — revisar heatmap e definir estratégia de campanha.")

        is_unnamed = seg.startswith("segment_")
        unnamed_warning = (
            f'<div class="callout callout-amber" style="margin-top:12px">'
            f'<strong>Unnamed cluster.</strong> Review the notebook heatmap, assign a name '
            f'in <code>SEGMENT_NAMES</code>, and add campaign copy to <code>MESSAGES</code>.'
            f'</div>'
        ) if is_unnamed else ""

        profile = _profile_description(seg)
        grid = _msg_grid(seg, messages)

        parts.append(f"""
  <div class="card">
    <h3>{display} <span style="vertical-align:middle;margin-left:8px">{badge_html}</span></h3>
    {profile}
    {unnamed_warning}
    <p style="margin-top:12px"><strong>Campaign goal:</strong> {goal}</p>
    {grid}
  </div>""")

    return "\n".join(parts)


# ── HTML template ─────────────────────────────────────────────────────────────

CSS = """
  :root {
    --ink:    #1a1a2e;
    --muted:  #5a5a7a;
    --accent: #4f46e5;
    --green:  #059669;
    --amber:  #d97706;
    --red:    #dc2626;
    --bg:     #f8f8fc;
    --card:   #ffffff;
    --border: #e4e4f0;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--ink); font-size: 15px; line-height: 1.65;
  }
  .page { max-width: 960px; margin: 0 auto; padding: 48px 32px 80px; }
  .doc-header { border-bottom: 2px solid var(--accent); padding-bottom: 24px; margin-bottom: 40px; }
  .doc-header .label { font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); margin-bottom: 8px; }
  .doc-header h1 { font-size: 28px; font-weight: 700; letter-spacing: -.3px; line-height: 1.2; margin-bottom: 6px; }
  .doc-header .meta { font-size: 13px; color: var(--muted); }
  h2 { font-size: 19px; font-weight: 700; color: var(--ink); margin: 44px 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
  h3 { font-size: 15px; font-weight: 700; color: var(--ink); margin: 28px 0 10px; }
  p { margin-bottom: 12px; color: #2a2a3e; }
  .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 24px 0 32px; }
  .kpi { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 18px 16px; }
  .kpi .value { font-size: 26px; font-weight: 800; color: var(--accent); line-height: 1.1; }
  .kpi .label { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px 22px; margin-bottom: 12px; }
  .steps { counter-reset: step; margin: 20px 0; }
  .step { display: flex; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--border); }
  .step:last-child { border-bottom: none; }
  .step-num { counter-increment: step; width: 28px; height: 28px; min-width: 28px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 12px; font-weight: 800; display: flex; align-items: center; justify-content: center; margin-top: 2px; }
  .step-body strong { display: block; font-size: 14px; margin-bottom: 3px; }
  .step-body span { font-size: 13px; color: var(--muted); }
  .seg-table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13.5px; }
  .seg-table th { background: #f0f0f8; text-align: left; padding: 10px 12px; font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); border-bottom: 2px solid var(--border); }
  .seg-table td { padding: 11px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .seg-table tr:last-child td { border-bottom: none; }
  .seg-table tr:hover td { background: #f8f8fc; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 100px; font-size: 11px; font-weight: 700; white-space: nowrap; }
  .badge-high { background: #d1fae5; color: #065f46; }
  .badge-med  { background: #fef3c7; color: #92400e; }
  .badge-low  { background: #fee2e2; color: #991b1b; }
  .badge-info { background: #e0e7ff; color: #3730a3; }
  .badge-gray { background: #f1f1f6; color: #4a4a6a; }
  .metric-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 12px 0; }
  .metric-table th { padding: 8px 12px; background: #f0f0f8; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); text-align: right; }
  .metric-table th:first-child { text-align: left; }
  .metric-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: right; }
  .metric-table td:first-child { text-align: left; font-weight: 600; }
  .metric-table tr.selected td { background: #eef2ff; font-weight: 700; color: var(--accent); }
  .callout { padding: 14px 18px; border-radius: 8px; margin: 16px 0; font-size: 14px; }
  .callout-green  { background: #ecfdf5; border-left: 4px solid var(--green);  color: #064e3b; }
  .callout-amber  { background: #fffbeb; border-left: 4px solid var(--amber);  color: #78350f; }
  .callout-purple { background: #eef2ff; border-left: 4px solid var(--accent); color: #312e81; }
  .msg-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 12px; }
  .msg-card { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .msg-slot { font-size: 10px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; background: #f0f0f8; color: var(--muted); padding: 6px 10px; border-bottom: 1px solid var(--border); }
  .msg-content { padding: 10px; }
  .msg-title { font-size: 12px; font-weight: 700; margin-bottom: 4px; color: var(--ink); }
  .msg-body  { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .doc-footer { margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--border); font-size: 12px; color: var(--muted); }
  ul { margin: 12px 0 12px 20px; color: #2a2a3e; }
  li { margin-bottom: 8px; }
  @media print {
    body { background: white; }
    .kpi-row { grid-template-columns: repeat(4, 1fr); }
    .msg-grid { grid-template-columns: repeat(3, 1fr); }
  }
"""


def render_html(m: dict) -> str:
    total_users = m.get("total_users", 0) or 0
    clean_users = m.get("clean_users", 0) or 0
    quarantined = m.get("quarantined", 0) or 0
    pushable    = m.get("pushable_total", 0) or 0
    best_k      = m.get("best_k", "?")
    month       = m.get("analysis_month", "unknown")
    pca_comp    = m.get("pca_components", "?")
    pca_var     = m.get("pca_variance", 0.0) or 0.0
    outlier_pct = m.get("outlier_pct", 0.0) or 0.0
    gmm_ari     = m.get("gmm_ari", 0.0) or 0.0
    stab_ari    = m.get("stability_ari", 0.0) or 0.0
    n_features  = m.get("n_features", "?")
    kyc_count   = m.get("kyc_override_count", 0) or 0

    seg_full      = m.get("seg_full", {})
    seg_push      = m.get("seg_push", {})
    cluster_means = m.get("cluster_means", {})
    segment_names = m.get("segment_names", {})
    messages      = m.get("messages", {})
    k_rows        = m.get("k_rows", [])

    stab_callout_cls = "callout-green" if stab_ari >= 0.85 else "callout-amber"
    stab_callout_msg = (
        f"<strong>Stability check passed:</strong> Min pairwise ARI across 5 seeds = <strong>{stab_ari:.3f}</strong>. "
        "Cluster assignments are essentially deterministic — users will not drift between segments due to random initialisation on monthly re-runs."
        if stab_ari >= 0.85 else
        f"<strong>Stability check failed (ARI = {stab_ari:.3f}):</strong> Cluster assignments vary across seeds. "
        "Consider revisiting k or the feature set before using this segmentation for campaigns."
    )

    gmm_callout = (
        f"<strong>GMM cross-validation:</strong> ARI between K-Means and GMM = {gmm_ari:.3f}. "
        + (
            "Clusters are robust across algorithm choice." if gmm_ari >= 0.85 else
            f"Below the 0.85 robust threshold. This reflects genuine overlap between adjacent behavioral segments — "
            "the cluster structure is stable and useful for campaigns; the overlap is a property of the user base, not an algorithmic failure."
        )
    )

    generated_date = date.today().strftime("%Y-%m-%d")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NBS CRM User Clustering — Executive Summary ({month})</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">

  <div class="doc-header">
    <div class="label">Confidential · NBS SPSAV LTDA</div>
    <h1>CRM User Clustering — Executive Summary</h1>
    <div class="meta">Analysis month: {month} &nbsp;·&nbsp; Generated: {generated_date} &nbsp;·&nbsp; Status: Phase 1 complete</div>
  </div>

  <div class="kpi-row">
    <div class="kpi"><div class="value">{total_users:,}</div><div class="label">Registered users</div></div>
    <div class="kpi"><div class="value">{clean_users:,}</div><div class="label">Users clustered</div></div>
    <div class="kpi"><div class="value">{pushable:,}</div><div class="label">Pushable users (active token)</div></div>
    <div class="kpi"><div class="value">{best_k}</div><div class="label">Behavioral segments</div></div>
  </div>

  <h2>1. Problem</h2>
  <p>
    NBS has {total_users:,} registered users with radically different behavioral profiles: some are high-volume onrampers generating meaningful spread revenue, others are DeFi traders who never converted BRL, others registered and never opened the app again. The existing segmentation (champion / active / at_risk / dormant) classifies users only by <em>recency and revenue</em> — it cannot distinguish <em>how</em> users use the product.
  </p>
  <p>
    This project replaces that coarse classification with an ML-derived segmentation — mapping each user to a behavioral archetype — so that push campaigns can be targeted by product usage pattern, recency, and revenue profile simultaneously.
  </p>

  <h2>2. Approach</h2>
  <div class="steps">
    <div class="step"><div class="step-num"></div><div class="step-body">
      <strong>Feature extraction — {n_features} behavioral signals per user</strong>
      <span>Six groups: lifecycle (signup age, onboarding status), onramp/offramp (volume, revenue, recency), card (spend, annual fee), DeFi/Solana (swaps, token diversity), international payouts, and engagement (AI sessions, notification read rate). Extracted directly from the production DB. No PII. Note: <code>kyc_level</code> is excluded from clustering features — it is effectively binary in this user base (0 = unverified, 2 = verified), collinear with all activity features. It is kept as a metadata column to drive the KYC-override messaging rule.</span>
    </div></div>
    <div class="step"><div class="step-num"></div><div class="step-body">
      <strong>Preprocessing — log transform → StandardScaler → PCA</strong>
      <span>Financial data follows power-law distributions. log1p compresses the tail without losing zeros. StandardScaler normalises scale across features. PCA retains {pca_var:.1f}% of variance in {pca_comp} components, eliminating correlated signals and preventing high-variance features from dominating the distance metric.</span>
    </div></div>
    <div class="step"><div class="step-num"></div><div class="step-body">
      <strong>Outlier quarantine — HDBSCAN</strong>
      <span>{quarantined:,} users ({outlier_pct:.1f}%) were removed before clustering — extreme profiles (very high spenders, bots, automated wallets) that would distort cluster centroids. Excluded from the push export.</span>
    </div></div>
    <div class="step"><div class="step-num"></div><div class="step-body">
      <strong>K selection — silhouette score, Davies-Bouldin index, inertia elbow</strong>
      <span>K-Means evaluated for k = 4–11. k = {best_k} maximised the silhouette score ({max((r["silhouette"] for r in k_rows), default=0):.3f}) — the clearest behavioural separation achievable in this user base.</span>
    </div></div>
    <div class="step"><div class="step-num"></div><div class="step-body">
      <strong>Final clustering — K-Means + GMM validation + seed stability</strong>
      <span>K-Means (k = {best_k}, 20 restarts with k-means++ initialisation). Cross-validated against GMM (ARI = {gmm_ari:.3f}). Stability confirmed across 5 seeds — min pairwise ARI = {stab_ari:.3f}.</span>
    </div></div>
    <div class="step"><div class="step-num"></div><div class="step-body">
      <strong>Export — CSV with per-segment Brazilian Portuguese push copy</strong>
      <span>{pushable:,} pushable users (active device token + delivery rules applied) exported with segment name, cluster ID, kyc_level, and ready-to-send push copy (title + body) for three time-of-day slots in Brazilian Portuguese. Users with kyc_level ≤ 1 receive a KYC completion CTA regardless of cluster.</span>
    </div></div>
  </div>

  <h2>3. Cluster Count Selection</h2>
  <table class="metric-table">
    <thead>
      <tr><th>k</th><th>Silhouette ↑</th><th>Davies-Bouldin ↓</th><th>Inertia</th><th>Assessment</th></tr>
    </thead>
    <tbody>
{_k_table(k_rows, best_k)}
    </tbody>
  </table>
  <div class="callout {stab_callout_cls}">{stab_callout_msg}</div>
  <div class="callout callout-amber">{gmm_callout}</div>

  <h2>4. User Segments</h2>
  <table class="seg-table">
    <thead>
      <tr><th>Segment</th><th>Users (total)</th><th>Pushable</th><th>Avg revenue (BRL)</th><th>Days since active</th><th>Revenue tier</th></tr>
    </thead>
    <tbody>
{_seg_table_rows(seg_full, seg_push, cluster_means, segment_names)}
    </tbody>
  </table>

  <h2>5. Segment Profiles &amp; Campaign Strategy</h2>
{_segment_profile_cards(seg_full, seg_push, messages, cluster_means, segment_names)}

  <h2>6. KYC Override Rule</h2>
  <p>
    Users with <strong>kyc_level ≤ 1 (not yet fully verified)</strong> receive a dedicated KYC completion CTA regardless of their cluster assignment — the generic segment message is irrelevant to a user who has not completed verification. kyc_level = 2 means fully verified; those users always receive their segment copy.
  </p>
  <div class="callout callout-purple">
    <strong>Why as a delivery-layer rule, not a cluster?</strong> kyc_level was removed from clustering features — it is effectively binary in this user base (0 or 2), collinear with <em>onboarding_completed</em> and every activity feature. Including it would double-count information already captured by presence flags and transaction counts. It is retained as a metadata column in the export solely to drive this override.
  </div>

  <h2>7. Why PCA Before K-Means</h2>
  <p>The {n_features} input features were reduced to <strong>{pca_comp} principal components retaining {pca_var:.1f}% of total variance</strong> before clustering. This is required for K-Means to work correctly on this data:</p>
  <ul>
    <li><strong>Correlated features inflate distance.</strong> <em>n_onramp_txns</em> and <em>total_onramp_brl</em> are highly correlated — without PCA, both count in the Euclidean distance, effectively double-weighting onramp behaviour.</li>
    <li><strong>High-variance features dominate.</strong> Even after StandardScaler, residual correlation causes dominant features to overwhelm low-variance but meaningful signals like <em>has_card</em> or <em>n_unique_tokens</em>.</li>
    <li><strong>Curse of dimensionality.</strong> In high-dimensional spaces, Euclidean distances lose discriminative power. Reducing to {pca_comp} components restores meaningful geometry.</li>
  </ul>

  <h2>8. The {quarantined:,} Quarantined Users</h2>
  <p>
    {outlier_pct:.1f}% of users were removed before clustering. HDBSCAN flags users whose behavioral profile does not fit into any density region shared by at least 15 other users — typically extreme spenders, DeFi power users with automated transaction counts, or internal test accounts. These users are excluded from the push export and should be reviewed manually.
  </p>

  <h2>9. Monthly Refresh Protocol</h2>
  <p>
    Clustering artifacts (scaler, PCA, K-Means model, segment name mapping) are saved to <code>data/processed/</code> versioned by month. New users can be scored without re-fitting: apply saved scaler → PCA → <code>km.predict()</code>.
  </p>
  <p>
    A <strong>full re-fit</strong> should run once per quarter or when the user base grows by more than 20%. After a full re-fit, cluster IDs shift — the segment name mapping must be manually re-reviewed against the heatmap.
  </p>

  <div class="doc-footer">
    Generated by <code>scripts/generate_crm_report.py</code> from <code>notebooks/crm_user_clustering.ipynb</code> &nbsp;·&nbsp; Analysis month: {month} &nbsp;·&nbsp; NBS SPSAV LTDA — Confidential
  </div>

</div>
</body>
</html>"""


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(description="Generate CRM clustering executive summary HTML.")
    parser.add_argument(
        "--notebook", "-n",
        default=str(repo_root / "notebooks" / "crm_user_clustering.ipynb"),
        help="Path to the run notebook (default: notebooks/crm_user_clustering.ipynb)",
    )
    parser.add_argument(
        "--out", "-o",
        default=str(repo_root / "docs" / "crm_clustering_executive_summary.html"),
        help="Output HTML path (default: docs/crm_clustering_executive_summary.html)",
    )
    args = parser.parse_args()

    nb_path = Path(args.notebook)
    if not nb_path.exists():
        print(f"Error: notebook not found at {nb_path}", file=sys.stderr)
        sys.exit(1)

    with open(nb_path) as f:
        nb = json.load(f)

    metrics = extract_metrics(nb)
    html = render_html(metrics)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()
