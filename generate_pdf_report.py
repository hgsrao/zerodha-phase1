#!/usr/bin/env python3
"""Generate PDF report for Zerodha Phase 1 External Engines"""

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Preformatted
from reportlab.lib import colors
from datetime import datetime

def create_pdf_report():
    """Create comprehensive PDF report of external engines"""

    filename = "Zerodha_Phase1_External_Engines_Report.pdf"
    doc = SimpleDocTemplate(filename, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)

    # Define styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a73e8'),
        spaceAfter=12,
        fontName='Helvetica-Bold'
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#1a73e8'),
        spaceAfter=8,
        spaceBefore=12,
        fontName='Helvetica-Bold'
    )

    subheading_style = ParagraphStyle(
        'Subheading',
        parent=styles['Heading3'],
        fontSize=11,
        textColor=colors.HexColor('#202124'),
        spaceAfter=6,
        spaceBefore=6,
        fontName='Helvetica-Bold'
    )

    normal_style = ParagraphStyle(
        'Custom',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6
    )

    # Build content
    story = []

    # Title Page
    story.append(Paragraph("🔧 Zerodha Phase 1", title_style))
    story.append(Paragraph("External Engines & Systems Report", styles['Heading2']))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
    story.append(Paragraph("Project Location: /home/user/zerodha-phase1", styles['Normal']))
    story.append(Paragraph("Total Files: 697", styles['Normal']))
    story.append(Spacer(1, 20))

    # Executive Summary
    story.append(Paragraph("Executive Summary", heading_style))
    story.append(Paragraph(
        "This report documents all external engines, cloud services, and third-party systems "
        "integrated into the Zerodha Phase 1 trading project. The system comprises 8 live trading "
        "instances, multiple calibration and backtesting engines, and supporting infrastructure.",
        normal_style
    ))
    story.append(Spacer(1, 12))

    # Important Note
    note_style = ParagraphStyle('Note', parent=styles['Normal'], fontSize=9,
                               textColor=colors.HexColor('#9c27b0'),
                               leftIndent=10, rightIndent=10)
    story.append(Paragraph(
        "<b>IMPORTANT:</b> All engines are read-only/paper trading. LIVE_TRADING_ENABLED is False everywhere. "
        "No order-placement code exists in production paths.",
        note_style
    ))
    story.append(Spacer(1, 20))
    story.append(PageBreak())

    # 1. Cloud Infrastructure
    story.append(Paragraph("1. Cloud Infrastructure", heading_style))
    story.append(Paragraph("AWS EC2", subheading_style))
    cloud_data = [
        ["Property", "Value"],
        ["Purpose", "Distributed calibration for parameter optimization"],
        ["Instance Type", "c6i.4xlarge (16 CPU cores)"],
        ["Time Savings", "Reduces 24h calibration → 45 min"],
        ["Cost", "~$5 (~₹500) per run"],
        ["Documentation", "AWS_CALIBRATION_GUIDE_STEPBYSTEP_20260830.md"],
    ]
    cloud_table = Table(cloud_data, colWidths=[2*inch, 4*inch])
    cloud_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d1ecff')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a73e8')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    story.append(cloud_table)
    story.append(Spacer(1, 20))

    # 2. Data Storage
    story.append(Paragraph("2. Data Storage & Caching", heading_style))
    story.append(Paragraph("Redis", subheading_style))
    redis_items = [
        "Real-time state management and circuit breaker logic",
        "Trading state (allowed/halted)",
        "Daily loss tracking",
        "Maximum drawdown tracking",
        "Volatility and correlation monitoring",
        "Stress factor management"
    ]
    for item in redis_items:
        story.append(Paragraph(f"• {item}", normal_style))
    story.append(Spacer(1, 20))

    # 3. Broker Integration
    story.append(Paragraph("3. Broker Integration", heading_style))
    story.append(Paragraph("Zerodha Kite API", subheading_style))
    broker_items = [
        "Indian stock market (NSE - National Stock Exchange)",
        "Live market data streaming",
        "Order placement (paper trading mode)",
        "Position management",
        "Account info and holdings",
        "Daily token refresh via get_kite_access_token.ps1"
    ]
    for item in broker_items:
        story.append(Paragraph(f"• {item}", normal_style))
    story.append(Spacer(1, 20))
    story.append(PageBreak())

    # 4. Data Analytics & ML
    story.append(Paragraph("4. Data Analytics & ML Libraries", heading_style))
    ml_data = [
        ["Library", "Purpose", "Usage"],
        ["NumPy", "Numerical computing", "Matrix operations, statistics"],
        ["Pandas", "Data frames", "OHLCV manipulation, backtesting"],
        ["Scikit-learn", "Machine learning", "Feature scaling, training"],
        ["XGBoost", "Gradient boosting", "Parameter optimization"],
        ["CVXPY", "Convex optimization", "Portfolio optimization"],
        ["CVXPortfolio", "Portfolio analytics", "Cost modeling, risk analysis"],
        ["TA-Lib", "Technical indicators", "ATR, RSI, MACD calculations"],
    ]
    ml_table = Table(ml_data, colWidths=[1.5*inch, 2*inch, 2.5*inch])
    ml_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d9ead3')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#4a7c2a')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    story.append(ml_table)
    story.append(Spacer(1, 20))

    # 5. Core Trading Engines
    story.append(Paragraph("5. Core Trading Engines", heading_style))
    story.append(Paragraph("Institutional Engine V3.4 (Variants)", subheading_style))
    engine_items = [
        "V3.4 - Baseline production version",
        "V3.4 B12/B13 - Resilience variants",
        "V3.4 P01D - Sovereign authorization",
        "V3.4 P02 - Multi-position candidate",
    ]
    for item in engine_items:
        story.append(Paragraph(f"• {item}", normal_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Key Components:", subheading_style))
    components = [
        "Trade execution pipeline",
        "Order status tracking",
        "Position management",
        "P&L calculation",
        "Risk management"
    ]
    for comp in components:
        story.append(Paragraph(f"• {comp}", normal_style))
    story.append(Spacer(1, 20))
    story.append(PageBreak())

    # 6. Calibration
    story.append(Paragraph("6. Calibration Engines", heading_style))
    story.append(Paragraph("Parameter Optimization", subheading_style))
    story.append(Paragraph("Parameters Optimized: 33 total", normal_style))

    calib_items = [
        "Entry and exit thresholds",
        "Position sizing rules",
        "Risk limits",
        "ATR scaling factors"
    ]
    for item in calib_items:
        story.append(Paragraph(f"• {item}", normal_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Optimization Methods:", subheading_style))
    methods = [
        "Walk-forward analysis",
        "Bootstrap sampling",
        "Cross-sectional optimization",
        "Risk-adjusted metrics"
    ]
    for method in methods:
        story.append(Paragraph(f"• {method}", normal_style))
    story.append(Spacer(1, 20))

    # 7. Backtesting
    story.append(Paragraph("7. Backtesting Engines", heading_style))
    story.append(Paragraph("Multi-Period & Historical Testing", subheading_style))
    story.append(Paragraph("Test Periods Supported:", normal_style))

    periods = ["1 Month (M)", "1 Year (Y)", "2 Years (2Y)", "3 Years (3Y)"]
    for period in periods:
        story.append(Paragraph(f"• {period}", normal_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Metrics Calculated:", subheading_style))
    metrics = [
        "P&L (Realized, Unrealized, MTM)",
        "Win Rate / Sharpe Ratio",
        "Drawdown Analysis",
        "Transaction Costs",
        "Risk-Adjusted Returns"
    ]
    for metric in metrics:
        story.append(Paragraph(f"• {metric}", normal_style))
    story.append(Spacer(1, 20))
    story.append(PageBreak())

    # 8. Live Trading Engines
    story.append(Paragraph("8. Live Trading Engines (8 Instances)", heading_style))

    live_engines_data = [
        ["#", "Engine Name", "Universe", "Mode"],
        ["1", "Chart Studies Monitor", "5 symbols", "Live"],
        ["2", "P02 Live Scan", "Nifty 50", "Live"],
        ["3", "Read-Only Shadow", "Cross-sectional 12_1", "Shadow"],
        ["4", "ORB Shadow Collector", "ORB scan", "Shadow"],
        ["5", "P01D Entry Gate", "V3.4 auth pipeline", "Dry-run"],
        ["6", "Dashboard Daemon", "3 dashboards", "Monitor"],
        ["7", "V11 Bridge - Terminal A", "19 symbols", "Shadow"],
        ["8", "V11 Bridge - Terminal B", "50 symbols", "Shadow"],
    ]
    live_table = Table(live_engines_data, colWidths=[0.4*inch, 2*inch, 2*inch, 1.5*inch])
    live_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f4cccc')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#9c27b0')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    story.append(live_table)
    story.append(Spacer(1, 20))

    # 9. Monitoring
    story.append(Paragraph("9. Monitoring & Logging", heading_style))
    story.append(Paragraph("Observability", subheading_style))

    monitor_items = [
        "v34_observatory_v3.py - Real-time observation system v3",
        "v34_observatory_v4.py - Enhanced observation system v4",
        "Local HTML dashboards (auto-refresh every 2 min)",
        "Alert aggregation and audit logging",
    ]
    for item in monitor_items:
        story.append(Paragraph(f"• {item}", normal_style))
    story.append(Spacer(1, 20))
    story.append(PageBreak())

    # 10. Summary Table
    story.append(Paragraph("10. Summary: External Engines by Category", heading_style))

    summary_data = [
        ["Category", "Engine/Service", "Key Library/Tool"],
        ["Cloud Computing", "AWS EC2", "c6i.4xlarge (16 cores)"],
        ["Caching/State", "Redis", "redis-py"],
        ["Broker", "Zerodha Kite API", "NSE exchange"],
        ["Data Analysis", "NumPy, Pandas", "Numerical computing"],
        ["ML/Optimization", "Scikit-learn, XGBoost, CVXPY", "Advanced tuning"],
        ["Technical Analysis", "TA-Lib", "ATR, RSI, MACD"],
        ["Trading Core", "Institutional Engine V3.4", "Order execution"],
        ["Backtesting", "Custom engines", "Multi-period validation"],
    ]
    summary_table = Table(summary_data, colWidths=[1.8*inch, 2*inch, 2.2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Important Notes
    story.append(Paragraph("Important Notes & Requirements", heading_style))

    important_notes = [
        ("<b>Paper Trading Only:</b> All engines run in paper/shadow mode. No live money deployed.", "✅"),
        ("<b>Daily Token Refresh:</b> Zerodha Kite API tokens expire daily. Must run get_kite_access_token.ps1 every morning.", "📋"),
        ("<b>Duplicate Prevention:</b> If the same engine script runs twice, silent file corruption occurs. Always verify running processes.", "🔒"),
        ("<b>Separate Windows Required:</b> Engines 1-5 and 7-8 each need their own PowerShell window with KITE_ACCESS_TOKEN set.", "🪟"),
        ("<b>EOD Handling:</b> Each engine handles its own end-of-day statement automatically after 15:30 IST.", "📊"),
    ]

    for note, emoji in important_notes:
        story.append(Paragraph(f"{emoji} {note}", normal_style))
    story.append(Spacer(1, 20))

    story.append(PageBreak())

    # Research Variants
    story.append(Paragraph("Research & Strategy Variants", heading_style))

    story.append(Paragraph("Walk-Forward Strategies:", subheading_style))
    walkforward = [
        "Anchored Walk-Forward (V10)",
        "Momentum Walk-Forward (V11, V12)",
        "Extended History Validation (V13)"
    ]
    for item in walkforward:
        story.append(Paragraph(f"• {item}", normal_style))

    story.append(Spacer(1, 12))
    story.append(Paragraph("Risk-Adjusted Variants:", subheading_style))
    risk_variants = [
        "V14 - Realistic Costs",
        "V15 - Risk Config Realignment",
        "V16 - Position Count Sweep",
        "V17 - Multi-Position CNC Engine"
    ]
    for item in risk_variants:
        story.append(Paragraph(f"• {item}", normal_style))
    story.append(Spacer(1, 20))

    # Conclusion
    story.append(Paragraph("Conclusion", heading_style))
    story.append(Paragraph(
        "The Zerodha Phase 1 project integrates a comprehensive ecosystem of external engines and systems "
        "for trading, calibration, and analysis. The architecture leverages cloud computing for fast calibration, "
        "Redis for state management, Zerodha's Kite API for market access, and Python's scientific stack for analysis. "
        "With 8 live trading instances running in shadow/paper mode and multiple backtesting frameworks, the system "
        "provides robust validation and monitoring capabilities for algorithmic trading strategy development.",
        normal_style
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"<i>Report generated: {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}</i>",
        styles['Normal']
    ))

    # Build PDF
    doc.build(story)
    print(f"✅ PDF Report generated: {filename}")
    return filename

if __name__ == "__main__":
    create_pdf_report()
