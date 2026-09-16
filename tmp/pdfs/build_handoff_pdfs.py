from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

OUT = Path(__file__).resolve().parents[2] / "output" / "pdf"
OUT.mkdir(parents=True, exist_ok=True)
S = getSampleStyleSheet()
def foot(c, d):
    c.saveState(); c.setFont("Helvetica", 8); c.drawString(18*mm, 12*mm, "ECS external engine - paper-replay research only"); c.drawRightString(192*mm, 12*mm, f"Page {d.page}"); c.restoreState()
def tbl(rows):
    t=Table(rows,repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#12355B")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.25,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)])); return t
def pdf(path, story):
    SimpleDocTemplate(str(path),pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm).build(story,onFirstPage=foot,onLaterPages=foot)

pdf(OUT/"ECS_Closed_Loop_Architecture_V3.pdf",[
 Paragraph("ECS Closed-Loop Architecture V3",S["Title"]),
 Paragraph("Goal: establish whether a cost-adjusted intraday edge exists on manifest-verified NSE data. Paper research only; no live-trading approval.",S["BodyText"]),Spacer(1,8),
 tbl([["Loop","Setpoint","Measurement","Output","Hard boundary"],["Entry quality","Cost-aware break-even probability","Completed earlier symbol/side outcomes","Entry derate 0 to 1","Never raises risk"],["Trade path","Frozen per-trade R path","Current R on completed held bars","One-way stop ratchet","Never loosens stop"],["Portfolio risk","Heat and drawdown budget","Exposure and drawdown","New-risk derate/halt","Never forces deployment"]]),
 Spacer(1,10),Paragraph("Data flow",S["Heading2"]),Paragraph("Completed bars -> features -> candidate -> entry-quality feedback -> paper trade -> trade-path feedback -> protective exit -> portfolio feedback -> completed outcome -> later candidates only.",S["BodyText"]),
 Spacer(1,10),Paragraph("Promotion rule",S["Heading2"]),Paragraph("A fixed alpha needs at least 30 resolved candidates and positive gross and net results in train, validation and untouched test. Passing permits only a separate closed-loop shadow test.",S["BodyText"])
])
pdf(OUT/"ECS_Sealed_Research_Results_2023Q4.pdf",[
 Paragraph("ECS Sealed Intraday Research Results",S["Title"]),
 Paragraph("48-symbol manifest-verified paper research. Per-share shadow aggregates; not live results.",S["BodyText"]),Spacer(1,8),
 Paragraph("Breakout continuation",S["Heading2"]),tbl([["Window","Candidates","Target-first","Gross/share","Net/share","Verdict"],["Train Sep","15,779","22.33%","-28,373","-53,619","Rejected"],["Validation Oct","15,386","20.89%","-28,827","-53,660","Rejected"],["Untouched test Nov","15,112","19.44%","-28,723","-52,989","Rejected"]]),
 Spacer(1,10),Paragraph("Breakout retest",S["Heading2"]),tbl([["Window","Candidates","Target-first","Gross/share","Net/share","Verdict"],["Train Sep","7,304","20.06%","-13,426","-24,879","Rejected"],["Validation Oct","7,075","19.22%","-13,245","-24,462","Rejected"],["Untouched test Nov","7,162","16.14%","-14,103","-25,497","Rejected"]]),
 Spacer(1,10),Paragraph("Conclusion",S["Heading2"]),Spacer(1,4),Paragraph("Immediate adverse selection is the largest measured loss cohort. Both continuation hypotheses are rejected. Current work: frozen feature separability for volume ratio, breakout extension, VWAP distance and EMA slope; thresholds are trained only on September and applied unchanged to validation and untouched test.",S["BodyText"])
])
