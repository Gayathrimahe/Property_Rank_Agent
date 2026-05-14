"""
Property_Agent — Guardrails
===========================
Safety checks applied before any agent runs:

1. API key format validation — catches accidental hardcoding or placeholder text
2. CSV input validation — checks required columns, row limits, data types
3. Email validation — checks recipient addresses are real format
4. Prompt content check — blocks prompt injection attempts in CSV data
5. Output content check — strips any accidentally leaked key patterns from responses
6. Rate limit guard — prevents accidental runaway API calls
"""

import os
import re
import sys
from pathlib import Path


# ── 1. API Key Guardrail ───────────────────────────────────────────────────────
PLACEHOLDER_PATTERNS = [
    "your-key-here", "your_key_here", "sk-ant-your",
    "AIza-your", "paste-key", "add-key", "insert-key",
    "xxxxxxxx", "changeme", "placeholder"
]

def validate_api_key(key: str, name: str = "GEMINI_API_KEY") -> None:
    """
    Blocks common mistakes:
    - Key is empty or None
    - Key looks like a placeholder (copy-paste error)
    - Key is hardcoded in source (caught by checking it came from env)
    """
    if not key or not key.strip():
        print(f"ERROR [{name}]: API key is empty. Set it in your .env file.")
        sys.exit(1)

    key_lower = key.lower()
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern in key_lower:
            print(f"ERROR [{name}]: Looks like a placeholder value '{key[:20]}...'")
            print("  Get your real key from: aistudio.google.com")
            sys.exit(1)

    # Gemini keys start with AIza
    if name == "GEMINI_API_KEY" and not key.startswith("AIza"):
        print(f"ERROR [{name}]: Gemini API keys start with 'AIza'. Got: {key[:6]}...")
        sys.exit(1)

    print(f"  ✅ {name} validated")


# ── 2. CSV Input Guardrail ─────────────────────────────────────────────────────
REQUIRED_COLUMNS = {"address", "price"}
MAX_ROWS         = 50   # prevents sending huge prompts accidentally

def validate_csv(df) -> None:
    """
    Checks:
    - Required columns exist
    - Not too many rows (cost + prompt size protection)
    - No prompt injection patterns in text fields
    """
    import pandas as pd

    # check required columns
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        print(f"ERROR [CSV]: Missing required columns: {missing}")
        print(f"  Found columns: {list(df.columns)}")
        sys.exit(1)

    # row limit
    if len(df) > MAX_ROWS:
        print(f"ERROR [CSV]: {len(df)} rows exceeds max of {MAX_ROWS}.")
        print("  Split your CSV into smaller batches.")
        sys.exit(1)

    # prompt injection check — look for suspicious patterns in text columns
    text_cols = df.select_dtypes(include="object").columns
    injection_patterns = [
        r"ignore\s+previous\s+instructions",
        r"ignore\s+all\s+instructions",
        r"you\s+are\s+now",
        r"system\s*prompt",
        r"<\s*script",
        r"JAILBREAK",
    ]
    for col in text_cols:
        for val in df[col].dropna().astype(str):
            for pattern in injection_patterns:
                if re.search(pattern, val, re.IGNORECASE):
                    print(f"ERROR [CSV]: Suspicious content detected in column '{col}': '{val[:60]}'")
                    print("  Possible prompt injection attempt blocked.")
                    sys.exit(1)

    print(f"  ✅ CSV validated — {len(df)} properties, columns OK, no injection patterns")


# ── 3. Email Guardrail ─────────────────────────────────────────────────────────
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
MAX_RECIPIENTS = 20

def validate_recipients(recipients: list) -> None:
    """
    Checks:
    - At least one recipient
    - All addresses are valid email format
    - Not too many recipients (spam guard)
    """
    if not recipients:
        print("ERROR [Email]: No recipients provided.")
        sys.exit(1)

    if len(recipients) > MAX_RECIPIENTS:
        print(f"ERROR [Email]: {len(recipients)} recipients exceeds max of {MAX_RECIPIENTS}.")
        sys.exit(1)

    for addr in recipients:
        if not EMAIL_PATTERN.match(addr):
            print(f"ERROR [Email]: Invalid email address: '{addr}'")
            sys.exit(1)

    print(f"  ✅ Recipients validated — {len(recipients)} address(es)")


# ── 4. Output Guardrail ────────────────────────────────────────────────────────
# patterns that look like leaked API keys in model output
KEY_LEAK_PATTERNS = [
    r"AIza[0-9A-Za-z_\-]{35}",       # Gemini key pattern
    r"sk-ant-[a-zA-Z0-9\-]{40,}",    # Anthropic key pattern
    r"sk-[a-zA-Z0-9]{48}",           # OpenAI key pattern
]

def scrub_output(text: str) -> str:
    """
    Scans model output for accidental key leaks and redacts them.
    Shouldn't happen but this is a safety net.
    """
    scrubbed = text
    found    = False

    for pattern in KEY_LEAK_PATTERNS:
        if re.search(pattern, scrubbed):
            scrubbed = re.sub(pattern, "[REDACTED-API-KEY]", scrubbed)
            found    = True

    if found:
        print("  ⚠️  WARNING: Possible API key pattern found in model output — redacted.")

    return scrubbed


# ── 5. Rate Limit Guard ────────────────────────────────────────────────────────
_call_count = 0
MAX_CALLS_PER_RUN = 10  # Gemini free tier: 250/day, but cap per run for safety

def check_rate_limit() -> None:
    """
    Counts API calls within a single run.
    Prevents runaway loops from burning free quota.
    """
    global _call_count
    _call_count += 1

    if _call_count > MAX_CALLS_PER_RUN:
        print(f"ERROR [Rate limit]: Exceeded {MAX_CALLS_PER_RUN} API calls in this run.")
        print("  This is a safety cap. If legitimate, increase MAX_CALLS_PER_RUN in guardrails.py")
        sys.exit(1)


# ── 6. CSV Path Guardrail ──────────────────────────────────────────────────────
def validate_csv_path(path: str) -> None:
    """
    Checks:
    - File exists
    - Is actually a .csv file
    - Not suspiciously large (> 5MB)
    """
    p = Path(path)

    if not p.exists():
        print(f"ERROR [CSV path]: File not found: '{path}'")
        sys.exit(1)

    if p.suffix.lower() != ".csv":
        print(f"ERROR [CSV path]: File must be a .csv — got '{p.suffix}'")
        sys.exit(1)

    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > 5:
        print(f"ERROR [CSV path]: File is {size_mb:.1f}MB — max is 5MB.")
        print("  Large files risk exceeding Gemini's token limit.")
        sys.exit(1)

    print(f"  ✅ CSV path validated — {p.name} ({size_mb*1024:.0f}KB)")


# ── Run all guardrails at once ─────────────────────────────────────────────────
def run_all(csv_path: str, recipients: list, df=None) -> None:
    """
    Single entry point — call this from orchestrator before running agents.
    """
    print("\n  Running guardrails...")

    # API key
    api_key = os.environ.get("GEMINI_API_KEY", "")
    validate_api_key(api_key, "GEMINI_API_KEY")

    # CSV path
    validate_csv_path(csv_path)

    # CSV content (if df already loaded)
    if df is not None:
        validate_csv(df)

    # recipients
    validate_recipients(recipients)

    print("  All guardrails passed ✅\n")