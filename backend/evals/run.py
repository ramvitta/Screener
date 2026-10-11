"""Eval runner:  python -m evals.run  [--update-baseline]

Suites
  pricing   app/screener/pricing.py vs. independent reference (evals/pricing_golden.jsonl)
  screener  engine output vs. hand-computed golden chains + invariants over every signal
  reason    validator accept/reject on faithful vs. adversarial "Reason for Trade" text

Writes evals/reports/latest.json and exits non-zero if any metric is worse than
evals/baseline.json (so CI blocks regressions) or any hard threshold fails.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_AUTH_TOKEN", "eval-" + "x" * 32)

from app.models.schemas import DataSource  # noqa: E402
from app.reasoning.validator import check_reason  # noqa: E402
from app.screener import engine, invariants, pricing  # noqa: E402
from app.screener.engine import ChainRow, OptionChainSnapshot, ScreenerParams  # noqa: E402

HERE = Path(__file__).parent
REPORT = HERE / "reports" / "latest.json"
BASELINE = HERE / "baseline.json"

# Hard limits, independent of the baseline.
MAX_ABS_ERR = {"price": 1e-6, "delta": 1e-4, "gamma": 1e-5, "theta": 0.01, "vega": 0.01}  # greeks are rounded by the app


def eval_pricing() -> dict:
    worst = {k: 0.0 for k in MAX_ABS_ERR}
    rel_price = 0.0
    n = 0
    for line in (HERE / "pricing_golden.jsonl").read_text().splitlines():
        c = json.loads(line)
        exp = c["expected"]
        got_price = pricing.bs_price(c["spot"], c["strike"], c["t"], c["vol"], c["kind"], c["r"])
        g = pricing.greeks(c["spot"], c["strike"], c["t"], c["vol"], c["kind"], c["r"])
        got = {"price": got_price, "delta": g.delta, "gamma": g.gamma, "theta": g.theta, "vega": g.vega}
        for k in worst:
            worst[k] = max(worst[k], abs(got[k] - exp[k]))
        if exp["price"] > 1e-3:
            rel_price = max(rel_price, abs(got_price - exp["price"]) / exp["price"])
        n += 1
    ok = all(worst[k] <= MAX_ABS_ERR[k] for k in worst)
    return {"cases": n, "max_abs_error": worst, "max_rel_price_error": rel_price, "passed": ok}


def _snapshot(spec: dict) -> OptionChainSnapshot:
    s = spec["snapshot"]
    return OptionChainSnapshot(
        symbol="NIFTY",
        expiry=date(2099, 1, 1),
        spot=s["spot"],
        t_years=s["t_years"],
        chain=[ChainRow(*row) for row in s["chain"]],
        underlying_closes=[21700 + i * 3 for i in range(60)],
        historical_iv=[],
        data_source=DataSource(s["data_source"]),
    )


def eval_screener() -> dict:
    results, invariant_violations, total_signals = [], 0, 0
    for path in sorted((HERE / "screener_golden").glob("*.json")):
        spec = json.loads(path.read_text())
        signals = engine.run_screener(_snapshot(spec), ScreenerParams(min_iv_rank=0))
        by_type = {s.strategy_type.value: s for s in signals}
        total_signals += len(signals)
        for s in signals:
            invariant_violations += len(invariants.check_signal(s))
        exp, failures = spec["expected"], []
        if "emits" in exp:
            if sorted(by_type) != sorted(exp["emits"]):
                failures.append(f"emitted {sorted(by_type)} expected {sorted(exp['emits'])}")
        else:
            for strategy, want in exp.items():
                s = by_type.get(strategy)
                if s is None:
                    failures.append(f"{strategy} missing")
                    continue
                rr = s.risk_reward
                got = {"max_profit": rr.max_profit, "max_loss": rr.max_loss, "breakeven": rr.breakeven, "pop": rr.pop, "breakeven_lower": rr.breakeven_lower}
                for key, value in want.items():
                    if key in ("short", "long"):
                        continue
                    if got.get(key) is None or abs(got[key] - value) > 0.011:
                        failures.append(f"{strategy}.{key}: got {got.get(key)} expected {value}")
        results.append({"case": spec["name"], "passed": not failures, "failures": failures})
    passed = sum(r["passed"] for r in results)
    return {
        "cases": len(results), "pass_rate": passed / len(results), "invariant_violations": invariant_violations,
        "signals_checked": total_signals, "details": results, "passed": passed == len(results) and invariant_violations == 0,
    }


def eval_reason() -> dict:
    spec = json.loads((HERE / "reason_golden.json").read_text())
    facts, rows = spec["facts"], []
    for case in spec["cases"]:
        accepted = check_reason(case["text"], facts) == []
        rows.append({"id": case["id"], "expected_safe": case["safe"], "accepted": accepted, "ok": accepted == case["safe"]})
    unsafe_accepted = sum(1 for r in rows if r["accepted"] and not r["expected_safe"])
    safe_rejected = sum(1 for r in rows if not r["accepted"] and r["expected_safe"])
    return {
        "cases": len(rows), "unsafe_accepted": unsafe_accepted, "safe_rejected": safe_rejected,
        "details": [r for r in rows if not r["ok"]], "passed": unsafe_accepted == 0 and safe_rejected == 0,
    }


def compare_to_baseline(report: dict) -> list:
    if not BASELINE.exists():
        return []
    base, regress = json.loads(BASELINE.read_text()), []
    for k, v in report["pricing"]["max_abs_error"].items():
        limit = base["pricing"]["max_abs_error"].get(k, 0) * 1.1 + 1e-12
        if v > limit:
            regress.append(f"pricing {k} error {v:.3g} > baseline {limit:.3g}")
    if report["screener"]["pass_rate"] < base["screener"]["pass_rate"]:
        regress.append("screener pass_rate dropped")
    if report["reason"]["unsafe_accepted"] > base["reason"]["unsafe_accepted"]:
        regress.append("reason validator now accepts more unsafe text")
    return regress


def main() -> int:
    report = {"pricing": eval_pricing(), "screener": eval_screener(), "reason": eval_reason()}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    if "--update-baseline" in sys.argv:
        BASELINE.write_text(json.dumps({
            "pricing": {"max_abs_error": report["pricing"]["max_abs_error"]},
            "screener": {"pass_rate": report["screener"]["pass_rate"]},
            "reason": {"unsafe_accepted": report["reason"]["unsafe_accepted"]},
        }, indent=2))
    regressions = compare_to_baseline(report)
    for name in ("pricing", "screener", "reason"):
        print(f"{name:9s} {'PASS' if report[name]['passed'] else 'FAIL'}")
    for r in regressions:
        print("REGRESSION:", r)
    failed = not all(report[n]["passed"] for n in report) or bool(regressions)
    if failed:
        print(json.dumps({k: v for k, v in report.items()}, indent=2)[:3000])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
