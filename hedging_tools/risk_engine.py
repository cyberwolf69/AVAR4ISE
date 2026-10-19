"""Offline decision gate. Does not connect to OKX or execute orders."""
from hedging_tools.paths import CONFIG_PATH
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite number")
    return result


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    leverage: int = 0
    contracts: str = "0"
    margin: str = "0"
    notional: str = "0"
    planned_risk: str = "0"


class RiskEngine:
    def __init__(self, config):
        self.config = config
        if config["mode"] != "paper":
            raise ValueError("Only offline paper mode is implemented")
        if not 10 <= config["min_leverage"] <= config["max_leverage"] <= 30:
            raise ValueError("Invalid leverage limits")
        for key in ("initial_equity", "margin_per_trade", "risk_per_trade",
                    "max_spread_fraction", "estimated_roundtrip_cost_fraction"):
            if number(config[key]) <= 0:
                raise ValueError(f"Invalid {key}")
        if not 0 < number(config["daily_loss_fraction"]) < 1:
            raise ValueError("Invalid daily loss limit")

    def evaluate(self, signal, market, account, now):
        """All inputs are snapshots; account equity must already include costs.

        halted_today must be latched by the future account supervisor after a
        daily breach, even if equity later recovers. Liquidation prices below
        are supplied estimates per leverage, not calculated by this module.
        """
        try:
            return self._evaluate(signal, market, account, now)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            return Decision("WAIT", f"Invalid or missing input: {exc}")

    def _evaluate(self, s, m, a, now):
        c = self.config
        if now.tzinfo is None:
            raise ValueError("Timezone-aware timestamp required")
        start, equity = number(a["day_start_equity"]), number(a["equity"])
        if start <= 0:
            raise ValueError("Invalid starting equity")
        if type(a["halted_today"]) is not bool:
            raise ValueError("halted_today must be boolean")
        remaining = equity - start * (1 - number(c["daily_loss_fraction"]))
        if a["halted_today"] or remaining <= 0:
            return Decision("HALT", "Cancel entries, close positions, and latch daily halt")
        positions = a["open_positions"]
        if type(positions) is not int or positions < 0:
            raise ValueError("Invalid position count")
        if positions >= c["max_positions"] or a["pending_entry"]:
            return Decision("WAIT", "Position or entry order already exists")
        if s["action"] == "WAIT":
            return Decision("WAIT", "Analyst abstained")
        if s["action"] not in ("LONG", "SHORT"):
            raise ValueError("Invalid signal action")
        if s["pair"] not in c["pairs"] or s["pair"] != m["pair"]:
            return Decision("WAIT", "Pair not allowed or snapshot mismatch")
        observed = datetime.fromisoformat(m["observed_at"])
        expiry = datetime.fromisoformat(s["expires_at"])
        age = (now - observed).total_seconds()
        if age < 0 or age > c["max_market_age_seconds"] or now >= expiry:
            return Decision("WAIT", "Stale market or expired signal")
        bid, ask, stop = number(m["bid"]), number(m["ask"]), number(s["stop"])
        if not 0 < bid <= ask or stop <= 0:
            raise ValueError("Invalid prices")
        if (ask - bid) / bid > number(c["max_spread_fraction"]):
            return Decision("WAIT", "Spread exceeds limit")
        entry = ask if s["action"] == "LONG" else bid
        if (s["action"] == "LONG" and stop >= entry) or (s["action"] == "SHORT" and stop <= entry):
            return Decision("WAIT", "Stop is on wrong side of entry")
        if m["contract_type"] != "linear" or m["settle_currency"] != "USDT" or m["state"] != "live":
            return Decision("WAIT", "Unsupported contract")
        unit, lot, minimum = (number(m[k]) for k in ("base_units_per_contract", "lot_size", "min_contracts"))
        if min(unit, lot, minimum) <= 0:
            raise ValueError("Invalid contract metadata")
        margin = number(c["margin_per_trade"])
        costs = number(c["estimated_roundtrip_cost_fraction"])
        risk_cap = min(number(c["risk_per_trade"]), remaining)
        # Start with the least leverage. Higher leverage never bypasses risk checks.
        for leverage in range(c["min_leverage"], min(c["max_leverage"], int(m["max_leverage"])) + 1):
            quantity = (margin * leverage / (entry * unit) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
            notional = quantity * unit * entry
            actual_margin = notional / leverage
            risk = quantity * unit * abs(entry - stop) + notional * costs
            if quantity < minimum or risk > risk_cap:
                continue
            if number(a["available_balance"]) < actual_margin + notional * costs:
                continue
            liquidation = number(m["estimated_liquidation_by_leverage"][str(leverage)])
            buffer = abs(entry - stop)
            if liquidation <= 0:
                continue
            if s["action"] == "LONG" and stop - liquidation < buffer:
                continue
            if s["action"] == "SHORT" and liquidation - stop < buffer:
                continue
            return Decision("APPROVE_PAPER", "Within configured limits", leverage,
                            str(quantity), str(actual_margin), str(notional), str(risk))
        return Decision("WAIT", "No valid size/leverage within risk, balance, and liquidation limits")


def load_engine():
    return RiskEngine(json.loads(CONFIG_PATH.read_text()))
