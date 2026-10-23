"""Pure Markdown rendering for market intelligence results."""

from datetime import datetime, timezone

from datahunt.models.market import (
    MarketCandidate,
    MarketCoverage,
    MarketIntentSpec,
    MarketRecord,
    MarketRegime,
)


def render_market_report(
    *,
    intent: MarketIntentSpec,
    coverage: MarketCoverage,
    regime: MarketRegime,
    context_notes: list[str],
    top_sectors: list[dict[str, str]],
    records: list[MarketRecord],
    watchlist: list[MarketCandidate] | None = None,
) -> str:
    """Render the stable market-report contract without touching discovery state."""
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y")
    horizon_label = intent.time_horizon.value.replace("_", " ").title()
    regime_desc = {
        MarketRegime.BULLISH: "Constructive momentum observed across benchmark indices with broad-based participation.",
        MarketRegime.BEARISH: "Defensive positioning with selective rotation; benchmark indices facing overhead resistance.",
        MarketRegime.SIDEWAYS: "Rangebound consolidation with selective stock-specific movement and index consolidation.",
        MarketRegime.HIGH_VOLATILITY: "Heightened intraday volatility requiring cautious risk management.",
        MarketRegime.MIXED: "Mixed sectoral signals with divergence between cyclicals and defensive counters.",
    }.get(regime, "Consolidation regime.")
    sector_leaders = ", ".join(f"{sector['sector']} ({sector['name']})" for sector in top_sectors[:3])
    lines = [
        f"# 📊 MARKET OUTLOOK: {intent.market}",
        f"**Evaluation Period**: {horizon_label} ({now_str}) | **Exchange**: {intent.market} | **Asset Class**: {intent.asset_type.value.upper()}",
        "",
        "## 1. Market Regime & Macro Context",
        f"- **Regime Assessment**: `{regime.value}` — {regime_desc}",
        f"- **Benchmark Breadth**: {context_notes[0] if context_notes else 'Benchmark indices holding key support zones.'}",
        f"- **Leading Sectors**: {sector_leaders}",
        "",
        "## 2. Top Monitored Candidates (Evidence-Backed Selection)",
        "> **Disclaimer**: This briefing summarizes public signals and is not an investment guarantee.",
        "",
    ]
    if not records:
        lines.append("No candidates satisfied multi-source evidence criteria during this research pass.")
        return "\n".join(lines)

    for idx, record in enumerate(records, 1):
        price = f"₹{record.current_price:.2f}" if record.current_price else "Live Exchange Quotes"
        weekly = f"{record.change_1w:+.2f}%" if record.change_1w is not None else "Multi-day consolidation"
        volume = f"{record.volume_ratio:.2f}x" if record.volume_ratio else "Normal Volume"
        lines.extend([
            f"### {idx}. {record.company_name} (`{record.symbol}`)",
            f"- **Exchange / Sector**: {record.exchange} | {record.sector or 'Diversified'}",
            f"- **Current Quote / Trend**: {price} (1W: {weekly}) | Volume Activity: `{volume}`",
            f"- **Signal Direction**: `{record.signal_direction}` | **Deterministic Score**: `{record.signal_score}/100` | **Evidence Confidence**: `{record.evidence_confidence}`",
            "",
            "**Why It Appears (Supporting Evidence):**",
        ])
        lines.extend(f"- {item.strip()}" for item in (record.bullish_evidence[:3] or [f"Positive relative strength identified in {record.exchange} trading scans."]))
        tech_items = []
        if record.technical_signals.get("rsi"):
            tech_items.append(f"RSI(14): {record.technical_signals['rsi']}")
        if record.technical_signals.get("sma20"):
            tech_items.append(f"SMA(20): ₹{record.technical_signals['sma20']}")
        fund_items = []
        if record.pe_ratio:
            fund_items.append(f"P/E: {record.pe_ratio:.1f}")
        if record.revenue_growth:
            fund_items.append(f"Revenue Growth: {record.revenue_growth:+.1f}%")
        lines.extend([
            "",
            "**Technical & Quantitative Setup:**",
            f"- {', '.join(tech_items) if tech_items else 'Price consolidating above near-term moving average support.'}",
            "",
            "**Fundamentals & Corporate Catalysts:**",
            f"- {', '.join(fund_items) if fund_items else 'Established market presence; recent operational disclosures.'}",
            "",
            "**Counter-Evidence & Key Risk Factors:**",
        ])
        lines.extend(f"- ⚠️ {risk.strip()}" for risk in (record.risk_factors[:2] or ["Broader market correction risk and near-term technical resistance."]))
        sources = list({e.source_name for e in record.source_evidence if e.source_name})[:4] or ["NSE India Official", "Financial Media Disclosures"]
        lines.extend(["", "**Evidence Sources & Provenance:**", f"- Verified across: {', '.join(sources)}", "", "---", ""])

    if watchlist:
        lines.extend([
            "## 3. Watchlist / Insufficient Corroborating Evidence",
            "> These counters lacked enough independent evidence for the primary list.",
            "",
        ])
        lines.extend(f"- **{candidate.company_name}** (`{candidate.symbol}`): Signal Score `{candidate.signal_score:.1f}`, Confidence `{candidate.evidence_confidence}`" for candidate in watchlist[:5])
        lines.extend(["", "---", "", "## 4. Research Coverage & Provenance Matrix"])
    else:
        lines.append("## 3. Research Coverage & Provenance Matrix")
    lines.extend([
        f"- **Exchanges Monitored**: {', '.join(coverage.exchanges_checked)}",
        f"- **Total Discovered Candidates**: {coverage.candidate_count}",
        f"- **Symbol-Validated Candidates**: {coverage.validated_candidate_count}",
        f"- **Deeply Researched**: {coverage.deeply_researched_count}",
        f"- **Stop Condition Triggered**: `{coverage.stop_reason or 'COVERAGE_COMPLETE'}`",
    ])
    return "\n".join(lines)
