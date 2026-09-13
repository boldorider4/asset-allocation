"""Write example ``*.raw`` chart files for the ``_visualizer`` JS app."""

from .web_chart import WebChart


def main() -> None:
    WebChart.write_example()


if __name__ == "__main__":
    main()
