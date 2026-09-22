# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write example ``*.raw`` chart files for the JS visualizer."""

from visual.plot.web_chart import WebChart


def main() -> None:
    WebChart.write_example()


if __name__ == "__main__":
    main()
