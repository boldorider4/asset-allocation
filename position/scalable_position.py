from __future__ import annotations

from position.justetf_position import JustETFPosition


class ScalablePosition(JustETFPosition):
    """
    JustETF country weights with a broker-supplied price from ``sc``.

    ``_fast_info_price`` is never called when ``price`` is passed to ``Position``.
    """

    def _fast_info_price(self) -> float | None:
        raise NotImplementedError(
            "ScalablePosition uses the price from sc broker holdings; "
            "quote lookup is not implemented"
        )

    def _history_last_close(self) -> float | None:
        raise NotImplementedError(
            "ScalablePosition does not fetch historical closes; "
            "use the broker-supplied price"
        )
