from __future__ import annotations

from position.justetf_position import JustETFPosition


class CLIQueryPosition(JustETFPosition):
    """
    JustETF country weights with a broker-supplied price from a CLI scrape
    (``sc`` for Scalable, ``pytr`` for Trade Republic).

    ``_fast_info_price`` is never called when ``price`` is passed to ``Position``.
    """

    def _fast_info_price(self) -> float | None:
        raise NotImplementedError(
            "CLIQueryPosition uses the price from broker holdings; "
            "quote lookup is not implemented"
        )

    def _history_last_close(self) -> float | None:
        raise NotImplementedError(
            "CLIQueryPosition does not fetch historical closes; "
            "use the broker-supplied price"
        )
