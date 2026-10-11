"""Regenerates pricing_golden.jsonl.

The expected values come from an INDEPENDENT pure-Python Black-Scholes (math.erf
only — no scipy, no code from app/), so the eval can actually catch a bug in
app/screener/pricing.py rather than comparing the code with itself. The first
rows are the textbook Hull cases (hand-checked: S=K=100, T=1, r=5%, σ=20% →
call 10.4506, put 5.5735).

    python -m evals.generate_pricing_golden
"""

import json
import math
import random
from pathlib import Path

OUT = Path(__file__).parent / "pricing_golden.jsonl"


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def reference(spot, strike, t, vol, r, kind):
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    disc = math.exp(-r * t)
    if kind == "CE":
        price = spot * _cdf(d1) - strike * disc * _cdf(d2)
        delta = _cdf(d1)
        theta = (-spot * _pdf(d1) * vol / (2 * math.sqrt(t)) - r * strike * disc * _cdf(d2)) / 365
    else:
        price = strike * disc * _cdf(-d2) - spot * _cdf(-d1)
        delta = _cdf(d1) - 1
        theta = (-spot * _pdf(d1) * vol / (2 * math.sqrt(t)) + r * strike * disc * _cdf(-d2)) / 365
    gamma = _pdf(d1) / (spot * vol * math.sqrt(t))
    vega = spot * _pdf(d1) * math.sqrt(t) / 100
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def main() -> None:
    rng = random.Random(20261001)  # fixed seed: the dataset is reproducible
    cases = [dict(spot=100, strike=100, t=1.0, vol=0.2, r=0.05, kind=k) for k in ("CE", "PE")]
    while len(cases) < 200:
        spot = rng.choice([21800.0, 22500.0, 24000.0, 100.0, 5000.0])
        cases.append(
            dict(
                spot=spot,
                strike=round(spot * rng.uniform(0.85, 1.15) / 50) * 50 or 50.0,
                t=round(rng.uniform(1 / 365, 1.0), 6),
                vol=round(rng.uniform(0.08, 0.60), 4),
                r=0.065,
                kind=rng.choice(["CE", "PE"]),
            )
        )
    with OUT.open("w") as f:
        for c in cases:
            f.write(json.dumps({**c, "expected": reference(c["spot"], c["strike"], c["t"], c["vol"], c["r"], c["kind"])}) + "\n")
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
