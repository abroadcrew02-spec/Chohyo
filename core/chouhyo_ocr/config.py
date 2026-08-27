"""設定ファイル（要件 §5.8: 1ファイル・6項目のみ）。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import project_root


@dataclass(frozen=True)
class Config:
    unclear_threshold: float = 0.85   # 〓閾値（render で効く）
    era_threshold: float = 0.05       # 丸印閾値（環状帯インク比の下限・render で効く）
    send_limit: int = 100             # 実行あたりの API 送信枚数上限
    output_dir: str = "output"        # 出力先
    workdir: str = "workdir"          # 中間データ保持先
    log_dir: str = "logs"             # ログ出力先


def config_path() -> Path:
    return project_root() / "config.json"


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else config_path()
    if not p.exists():
        return Config()
    data = json.loads(p.read_text(encoding="utf-8"))
    known = {k: data[k] for k in Config.__dataclass_fields__ if k in data}
    return Config(**known)


def save_config(cfg: Config, path: str | Path | None = None) -> None:
    p = Path(path) if path else config_path()
    p.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
