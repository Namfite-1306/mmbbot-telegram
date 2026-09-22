"""Compare stored-data signals before/after F/M input recovery at one session.

No crawl, no database writes and no strategy-rule changes are made here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.market_store import MarketStore  # noqa: E402
from app.providers.strategy_signal_provider import StrategySignalProvider  # noqa: E402
from app.strategy_engine import StrategyDataError, VietcapStrategyEngine  # noqa: E402


class BeforeInputsEngine(VietcapStrategyEngine):
    """Represent the former missing industry map and HNXINDEX, for comparison."""

    def _sector_map(self) -> dict[str, str]:
        return {}

    def index(self, as_of: pd.Timestamp, exchange: str = "HSX") -> pd.DataFrame:
        if exchange == "HNX":
            raise StrategyDataError("Thiếu chỉ số HNXINDEX")
        return super().index(as_of, exchange)


def audit(as_of: str) -> dict:
    day = pd.Timestamp(as_of)
    store = MarketStore(ROOT / "data" / "fintech_bot.db")
    universe = {symbol: exchange for symbol, exchange in store.universe().items()
                if exchange in {"HSX", "HNX"}}
    clock = datetime.combine(day.date(), time(16), tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    before = StrategySignalProvider(engine=BeforeInputsEngine(store), now=clock, auto_refresh=False)
    after_engine = VietcapStrategyEngine(store)
    after = StrategySignalProvider(engine=after_engine, now=clock, auto_refresh=False)
    breadth_snapshot = (as_of, store.breadth(as_of))
    before_actions: Counter[str] = Counter()
    after_actions: Counter[str] = Counter()
    f_missing: Counter[str] = Counter()
    m_missing: Counter[str] = Counter()
    fundamental_failures: Counter[str] = Counter()
    price_failures: Counter[str] = Counter()
    changed = []
    full_scores = 0
    full_scores_exact = 0
    stale_prices = 0
    examples = {}
    for symbol, exchange in sorted(universe.items()):
        try:
            after_engine.fundamental(symbol, day, exchange)
        except StrategyDataError as exc:
            fundamental_failures[str(exc)] += 1
        old = before._build_signal(symbol, as_of=day, breadth_snapshot=breadth_snapshot,
                                   universe=universe)
        new = after._build_signal(symbol, as_of=day, breadth_snapshot=breadth_snapshot,
                                  universe=universe)
        before_actions[old.action.value if old.action else "NONE"] += 1
        after_actions[new.action.value if new.action else "NONE"] += 1
        if old.action != new.action:
            changed.append({"symbol": symbol, "exchange": exchange,
                            "before": old.action.value if old.action else "NONE",
                            "after": new.action.value if new.action else "NONE"})
        components = new.score_components or {}
        if not components:
            price_failures[new.reasons[0] if new.reasons else "unknown"] += 1
        if components.get("F") is not None and components.get("M") is not None:
            full_scores += 1
            if new.data_time.date() == day.date():
                full_scores_exact += 1
        else:
            for reason in new.reasons:
                if reason.startswith("F chưa tính được:"):
                    f_missing[reason.partition(": ")[2]] += 1
                elif reason.startswith("M chưa tính được:"):
                    m_missing[reason.partition(": ")[2]] += 1
        if components and new.data_time.date() != day.date():
            stale_prices += 1
        if symbol in {"KLB", "TLG"}:
            examples[symbol] = {"old_score": old.score, "new_score": new.score,
                                "T": components.get("T"), "F": components.get("F"),
                                "M": components.get("M"), "action": new.action.value if new.action else "NONE"}
    return {"session": as_of, "universe_hsx_hnx": len(universe),
            "complete_tfm_on_or_before_session": full_scores,
            "complete_tfm_exact_session": full_scores_exact,
            "stale_prices_at_session": stale_prices,
            "fundamental_failures_at_session": dict(fundamental_failures),
            "price_failures": dict(price_failures),
            "f_missing_reasons": dict(f_missing), "m_missing_reasons": dict(m_missing),
            "actions_before": dict(before_actions), "actions_after": dict(after_actions),
            "changed_actions": changed, "examples": examples}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=str, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.as_of), ensure_ascii=True, indent=2, default=float))
