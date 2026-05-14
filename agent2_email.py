"""
Property_Agent — Agent 2: Email Agent
Parses Agent 1 ranking, generates a professional PDF report,
sends a clean plain-text email with the PDF attached.

To run standalone: python agent2_email.py
Called automatically by orchestrator.py
"""

import os
import re
import sys
import threading
import itertools
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# reportlab for PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

load_dotenv()

# ── config — edit these ────────────────────────────────────────────────────────
ANALYSIS_FILE = "reports/latest_analysis.txt"
#RECIPIENTS    = ["your_recipient@email.com"]
# fix — reads only from env, exits if not set
_recipients = os.environ.get("RECIPIENTS")
if not _recipients:
    print("ERROR: RECIPIENTS not set. Add it to .env or GitHub Secrets.")
    sys.exit(1)
RECIPIENTS = [r.strip() for r in _recipients.split(",") if r.strip()]
SEND_EMAIL    = True
MODEL         = "gemini-2.5-flash"

# ── Gemini client ──────────────────────────────────────────────────────────────
_api_key = os.environ.get("GEMINI_API_KEY")
if not _api_key:
    print("ERROR: GEMINI_API_KEY not set. Add it to .env")
    sys.exit(1)

client = genai.Client(api_key=_api_key)


# ── Step 1: Parse ranking block from Agent 1 output ───────────────────────────
def parse_ranking(analysis_text):
    match = re.search(
        r"---RANKING_START---(.*?)---RANKING_END---",
        analysis_text, re.DOTALL
    )
    if not match:
        return {"raw": analysis_text, "ranks": [],
                "market_context": "", "final_recommendation": ""}

    block  = match.group(1).strip()
    result = {"raw": block, "ranks": [],
              "market_context": "", "final_recommendation": ""}

    rank_pattern = re.finditer(
        r"RANK_(\d+):\s*(.+?)\s*\|\s*SIGNAL:\s*(\w+)\s*\|\s*SCORE:\s*([\d.]+/[\d.]+)"
        r"\s*REASON:\s*(.+?)\s*RED_FLAGS:\s*(.+?)\s*POSITIVES:\s*(.+?)"
        r"(?=RANK_\d+:|MARKET_CONTEXT:|$)",
        block, re.DOTALL
    )
    for m in rank_pattern:
        result["ranks"].append({
            "rank":      int(m.group(1)),
            "address":   m.group(2).strip(),
            "signal":    m.group(3).strip(),
            "score":     m.group(4).strip(),
            "reason":    m.group(5).strip(),
            "red_flags": m.group(6).strip(),
            "positives": m.group(7).strip(),
        })

    mc = re.search(r"MARKET_CONTEXT:\s*(.+?)(?=FINAL_RECOMMENDATION:|$)", block, re.DOTALL)
    if mc:
        result["market_context"] = mc.group(1).strip()

    fr = re.search(r"FINAL_RECOMMENDATION:\s*(.+)", block, re.DOTALL)
    if fr:
        result["final_recommendation"] = fr.group(1).strip()

    return result


# ── Step 2: Generate PDF report ────────────────────────────────────────────────
def generate_pdf(ranking, analysis_text):
    Path("reports").mkdir(exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = Path(f"reports/investment_briefing_{ts}.pdf")

    # colours
    NAVY    = colors.HexColor("#0d3b6e")
    BLUE    = colors.HexColor("#1a56a0")
    LGRAY   = colors.HexColor("#f2f5f9")
    MGRAY   = colors.HexColor("#d0d9e6")
    DGRAY   = colors.HexColor("#444444")
    GREEN   = colors.HexColor("#1a7a4a")
    AMBER   = colors.HexColor("#b85c00")
    RED_C   = colors.HexColor("#a02020")
    WHITE   = colors.white
    BLACK   = colors.black

    signal_colors = {"GO": GREEN, "WATCH": AMBER, "PASS": RED_C}

    # styles
    styles = getSampleStyleSheet()

    def style(name, **kwargs):
        return ParagraphStyle(name, **kwargs)

    S = {
        "title":    style("title",    fontName="Helvetica-Bold", fontSize=22, textColor=NAVY,  leading=28, spaceAfter=4),
        "subtitle": style("subtitle", fontName="Helvetica",      fontSize=11, textColor=BLUE,  leading=16, spaceAfter=2),
        "meta":     style("meta",     fontName="Helvetica",      fontSize=9,  textColor=DGRAY, leading=13, spaceAfter=12),
        "h1":       style("h1",       fontName="Helvetica-Bold", fontSize=13, textColor=NAVY,  leading=18, spaceBefore=16, spaceAfter=6),
        "h2":       style("h2",       fontName="Helvetica-Bold", fontSize=11, textColor=BLUE,  leading=15, spaceBefore=12, spaceAfter=4),
        "body":     style("body",     fontName="Helvetica",      fontSize=9,  textColor=DGRAY, leading=14, spaceAfter=6),
        "bold":     style("bold",     fontName="Helvetica-Bold", fontSize=9,  textColor=DGRAY, leading=14, spaceAfter=4),
        "small":    style("small",    fontName="Helvetica",      fontSize=8,  textColor=DGRAY, leading=12, spaceAfter=4),
        "label":    style("label",    fontName="Helvetica-Bold", fontSize=8,  textColor=BLUE,  leading=12, spaceAfter=2),
        "rec":      style("rec",      fontName="Helvetica-Bold", fontSize=10, textColor=GREEN, leading=15, spaceAfter=6),
        "footer":   style("footer",   fontName="Helvetica",      fontSize=7,  textColor=DGRAY, alignment=TA_CENTER),
    }

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch,  bottomMargin=0.75*inch,
    )

    content_width = letter[0] - 1.5*inch
    story = []

    # ── header bar ─────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph("Property_Agent", style("hdr", fontName="Helvetica-Bold", fontSize=14, textColor=WHITE, leading=18)),
        Paragraph(f"Investment Briefing<br/>{datetime.now().strftime('%B %d, %Y')}", style("hdr2", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#b8d4f0"), leading=13, alignment=TA_RIGHT))
    ]]
    header_table = Table(header_data, colWidths=[content_width*0.6, content_width*0.4])
    header_table.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), NAVY),
        ("TOPPADDING",   (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0), (-1,-1), 10),
        ("LEFTPADDING",  (0,0), (-1,-1), 14),
        ("RIGHTPADDING", (0,0), (-1,-1), 14),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    # ── recommendation box ──────────────────────────────────────────────────────
    rec_text = ranking.get("final_recommendation", "See full analysis below.")
    rec_data = [[
        Paragraph("RECOMMENDATION", style("rl", fontName="Helvetica-Bold", fontSize=8, textColor=GREEN, leading=12)),
        Paragraph(rec_text, style("rt", fontName="Helvetica", fontSize=9, textColor=DGRAY, leading=14))
    ]]
    rec_table = Table(rec_data, colWidths=[1.2*inch, content_width - 1.2*inch])
    rec_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), LGRAY),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LINEBEFORE",    (0,0), (0,-1),  3, GREEN),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(rec_table)
    story.append(Spacer(1, 14))

    # ── ranking table ───────────────────────────────────────────────────────────
    story.append(Paragraph("Property Rankings", S["h1"]))
    story.append(HRFlowable(width=content_width, thickness=1, color=MGRAY, spaceAfter=8))

    rank_header = ["#", "Signal", "Address", "Score", "One-Line Reason"]
    col_widths  = [0.35*inch, 0.65*inch, 2.2*inch, 0.65*inch, content_width - 3.85*inch]

    rank_rows = [rank_header]
    for r in ranking.get("ranks", []):
        signal = r.get("signal", "")
        reason = r.get("reason", "")
        # trim reason to first sentence
        first_sentence = reason.split(".")[0].strip() + "." if "." in reason else reason[:120]
        rank_rows.append([
            str(r.get("rank", "")),
            signal,
            r.get("address", ""),
            r.get("score", ""),
            first_sentence
        ])

    rank_table = Table(rank_rows, colWidths=col_widths, repeatRows=1)

    # build style commands
    ts_cmds = [
        ("BACKGROUND",    (0,0),  (-1,0),  NAVY),
        ("TEXTCOLOR",     (0,0),  (-1,0),  WHITE),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,0),  8),
        ("TOPPADDING",    (0,0),  (-1,0),  6),
        ("BOTTOMPADDING", (0,0),  (-1,0),  6),
        ("FONTNAME",      (0,1),  (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1),  (-1,-1), 8),
        ("TOPPADDING",    (0,1),  (-1,-1), 5),
        ("BOTTOMPADDING", (0,1),  (-1,-1), 5),
        ("LEFTPADDING",   (0,0),  (-1,-1), 6),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 6),
        ("VALIGN",        (0,0),  (-1,-1), "TOP"),
        ("GRID",          (0,0),  (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS",(0,1),  (-1,-1), [WHITE, LGRAY]),
    ]

    # colour signal cells
    for i, r in enumerate(ranking.get("ranks", []), start=1):
        sig = r.get("signal", "")
        col = signal_colors.get(sig, DGRAY)
        ts_cmds.append(("TEXTCOLOR", (1, i), (1, i), col))
        ts_cmds.append(("FONTNAME",  (1, i), (1, i), "Helvetica-Bold"))

    rank_table.setStyle(TableStyle(ts_cmds))
    story.append(rank_table)
    story.append(Spacer(1, 14))

    # ── property detail cards ───────────────────────────────────────────────────
    story.append(Paragraph("Property Detail", S["h1"]))
    story.append(HRFlowable(width=content_width, thickness=1, color=MGRAY, spaceAfter=8))

    for r in ranking.get("ranks", []):
        signal    = r.get("signal", "WATCH")
        sig_color = signal_colors.get(signal, DGRAY)
        address   = r.get("address", "")
        score     = r.get("score", "")

        # card header
        card_hdr = [[
            Paragraph(f"#{r.get('rank')}  {address}", style("ch", fontName="Helvetica-Bold", fontSize=10, textColor=WHITE, leading=14)),
            Paragraph(f"{signal}  |  {score}", style("cs", fontName="Helvetica-Bold", fontSize=9, textColor=WHITE, leading=14, alignment=TA_RIGHT))
        ]]
        card_hdr_t = Table(card_hdr, colWidths=[content_width*0.7, content_width*0.3])
        card_hdr_t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), sig_color),
            ("TOPPADDING",    (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ("RIGHTPADDING",  (0,0), (-1,-1), 10),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]))

        # card body
        reason    = r.get("reason", "")
        red_flags = r.get("red_flags", "NONE")
        positives = r.get("positives", "")

        body_rows = [
            [Paragraph("Reason", S["label"]),    Paragraph(reason, S["body"])],
            [Paragraph("Positives", S["label"]), Paragraph(positives.replace("*", "").replace("\n", " | "), S["body"])],
        ]
        if red_flags.upper() not in ("NONE", "", "NONE IDENTIFIED"):
            body_rows.append([
                Paragraph("Red Flags", style("rfl", fontName="Helvetica-Bold", fontSize=8, textColor=RED_C, leading=12)),
                Paragraph(red_flags.replace("*", "").replace("\n", " | "), S["body"])
            ])

        body_table = Table(body_rows, colWidths=[1.0*inch, content_width - 1.0*inch])
        body_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), WHITE),
            ("LEFTPADDING",   (0,0), (-1,-1), 8),
            ("RIGHTPADDING",  (0,0), (-1,-1), 8),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
            ("LINEBELOW",     (0,0), (-1,-2), 0.5, MGRAY),
            ("BOX",           (0,0), (-1,-1), 0.5, MGRAY),
        ]))

        story.append(KeepTogether([card_hdr_t, body_table, Spacer(1, 10)]))

    # ── market context ──────────────────────────────────────────────────────────
    market = ranking.get("market_context", "")
    if market:
        story.append(Paragraph("Market Context", S["h1"]))
        story.append(HRFlowable(width=content_width, thickness=1, color=MGRAY, spaceAfter=8))

        mkt_data = [[Paragraph(market, S["body"])]]
        mkt_table = Table(mkt_data, colWidths=[content_width])
        mkt_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), LGRAY),
            ("LEFTPADDING",   (0,0), (-1,-1), 12),
            ("RIGHTPADDING",  (0,0), (-1,-1), 12),
            ("TOPPADDING",    (0,0), (-1,-1), 10),
            ("BOTTOMPADDING", (0,0), (-1,-1), 10),
            ("LINEBEFORE",    (0,0), (0,-1),  3, BLUE),
        ]))
        story.append(mkt_table)
        story.append(Spacer(1, 14))

    # ── scoring guide ───────────────────────────────────────────────────────────
    story.append(Paragraph("Scoring Methodology", S["h1"]))
    story.append(HRFlowable(width=content_width, thickness=1, color=MGRAY, spaceAfter=8))

    score_data = [
        ["Criterion", "Weight", "Logic"],
        ["Price per SF",           "30%", "Lower vs market benchmark = better value"],
        ["Building Class",         "25%", "A > B > C — higher class reduces operational risk"],
        ["Year Built / Renovated", "20%", "Newer or recently renovated = lower near-term capex"],
        ["Parking Ratio",          "15%", "Higher spaces per 1,000 SF = more flexible occupancy"],
        ["Lot Size",               "10%", "Larger lot = expansion and redevelopment potential"],
    ]
    score_col_widths = [2.0*inch, 0.8*inch, content_width - 2.8*inch]
    score_table = Table(score_data, colWidths=score_col_widths)
    score_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),  (-1,0),  NAVY),
        ("TEXTCOLOR",     (0,0),  (-1,0),  WHITE),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,-1), 8),
        ("FONTNAME",      (0,1),  (-1,-1), "Helvetica"),
        ("TOPPADDING",    (0,0),  (-1,-1), 5),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 5),
        ("LEFTPADDING",   (0,0),  (-1,-1), 7),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 7),
        ("GRID",          (0,0),  (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS",(0,1),  (-1,-1), [WHITE, LGRAY]),
        ("VALIGN",        (0,0),  (-1,-1), "MIDDLE"),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 14))

    # ── signals legend ──────────────────────────────────────────────────────────
    legend_data = [["GO", "Strong investment candidate — pursue immediately"],
                   ["WATCH", "Potential with caveats — gather more information"],
                   ["PASS", "Does not meet criteria — skip or deprioritize"]]
    legend_col_widths = [0.8*inch, content_width - 0.8*inch]
    legend_table = Table(legend_data, colWidths=legend_col_widths)
    legend_cmds = [
        ("FONTSIZE",      (0,0), (-1,-1), 8),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("GRID",          (0,0), (-1,-1), 0.5, MGRAY),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME",      (0,0), (-1,-1), "Helvetica"),
    ]
    for i, sig in enumerate(["GO", "WATCH", "PASS"]):
        legend_cmds.append(("TEXTCOLOR",  (0,i), (0,i), signal_colors[sig]))
        legend_cmds.append(("FONTNAME",   (0,i), (0,i), "Helvetica-Bold"))
        legend_cmds.append(("BACKGROUND", (0,i), (-1,i), LGRAY if i % 2 == 0 else WHITE))
    legend_table.setStyle(TableStyle(legend_cmds))
    story.append(legend_table)
    story.append(Spacer(1, 20))

    # ── footer ──────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width=content_width, thickness=0.5, color=MGRAY, spaceAfter=6))
    story.append(Paragraph(
        f"Generated by Property_Agent v2.0  |  {datetime.now().strftime('%B %d, %Y at %H:%M')}  |  "
        "Powered by Google Gemini 2.5 Flash + Google Search  |  "
        "Verify all figures independently before investment decisions.",
        S["footer"]
    ))

    doc.build(story)
    return pdf_path


# ── Step 3: Build plain-text email body ────────────────────────────────────────
def build_email_body(ranking):
    rec   = ranking.get("final_recommendation", "See attached report.")
    mkt   = ranking.get("market_context", "")
    ranks = ranking.get("ranks", [])
    date  = datetime.now().strftime("%B %d, %Y")

    lines = [
        f"Investment Briefing — {date}",
        "=" * 50,
        "",
        "RECOMMENDATION",
        rec,
        "",
        "PROPERTY RANKINGS",
        f"{'#':<3} {'Signal':<7} {'Score':<8} Address",
        "-" * 55,
    ]

    for r in ranks:
        lines.append(f"{r.get('rank'):<3} {r.get('signal',''):<7} {r.get('score',''):<8} {r.get('address','')}")

    lines += ["", "MARKET CONTEXT", mkt or "See attached report.", "",
              "Please open the attached PDF for full property detail,",
              "reasoning, red flags, and scoring methodology.", "",
              "—", "Property_Agent | Property Intelligence",
              "Powered by Google Gemini 2.5 Flash + Google Search",
              "Verify all figures independently before investment decisions."]

    return "\n".join(lines)


# ── Step 4: Send email with PDF attachment ─────────────────────────────────────
def send_with_attachment(subject, body_text, pdf_path, recipients):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")

    if not smtp_user or not smtp_pass:
        print("  SMTP not configured — add SMTP_USER and SMTP_PASSWORD to .env")
        return False

    try:
        msg            = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = ", ".join(recipients)

        # plain-text body
        msg.attach(MIMEText(body_text, "plain"))

        # PDF attachment
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={pdf_path.name}")
        msg.attach(part)

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f"  SMTP error: {e}")
        return False


# ── run — called by orchestrator ──────────────────────────────────────────────
def run(analysis_text, recipients, send=True):
    print(f"\n{'='*60}")
    print("AGENT 2 — EMAIL + PDF REPORT")
    print("="*60)

    print("  Parsing ranking from Agent 1 output...")
    ranking = parse_ranking(analysis_text)
    n = len(ranking["ranks"])
    print(f"  Found {n} ranked propert{'y' if n==1 else 'ies'}")
    if ranking["final_recommendation"]:
        print(f"  Recommendation: {ranking['final_recommendation'][:80]}...")

    # spinner while building PDF
    spinner_done  = threading.Event()
    spinner_chars = itertools.cycle(["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"])

    def spin(label):
        while not spinner_done.is_set():
            print(f"\r  {next(spinner_chars)} {label}", end="", flush=True)
            spinner_done.wait(0.1)
        print("\r" + " "*50 + "\r", end="", flush=True)

    print("\n  Generating PDF report...")
    spinner_done.clear()
    t = threading.Thread(target=spin, args=("Building PDF...",), daemon=True)
    t.start()
    try:
        pdf_path = generate_pdf(ranking, analysis_text)
    finally:
        spinner_done.set()
        t.join()
    print(f"  PDF saved: {pdf_path}")

    # build email
    subject    = f"Property Investment Briefing — {datetime.now().strftime('%B %d, %Y')}"
    body_text  = build_email_body(ranking)

    if send and recipients:
        print(f"\n  Sending to: {', '.join(recipients)}")
        spinner_done = threading.Event()
        t2 = threading.Thread(target=spin, args=("Sending email...",), daemon=True)
        t2.start()
        try:
            sent = send_with_attachment(subject, body_text, pdf_path, recipients)
        finally:
            spinner_done.set()
            t2.join()
        if sent:
            print("  Email sent with PDF attached")
        else:
            print("  Not sent — open PDF manually and forward")
    else:
        print("  Send skipped — PDF saved to reports/")

    return {"subject": subject, "body_plain": body_text, "pdf_path": str(pdf_path)}


# ── main flow — only when run directly ────────────────────────────────────────
analysis_text = Path(ANALYSIS_FILE).read_text(encoding="utf-8")
run(analysis_text, RECIPIENTS, send=SEND_EMAIL)