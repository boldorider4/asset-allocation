import json
from pathlib import Path


global portfolio
portfolio: dict[str, list[dict]] = {}

def _default_assets_path() -> Path:
    return Path(__file__).resolve().parent / "assets.json"


def load_portfolio(path: Path | None = None) -> dict[str, list[dict]]:
    """Load portfolio buckets from a JSON file (default: assets.json next to this module)."""
    assets_path = path or _default_assets_path()
    with assets_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("assets root must be a JSON object")
    for key, positions in data.items():
        if not isinstance(positions, list):
            raise ValueError(f"{key!r} must be a JSON array")
        for i, pos in enumerate(positions):
            if not isinstance(pos, dict):
                raise ValueError(f"{key}[{i}] must be a JSON object")
    return data


def write_portfolio_to_file(path: Path | None = None) -> None:
    """Overwrite the assets JSON file (default: assets.json next to this module) with the current global ``portfolio``."""
    assets_path = path or _default_assets_path()
    with assets_path.open("w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)
        f.write("\n")
