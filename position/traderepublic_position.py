from __future__ import annotations

from position.justetf_position import JustETFPosition


class TradeRepublicPosition(JustETFPosition):
    """
    JustETF country weights with a broker-supplied price from ``pytr``.

    ``_fast_info_price`` is never called when ``price`` is passed to ``Position``.
    """

    def _fast_info_price(self) -> float | None:
        raise NotImplementedError(
            "TradeRepublicPosition uses the price from pytr portfolio; "
            "quote lookup is not implemented"
        )

    def _history_last_close(self) -> float | None:
        raise NotImplementedError(
            "TradeRepublicPosition does not fetch historical closes; "
            "use the broker-supplied price"
        )
