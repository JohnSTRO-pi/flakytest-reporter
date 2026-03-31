#!/usr/bin/env python3
"""
Flaky Test Reporter — AI-powered test failure classifier.

Reads test results JSON + optional log file, uses Claude to classify each
failure as FLAKY or REAL_FAILURE, identifies the responsible service, and
emits a markdown diagnostic report with fix suggestions.

Usage:
    python analyzer.py --results results.json --logs test.log
    python analyzer.py --results results.json --logs test.log --output report.md
    python analyzer.py --results results.json --json      # raw JSON output
    cat results.json | python analyzer.py --logs test.log
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic

# ── Constants ────────────────────────────────────────────────────────────────

MAX_LOG_CHARS = 50_000  # tail of log sent to Claude (~12 K tokens)

SYSTEM_PROMPT = """\
You are an expert SDET specialising in test reliability analysis.

Classify each FAILED test as one of:
  FLAKY        — Non-deterministic: timing, ports, network, race conditions,
                 environment noise, test-order dependencies.
  REAL_FAILURE — Deterministic bug: wrong output, invariant violations,
                 missing functionality, consistent assertion errors.

For each test, identify the problematic service/component and give 2-3
concrete, actionable fix suggestions.

Respond with valid JSON only — no markdown fences, no commentary."""

PROMPT_TEMPLATE = """\
Analyse these test results and logs.

=== TEST RESULTS ===
{results}

=== LOGS (tail) ===
{logs}

Return JSON matching this schema exactly:
{{
  "summary": {{
    "total_analyzed":     <int>,
    "flaky_count":        <int>,
    "real_failure_count": <int>,
    "overall_health":     "CRITICAL|DEGRADED|HEALTHY"
  }},
  "tests": [
    {{
      "name":                "<test name>",
      "classification":      "FLAKY|REAL_FAILURE|PASS",
      "confidence":          "HIGH|MEDIUM|LOW",
      "problematic_service": "<service or component>",
      "root_cause":          "<one sentence>",
      "evidence":            "<key log line or signal>",
      "fix_suggestions":     ["<fix 1>", "<fix 2>"]
    }}
  ]
}}"""


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_results(path: str | None) -> dict:
    """Load test results from file or stdin. Accepts a dict or bare list."""
    text = Path(path).read_text() if path else sys.stdin.read()
    data = json.loads(text)
    return data if isinstance(data, dict) else {"tests": data}


def load_logs(path: str | None) -> str:
    """Return log content, trimmed to the last MAX_LOG_CHARS characters."""
    if not path:
        return "(no logs provided)"
    text = Path(path).read_text(errors="replace")
    if len(text) > MAX_LOG_CHARS:
        text = "...[truncated — showing tail]...\n" + text[-MAX_LOG_CHARS:]
    return text


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze(results: dict, logs: str) -> dict:
    """Call Claude with adaptive thinking and return parsed analysis dict."""
    client = anthropic.Anthropic()

    user_content = PROMPT_TEMPLATE.format(
        results=json.dumps(results, indent=2),
        logs=logs,
    )

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=8192,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        final = stream.get_final_message()

    # Extract the text block; skip thinking blocks
    raw = next(b.text for b in final.content if b.type == "text")
    return json.loads(raw.strip())


# ── Report rendering ─────────────────────────────────────────────────────────

_BADGE = {
    "FLAKY":        "🟡 FLAKY",
    "REAL_FAILURE": "🔴 REAL FAILURE",
    "PASS":         "🟢 PASS",
}
_HEALTH_BADGE = {
    "CRITICAL": "🔴 CRITICAL",
    "DEGRADED":  "🟡 DEGRADED",
    "HEALTHY":   "🟢 HEALTHY",
}


def render_report(analysis: dict) -> str:
    """Render a clean markdown diagnostic report."""
    s = analysis["summary"]
    health = _HEALTH_BADGE.get(s["overall_health"], s["overall_health"])
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# Flaky Test Diagnostic Report",
        f"_Generated {timestamp}_",
        "",
        "## Summary",
        "",
        "| Tests Analyzed | Flaky | Real Failures | Health |",
        "|:-:|:-:|:-:|:-:|",
        f"| {s['total_analyzed']} | {s['flaky_count']} "
        f"| {s['real_failure_count']} | {health} |",
        "",
        "---",
        "",
        "## Test Analysis",
        "",
    ]

    for t in analysis["tests"]:
        clf = t["classification"]
        badge = _BADGE.get(clf, clf)

        lines += [
            f"### `{t['name']}`",
            f"**{badge}** &nbsp;·&nbsp; "
            f"Confidence: `{t['confidence']}` &nbsp;·&nbsp; "
            f"Service: **{t['problematic_service']}**",
            "",
            f"**Root Cause:** {t['root_cause']}",
            "",
            f"**Evidence:** `{t['evidence']}`",
            "",
        ]

        if t.get("fix_suggestions") and clf != "PASS":
            lines.append("**Fix Suggestions:**")
            for i, fix in enumerate(t["fix_suggestions"], 1):
                lines.append(f"{i}. {fix}")
            lines.append("")

        lines += ["---", ""]

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="AI-powered flaky test classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-r", "--results", help="Test results JSON file (or pipe to stdin)")
    ap.add_argument("-l", "--logs",    help="Log file to analyse")
    ap.add_argument("-o", "--output",  help="Write report to this file (default: stdout)")
    ap.add_argument("--json", action="store_true", help="Emit raw JSON instead of markdown")
    args = ap.parse_args()

    if not args.results and sys.stdin.isatty():
        ap.error("Supply --results FILE or pipe JSON to stdin.")

    try:
        results = load_results(args.results)
        logs    = load_logs(args.logs)
    except FileNotFoundError as e:
        sys.exit(f"File not found: {e}")
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid JSON in results: {e}")

    print("Analysing with Claude…", file=sys.stderr)

    try:
        analysis = analyze(results, logs)
    except anthropic.AuthenticationError:
        sys.exit("Error: ANTHROPIC_API_KEY is not set or invalid.")
    except anthropic.APIError as e:
        sys.exit(f"API error: {e}")
    except (json.JSONDecodeError, StopIteration) as e:
        sys.exit(f"Could not parse Claude's response: {e}")

    report = json.dumps(analysis, indent=2) if args.json else render_report(analysis)

    if args.output:
        Path(args.output).write_text(report)
        print(f"Report saved → {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
