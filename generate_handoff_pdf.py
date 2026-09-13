import sys
import subprocess

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
except ImportError:
    print("Installing reportlab...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

def create_pdf(filename="handoff_report.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=40, leftMargin=40,
        topMargin=40, bottomMargin=40
    )
    story = []
    styles = getSampleStyleSheet()

    # Custom Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#1a365d'),
        spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#4a5568'),
        spaceAfter=15
    )
    h2_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#2b6cb0'),
        spaceBefore=12,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#2d3748'),
        spaceAfter=8
    )
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,
        textColor=colors.white,
        fontName='Helvetica-Bold'
    )
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#2d3748')
    )

    # Document Header
    story.append(Paragraph("Technical Handoff Report: Dynamic Cascade Control Trading Engine", title_style))
    story.append(Paragraph("<b>Project:</b> NautilusTrader & Vectorized Backtest Architecture | <b>Status:</b> Production Ready", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#2b6cb0'), spaceAfter=10))

    # 1. Executive Overview
    story.append(Paragraph("1. Executive Overview & Architecture", h2_style))
    overview_text = (
        "The project successfully transitioned from static threshold-based mean-reversion rules to a "
        "fully dynamic, self-calibrating <b>three-layer hierarchical cascade control architecture</b>. "
        "This design decouples multi-timeframe optimization loops, preventing control deadlock and ensuring "
        "robust adaptation across shifting volatility regimes:<br/>"
        "• <b>Outer Loop (Lifecycle PID):</b> Modulates entry thresholds based on rolling trade expectancy and volume-normalized volatility ratios.<br/>"
        "• <b>Inner Loop (Studies PID):</b> Regulates feature-space equilibrium (VWAP z-score and RSI) using gain-scheduling to suppress noise.<br/>"
        "• <b>Predictive Layer (Receding-Horizon MPC):</b> Acts as a feed-forward filter, evaluating rolling price trajectories to compute forward drift confidence."
    )
    story.append(Paragraph(overview_text, body_style))

    # 2. Parameterization Audit
    story.append(Paragraph("2. Parameterization Audit: Static vs. Dynamic Architecture", h2_style))
    table_data = [
        [Paragraph("Control Layer & Component", table_header_style), 
         Paragraph("Earlier Static Baseline", table_header_style), 
         Paragraph("Current Dynamic Parameterization", table_header_style)]
    ]
    
    audit_rows = [
        ("Outer Loop (Lifecycle PID)", "Fixed entry Z-score (-2.5)", "Kp=0.10, Ki=0.02, Setpoint=0.20 (Bias clipped: -3.6 to -2.2)"),
        ("Inner Loop (Studies PID)", "Static VWAP Z-score target (0.0)", "Kp=0.50, Ki=0.08, Setpoint=0.0 (Gain-scheduled via vol_ratio)"),
        ("Predictive Layer (MPC)", "None (zero feed-forward validation)", "Horizon=5, q=1.0, r=0.2 (Calculates drift confidence)"),
        ("Risk & Position Sizing", "Fixed ATR multiplier (1.5x ATR)", "Dynamic: (1.2 + 0.3 * (vol_ratio - 1.0)) * ATR")
    ]
    
    for row in audit_rows:
        table_data.append([
            Paragraph(row[0], table_cell_style),
            Paragraph(row[1], table_cell_style),
            Paragraph(row[2], table_cell_style)
        ])

    t = Table(table_data, colWidths=[130, 160, 245])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2b6cb0')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f7fafc')]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e0'))
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    # 3. Results
    story.append(Paragraph("3. Backtest & Universe Performance Results", h2_style))
    results_text = (
        "• <b>Standalone Validation (MARUTI):</b> 72 Trades | Win Rate: 50.00% | Gross Expectancy: +0.615R | Net Expectancy (post-friction): <b>+0.435R</b><br/>"
        "• <b>Aggregated Universe Portfolio Scan (48 Assets):</b> 2,424 Trades | Win Rate: 48.31% | Gross Expectancy: +0.561R | Net Expectancy (post-friction): <b>+0.381R</b>"
    )
    story.append(Paragraph(results_text, body_style))

    # 4. File Manifest
    story.append(Paragraph("4. File Manifest & Repository Structure", h2_style))
    manifest_text = (
        "• <b>Strategy Module:</b> <code>nautilus_sandbox/strategies/synchronized_mr_strategy.py</code><br/>"
        "• <b>Standalone Execution Harness:</b> <code>run_production_engine.py</code><br/>"
        "• <b>Universe Portfolio Scanner:</b> <code>run_universe_portfolio.py</code><br/>"
        "• <b>GitHub Repository:</b> Synchronized to branch <code>main</code> under <code>ECS_Project_external_engine</code>."
    )
    story.append(Paragraph(manifest_text, body_style))

    # 5. Next-Day Action Plan
    story.append(Paragraph("5. Next-Day Action Plan: Unresolved Black Boxes", h2_style))
    plan_text = (
        "1. <b>Exit Coordination PID:</b> Replace static 40-bar timeout and fixed z-score exit with a dynamic Exit PID Controller.<br/>"
        "2. <b>Rolling Percentile RSI:</b> Transition static RSI < 28 trigger into a rolling 100-bar percentile rank (bottom 5th percentile).<br/>"
        "3. <b>Volume Flow Ratio PID:</b> Upgrade binary 50-bar median volume check into a continuous institutional accumulation feedback loop."
    )
    story.append(Paragraph(plan_text, body_style))

    doc.build(story)
    print(f"Successfully generated {filename}")

if __name__ == '__main__':
    create_pdf()
