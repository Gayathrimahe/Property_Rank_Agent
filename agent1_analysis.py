"""
Property_Agent — Agent 1: Property Analysis
Loads LoopNet CSV, computes metrics, ranks properties via Gemini + Google Search.

To run: python agent1_analysis.py
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from tabulate import tabulate
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# ── config — edit these ────────────────────────────────────────────────────────
CSV_PATH    = "data/loopnet_listings.csv"
SAVE_REPORT = True
MODEL       = "gemini-2.5-flash"
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)

# ── Gemini client — created once and reused ────────────────────────────────────
_api_key = os.environ.get("GEMINI_API_KEY")
if not _api_key:
    print("ERROR: GEMINI_API_KEY not set. Add it to .env")
    sys.exit(1)

client = genai.Client(api_key=_api_key)


# ── load CSV and compute price per SF ─────────────────────────────────────────
def load_and_enrich(csv_path):
    df = pd.read_csv(csv_path)

    # clean column names
    df.columns = (df.columns
                  .str.strip()
                  .str.lower()
                  .str.replace(" ", "_")
                  .str.replace("/", "_"))

    # strip currency symbols, commas, text so pandas can do math
    numeric_cols = ["price", "asking_price", "building_size_sf", "total_sqft",
                    "price_per_sf", "typical_floor_size_sf", "lot_size_ac",
                    "building_height_stories"]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace("$", "", regex=False)
                       .str.replace(",", "", regex=False)
                       .str.replace(" SF", "", regex=False)
                       .str.replace(" sf", "", regex=False)
                       .str.strip())
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # find price column
    price_col = None
    for c in ["price", "asking_price"]:
        if c in df.columns:
            price_col = c
            break

    # find sqft column
    sqft_col = None
    for c in ["building_size_sf", "total_sqft"]:
        if c in df.columns:
            sqft_col = c
            break

    # price per sqft = price divided by building size
    if price_col and sqft_col:
        df["computed_price_per_sqft"] = (df[price_col] / df[sqft_col]).round(2)

    return df


# ── print preview table in terminal ───────────────────────────────────────────
def print_table(df):
    priority = [
        "date", "address", "neighborhood", "sale_type", "property_type",
        "building_size_sf", "building_class", "year_built_renovated",
        "price", "price_per_sf", "computed_price_per_sqft",
        "tenancy", "building_height_stories", "typical_floor_size_sf",
        "lot_size_ac", "zoning", "parking"
    ]
    cols = [c for c in priority if c in df.columns]
    print(tabulate(df[cols], headers="keys", tablefmt="github",
                   showindex=False, floatfmt=".2f"))


# ── build Gemini prompt ────────────────────────────────────────────────────────
def build_prompt(df):
    records      = df.where(pd.notnull(df), None).to_dict(orient="records")
    prop_type    = df["property_type"].iloc[0] if "property_type" in df.columns else "Commercial"
    neighborhood = df["neighborhood"].iloc[0]  if "neighborhood"  in df.columns else "the subject market"

    return f"""You are a senior commercial real estate investment analyst serving a data team.
Analyze these LoopNet property listings and produce a full investment ranking.

TODAY: {datetime.now().strftime("%B %d, %Y")}
PROPERTY TYPE: {prop_type}
MARKET AREA: {neighborhood}
SCORING MODE: Owner-user office — no NOI or rental income data available

PROPERTY DATA (with computed metrics):
{json.dumps(records, indent=2, default=str)}

SCORING WEIGHTS:
  Price per SF           : 30%  (lower vs market benchmark = better value)
  Building class         : 25%  (A > B > C)
  Year built / renovated : 20%  (newer or recently renovated = lower capex risk)
  Parking ratio          : 15%  (higher spaces per 1,000 SF = more flexible use)
  Lot size               : 10%  (larger lot = expansion potential)

STEP 1 — Search the web for:
  - Current {prop_type} prices and vacancy in {neighborhood} or nearest metro
  - Current SBA 504 and commercial mortgage rates
  - Any zoning or development news for this area

STEP 2 — Rank all {len(df)} properties. Use EXACTLY this format:

---RANKING_START---
RANK_1: [address] | SIGNAL: GO/WATCH/PASS | SCORE: X.X/3.0
REASON: [2-3 sentences with specific numbers]
RED_FLAGS: [bullet list or NONE]
POSITIVES: [bullet list]

RANK_2: [address] | SIGNAL: GO/WATCH/PASS | SCORE: X.X/3.0
REASON: [2-3 sentences with specific numbers]
RED_FLAGS: [bullet list or NONE]
POSITIVES: [bullet list]

RANK_3: [address] | SIGNAL: GO/WATCH/PASS | SCORE: X.X/3.0
REASON: [2-3 sentences with specific numbers]
RED_FLAGS: [bullet list or NONE]
POSITIVES: [bullet list]

MARKET_CONTEXT: [2-3 sentences from your search]
FINAL_RECOMMENDATION: [1 clear sentence]
---RANKING_END---

STEP 3 — Write a detailed narrative analysis with full reasoning.
"""


# ── call Gemini with Google Search grounding ───────────────────────────────────
def analyze(df):
    print("  Calling Gemini with Google Search grounding...")
    print("  Searching market data and computing rankings...\n")

    response = client.models.generate_content(
        model=MODEL,
        contents=build_prompt(df),
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
        )
    )

    print(response.text)
    return response.text


# ── save HTML report ───────────────────────────────────────────────────────────
def save_report(df, analysis, csv_path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path      = REPORTS_DIR / f"property_report_{timestamp}.html"

    price_col = None
    for c in ["price", "asking_price"]:
        if c in df.columns:
            price_col = c
            break

    sqft_col = None
    for c in ["building_size_sf", "total_sqft"]:
        if c in df.columns:
            sqft_col = c
            break

    rows = ""
    for _, r in df.iterrows():
        price = f"${r[price_col]:,.0f}"  if price_col and pd.notna(r.get(price_col)) else "—"
        sqft  = f"{r[sqft_col]:,.0f} SF" if sqft_col  and pd.notna(r.get(sqft_col))  else "—"
        ppsf  = f"${r['computed_price_per_sqft']:,.2f}" if pd.notna(r.get("computed_price_per_sqft")) else "—"
        year  = r.get("year_built_renovated", "—")
        if pd.isna(year):
            year = "—"

        rows += f"""<tr>
          <td>{r.get('address','—')}</td>
          <td>{r.get('neighborhood','—')}</td>
          <td>{price}</td><td>{sqft}</td>
          <td>{r.get('building_class','—')}</td>
          <td>{year}</td>
          <td>{r.get('zoning','—')}</td>
          <td>{r.get('parking','—')}</td>
          <td>{ppsf}</td>
        </tr>"""

    body = (analysis
            .replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            .replace("\n","<br>"))

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Property_Agent Report</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap');
  :root{{--bg:#0c0c0c;--sf:#161616;--bd:#252525;--tx:#e6e2d8;--mt:#5a5a5a;--ac:#c8a96e;--gr:#4caf7d}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--tx);font-family:'DM Sans',sans-serif;font-size:15px;line-height:1.75;padding:4rem 3rem;max-width:1100px;margin:0 auto}}
  .ey{{font-family:'DM Mono',monospace;font-size:11px;color:var(--ac);letter-spacing:.14em;text-transform:uppercase;margin-bottom:.5rem}}
  h1{{font-family:'DM Serif Display',serif;font-size:2.4rem;font-weight:400;margin-bottom:.4rem}}
  .meta{{font-family:'DM Mono',monospace;font-size:12px;color:var(--mt);padding-bottom:2rem;border-bottom:1px solid var(--bd);margin-bottom:3rem}}
  h2{{font-family:'DM Serif Display',serif;font-size:1.3rem;font-weight:400;color:var(--ac);margin:2.5rem 0 1rem}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:1rem}}
  th{{text-align:left;padding:10px 14px;background:var(--sf);color:var(--mt);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;border-bottom:1px solid var(--bd)}}
  td{{padding:10px 14px;border-bottom:1px solid var(--bd)}}
  tr:hover td{{background:var(--sf)}}
  .analysis{{background:var(--sf);border:1px solid var(--bd);border-left:3px solid var(--ac);padding:2rem 2.5rem;border-radius:4px;font-size:14px;line-height:1.9}}
  .legend{{font-size:12px;color:var(--mt);margin:.75rem 0 2rem;font-family:'DM Mono',monospace}}
  .footer{{margin-top:4rem;padding-top:1.5rem;border-top:1px solid var(--bd);font-size:12px;color:var(--mt);font-family:'DM Mono',monospace}}
  .tag{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;background:#1a2a1a;color:var(--gr)}}
</style></head><body>
<div class="ey">Property_Agent v2.0 — Investment Analysis</div>
<h1>Property Ranking &amp; Due Diligence</h1>
<div class="meta">Generated: {datetime.now().strftime("%B %d, %Y at %H:%M")} &nbsp;·&nbsp; Source: {Path(csv_path).name} &nbsp;·&nbsp; Properties: {len(df)}</div>
<h2>Property Comparison Table</h2>
<table><thead><tr>
  <th>Address</th><th>Neighborhood</th><th>Price</th><th>Size</th>
  <th>Class</th><th>Built/Reno</th><th>Zoning</th><th>Parking</th><th>$/SF</th>
</tr></thead><tbody>{rows}</tbody></table>
<div class="legend">$/SF = Price divided by Building Size &nbsp;·&nbsp; Owner-user office scoring applied</div>
<h2>Analysis, Ranking &amp; Recommendation</h2>
<div class="analysis">{body}</div>
<div class="footer">
  <span class="tag">Gemini-grounded</span> &nbsp;
  Powered by Gemini 2.5 Flash + Google Search &nbsp;·&nbsp; Property_Agent v2.0 &nbsp;·&nbsp;
  Verify all figures independently before investment decisions.
</div>
</body></html>"""

    path.write_text(html, encoding="utf-8")
    return path


# ── main flow — runs top to bottom ────────────────────────────────────────────
print("\n" + "="*60)
print("AGENT 1 — PROPERTY ANALYSIS")
print("="*60)
print(f"  Loading: {CSV_PATH}")

df       = load_and_enrich(CSV_PATH)
print(f"  {len(df)} properties loaded\n")
print_table(df)
print()

analysis = analyze(df)

if SAVE_REPORT:
    report_path = save_report(df, analysis, CSV_PATH)
    print(f"\n  Report saved: {report_path}")