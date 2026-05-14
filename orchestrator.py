"""
Property_Agent — Orchestrator
Runs Agent 1 (analysis) then Agent 2 (email) in sequence.

Local run  : python orchestrator.py
GitHub Actions: runs automatically via analyze.yml schedule
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import guardrails

load_dotenv()

# ── config ─────────────────────────────────────────────────────────────────────
# reads from environment variable if set (GitHub Actions),
# otherwise falls back to the hardcoded default below

CSV_PATH   = os.environ.get("CSV_FILE",    "data/loopnet_listings.csv")
#RECIPIENTS = os.environ.get("RECIPIENTS",  "your_email@example.com").split(",")# if ok to hardcode recipients for testing, BEST PRACTICE : set in .env or GitHub Secrets
# fix — reads only from env, exits if not set
_recipients = os.environ.get("RECIPIENTS")
if not _recipients:
    print("ERROR: RECIPIENTS not set. Add it to .env or GitHub Secrets.")
    sys.exit(1)
RECIPIENTS = [r.strip() for r in _recipients.split(",") if r.strip()]
SEND_EMAIL = os.environ.get("SEND_EMAIL",  "true").lower() == "true"
SAVE_HTML  = True

# clean up any whitespace from split
RECIPIENTS = [r.strip() for r in RECIPIENTS if r.strip()]

# ── run ────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  Property_Agent — ORCHESTRATOR")
print(f"  {datetime.now().strftime('%B %d, %Y at %H:%M')}")
print("  Powered by Google Gemini 2.5 Flash (Free)")
print("="*60)
print(f"  CSV        : {CSV_PATH}")
print(f"  Recipients : {', '.join(RECIPIENTS)}")
print(f"  Send email : {'Yes' if SEND_EMAIL else 'No — preview only'}")
print(f"  HTML report: {'Yes' if SAVE_HTML else 'No'}")
print("="*60)

# ── guardrails ─────────────────────────────────────────────────────────────────
guardrails.run_all(csv_path=CSV_PATH, recipients=RECIPIENTS)

# ── Agent 1 ────────────────────────────────────────────────────────────────────
from agent1_analysis import load_and_enrich, analyze, save_report, print_table

guardrails.validate_csv_path(CSV_PATH)
df = load_and_enrich(CSV_PATH)
guardrails.validate_csv(df)

print(f"\n{'='*60}")
print("AGENT 1 — PROPERTY ANALYSIS")
print("="*60)
print(f"  {len(df)} properties loaded\n")
print_table(df)
print()

analysis = analyze(df)
analysis = guardrails.scrub_output(analysis)

Path("reports").mkdir(exist_ok=True)
ts            = datetime.now().strftime("%Y%m%d_%H%M%S")
analysis_file = Path(f"reports/analysis_{ts}.txt")
analysis_file.write_text(analysis, encoding="utf-8")
Path("reports/latest_analysis.txt").write_text(analysis, encoding="utf-8")
print(f"\n  Analysis saved: {analysis_file}")

if SAVE_HTML:
    report_path = save_report(df, analysis, CSV_PATH)
    print(f"  Report saved:   {report_path}")

# ── Agent 2 ────────────────────────────────────────────────────────────────────
from agent2_email import run as run_email

run_email(analysis, RECIPIENTS, send=SEND_EMAIL)

print("\n" + "="*60)
print("  COMPLETE")
print(f"  Agent 1 : Analysis done")
print(f"  Agent 2 : PDF + email {'sent' if SEND_EMAIL else 'preview only'}")
print(f"  Reports : reports/")
print("="*60 + "\n")