# Flaky Test Reporter

AI-powered test failure classifier. Feed it test results + logs and it tells you:

- **FLAKY** vs **REAL FAILURE** for every failed test
- Which **service or component** is responsible
- Concrete **fix suggestions**
---

## Install

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Usage

```bash
# Markdown report to stdout
python analyzer.py --results results.json --logs test.log

# Save report to file
python analyzer.py --results results.json --logs test.log --output report.md

# Raw JSON output (for further processing)
python analyzer.py --results results.json --logs test.log --json

# Pipe results via stdin (logs still via flag)
cat results.json | python analyzer.py --logs test.log

# Logs are optional
python analyzer.py --results results.json
```

---

## Input Format

### `results.json`

A JSON object with a `"tests"` array (or a bare array):

```json
{
  "suite": "checkout-service",
  "tests": [
    {
      "name":        "test_payment_timeout",
      "status":      "FAILED",
      "duration_ms": 30042,
      "error":       "ConnectionError: timed out after 30s",
      "run_history": ["PASS", "FAIL", "PASS", "FAIL"]
    },
    {
      "name":        "test_discount_calculation",
      "status":      "FAILED",
      "duration_ms": 43,
      "error":       "AssertionError: Expected 89.99, got 99.99",
      "run_history": ["FAIL", "FAIL", "FAIL", "FAIL"]
    }
  ]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `name` | ✅ | Test identifier |
| `status` | ✅ | `"PASSED"` / `"FAILED"` (case-insensitive) |
| `error` | — | Exception / assertion message |
| `duration_ms` | — | Helps detect timeout-related flakiness |
| `run_history` | — | Previous run outcomes — strongest flakiness signal |

### `test.log`

Plain text. The tool sends the last 50 000 characters to Claude — no preprocessing needed. Logs are optional; results alone are sufficient for basic classification.

---

## Output

```
# Flaky Test Diagnostic Report
_Generated 2026-03-30 14:35:12_

## Summary

| Tests Analyzed | Flaky | Real Failures | Health |
|:-:|:-:|:-:|:-:|
| 4 | 2 | 1 | 🟡 DEGRADED |

---

## Test Analysis

### `test_payment_timeout`
**🟡 FLAKY** · Confidence: `HIGH` · Service: **payment-gateway**

**Root Cause:** Intermittent connection timeout to payment-gateway:8443 under load.

**Evidence:** `Retry 3/3: connection refused — payment-gateway:8443`

**Fix Suggestions:**
1. Add a health-check / readiness gate before the test starts.
2. Increase default timeout and add exponential-backoff retry in the client.
3. Mock the payment gateway in unit tests; reserve live calls for integration.
```

---

## Examples

Try it against the bundled samples:

```bash
python analyzer.py \
  --results examples/sample_results.json \
  --logs    examples/sample.log
```

---

## How it works

```
results.json + test.log
        │
        ▼
  analyzer.py
        │  builds a structured prompt
        ▼
  Claude claude-opus-4-6  (adaptive thinking + streaming)
        │  returns JSON: classification, service, evidence, fixes
        ▼
  Markdown report  (or --json for raw JSON)
```

Failures are classified using:
- **Run history** — intermittent = flaky, always-fails = real
- **Error message patterns** — timeouts, port conflicts, DNS → flaky; assertion mismatches → real
- **Log signals** — retry storms, race markers, environment errors
- **Duration** — suspiciously long tests often point to timeout flakiness
