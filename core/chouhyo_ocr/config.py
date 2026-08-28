"""設定ファイル（要件 §5.8: 1ファイル・6項目のみ）。

読み込み時に型・範囲を検証し、通らなければ ConfigError で止める（issue #14）。
〓閾値 0 は「低信頼値がすべて素通りする」＝転記主義の無効化なので、黙って
受け入れず拒否する。未知キーも拒否する——typo したキーが無言で既定値に
落ちると、利用者は設定が効いていると誤解したまま運用する。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import project_root


class ConfigError(ValueError):
    """config.json の値が不正（キー名は表示してよい・記入値ではない）。"""


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


def _validate(cfg: Config) -> Config:
    for key in ("unclear_threshold", "era_threshold"):
        v = getattr(cfg, key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise ConfigError(f"{key} は 0 より大きく 1 以下の数値にする（現在: {v!r}）")
    if isinstance(cfg.send_limit, bool) or not isinstance(cfg.send_limit, int) \
            or cfg.send_limit < 1:
        raise ConfigError(f"send_limit は 1 以上の整数にする（現在: {cfg.send_limit!r}）")
    for key in ("output_dir", "workdir", "log_dir"):
        v = getattr(cfg, key)
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(f"{key} は空でないパス文字列にする（現在: {v!r})")
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else config_path()
    if not p.exists():
        return Config()
    data = json.loads(p.read_text(encoding="utf-8"))
    unknown = sorted(set(data) - set(Config.__dataclass_fields__))
    if unknown:
        raise ConfigError(f"config.json に未知のキーがある: {', '.join(unknown)}")
    return _validate(Config(**data))


def save_config(cfg: Config, path: str | Path | None = None) -> None:
    p = Path(path) if path else config_path()
    p.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
