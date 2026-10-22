"""Split observed quota usage into harness and user shares.

Only the account total is observable. The harness's share is its output
tokens times a learned token→% coefficient; the user's share is what remains.
"""

from __future__ import annotations

__all__ = ["TokenToPct", "estimate_user_usage"]

EMA_ALPHA = 0.1


class TokenToPct:
    """Online estimate of quota-% per harness token.

    Learns only while the user is idle, so the whole delta is the harness's.
    Usage is reported in whole percent: a tick whose delta rounds to 0 is not
    evidence of a zero coefficient, so its tokens carry over to the next tick
    that does show movement instead of dragging the estimate toward 0.
    """

    def __init__(self, coef: float) -> None:
        if coef <= 0:
            raise ValueError(f"coef must be > 0: {coef}")
        self.coef = coef
        self._carried_tokens = 0

    def update(self, delta_total_pct: float, harness_tokens: int, user_idle: bool) -> None:
        if not user_idle or delta_total_pct < 0:
            # User activity (or a window reset) makes the delta unattributable.
            self._carried_tokens = 0
            return
        tokens = self._carried_tokens + harness_tokens
        if delta_total_pct == 0:
            self._carried_tokens = tokens
            return
        self._carried_tokens = 0
        if tokens <= 0:
            return
        sample = delta_total_pct / tokens
        self.coef = (1 - EMA_ALPHA) * self.coef + EMA_ALPHA * sample

    def pct(self, tokens: int) -> float:
        return tokens * self.coef


def estimate_user_usage(delta_total_pct: float, harness_tokens: int, conv: TokenToPct) -> float:
    """User's share of ``delta_total_pct``; never negative."""
    return max(0.0, delta_total_pct - conv.pct(harness_tokens))
