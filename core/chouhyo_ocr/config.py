"""設定ファイル（要件 §5.8: 1ファイル・6項目のみ）。

読み込み時に型・範囲を検証し、通らなければ ConfigError で止める（issue #14）。
〓閾値 0 は「低信頼値がすべて素通りする」＝転記主義の無効化なので、黙って
受け入れず拒否する。未知キーも拒否する——typo したキーが無言で既定値に
落ちると、利用者は設定が効いていると誤解したまま運用する。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .paths import project_root


class ConfigError(ValueError):
    """config.json の値が不正。

    メッセージには設定キー名と設定値を含める（どちらも利用者自身が書いた
    設定であり、帳票の記入値ではないため表示してよい）。
    """


@dataclass(frozen=True)
class Config:
    unclear_threshold: float = 0.85   # 〓閾値（render で効く）
    era_threshold: float = 0.05       # 丸印閾値（環状帯インク比の下限・render で効く）
    send_limit: int = 100             # 実行あたりの API 送信枚数上限
    output_dir: str = "output"        # 出力先
    workdir: str = "workdir"          # 中間データ保持先
    log_dir: str = "logs"             # ログ出力先
    # API 送信の月次上限（安全装置・ユーザー指示 2026-08-28）。無料枠 1,000 に
    # 対し余裕を残す。設定6項目（要件 §5.8）には数えない——利用者が日常的に
    # 触る設定ではなく、課金事故を止めるための歯止めだから
    api_monthly_cap: int = 900
    # 文字単位〓（U-10・#62）。既定 OFF——段階導入し、実データ確認後に反転する
    # （設計 §8.5）。api_monthly_cap と同じ扱いで GUI 設定画面には出さず、
    # 設定6項目（要件 §5.8）にも数えない
    unclear_char_level: bool = False
    # ブロック単位の枠吸着（issue #75 (f)・FR-F39）。**既定 OFF**——許容幅の
    # 内側での誤吸着を検出する下流が存在しない（07 §3.5）ため、実データで
    # 較正するまで既定を反転しない。api_monthly_cap・unclear_char_level と
    # 同じ扱いで GUI 設定画面には出さず、設定6項目（要件 §5.8）にも数えない
    # ——チェックボックス1つで一般利用者が押せる場所に置くと、ON にする前の
    # 差分全件確認（Q-F14 の受け入れ手順）を踏まずに ON になる
    snap_blocks: bool = False
    # issue #72 (t)・FR-F29・08 §3.5.1。実行画面・編集画面が最後に使った
    # テンプレートの区分＋表示名（絶対パスは保存しない）。値は "shipped"
    # （出荷テンプレート）または "user:<表示名>"（利用者テンプレート）の
    # いずれかのみ——**_validate はこのキーだけ例外を投げない特例**
    # （下記 _validate 参照・AC-F60）
    last_template: str = "shipped"
    # issue #72 (t)・M-1（2026-09-02 マリン指摘）。last_template を
    # フォールバックしたときの理由コード（空文字列 = フォールバックなし）。
    # 本番の呼び出し順（load_config → log.init）では、_validate の時点で
    # まだ logging_safe が初期化されておらず直接 warn しても消えるため、
    # ここに理由だけ積んでおき、呼び出し側（cli._load_config_and_init_log）
    # が log.init の直後に読んで warn する。**config.json には永続化しない**
    # （save_config が除く・下記参照）——設定ではなく1回限りの診断情報
    last_template_fallback_reason: str = ""


def config_path() -> Path:
    return project_root() / "config.json"


def _validate(cfg: Config) -> Config:
    for key in ("unclear_threshold", "era_threshold"):
        v = getattr(cfg, key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise ConfigError(f"{key} は 0 より大きく 1 以下の数値にする（現在: {v!r}）")
    # 0 は「送信しないドライラン」として正当（test_resume_cap が §8-6 の検証に使う）
    if isinstance(cfg.send_limit, bool) or not isinstance(cfg.send_limit, int) \
            or cfg.send_limit < 0:
        raise ConfigError(f"send_limit は 0 以上の整数にする（現在: {cfg.send_limit!r}）")
    if isinstance(cfg.api_monthly_cap, bool) \
            or not isinstance(cfg.api_monthly_cap, int) \
            or not 0 <= cfg.api_monthly_cap <= 1000000:
        raise ConfigError(
            f"api_monthly_cap は 0 以上の整数にする（現在: {cfg.api_monthly_cap!r}）")
    for key in ("output_dir", "workdir", "log_dir"):
        v = getattr(cfg, key)
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(f"{key} は空でないパス文字列にする（現在: {v!r}）")
    if not isinstance(cfg.unclear_char_level, bool):
        raise ConfigError(
            f"unclear_char_level は true/false にする（現在: {cfg.unclear_char_level!r}）")
    if not isinstance(cfg.snap_blocks, bool):
        raise ConfigError(
            f"snap_blocks は true/false にする（現在: {cfg.snap_blocks!r}）")
    # issue #72 (t)・FR-F29・AC-F60: last_template だけは ConfigError を
    # 投げない。config.json は手編集や別プロセス（GUI）からも書けるため、
    # 他のキーと同じく例外にすると「last_template の1行が壊れているだけで
    # run／verify／render／remap すべてが起動不能」になる——FR-F29 が明記する
    # 「設定1行で起動不能にしない」方針（08 §3.10 不変条件6）。形式・型が
    # 不正なら黙って "shipped"（出荷テンプレート）へ倒し、名前を出さずに
    # 警告だけ残す
    lt = cfg.last_template
    lt_valid = isinstance(lt, str) and (
        lt == "shipped" or (lt.startswith("user:") and len(lt) > len("user:")))
    if lt_valid:
        # last_template_fallback_reason は「今回の読み込みで実際に
        # フォールバックしたか」を示す一時情報——config.json を手編集して
        # この値を紛れ込ませても（本来 save_config は書かない）、正規化
        # して常に事実と一致させる
        if cfg.last_template_fallback_reason:
            cfg = replace(cfg, last_template_fallback_reason="")
    else:
        # M-1: ここでは log.warn しない（本番順序では未初期化で消える）。
        # 理由コードだけ積んで呼び出し側に委ねる
        cfg = replace(cfg, last_template="shipped",
                      last_template_fallback_reason="invalid_format")
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else config_path()
    if not p.exists():
        return Config()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        # issue #97: 素の JSONDecodeError は cli.main の包括ハンドラへ落ちて
        # `ERROR JSONDecodeError: 処理を中止しました。詳細は error.log を参照。`
        # になるが、**この失敗は log.init より前に起きる**ので error.log には
        # 何も書かれない（案内先が存在しない）。ConfigError に包んで
        # `except ConfigError` 分岐（理由をそのまま出す）へ載せる。
        # 位置と構文の説明だけを出す——config.json の中身は載せない
        raise ConfigError(
            f"config.json が JSON として読めない（{e.lineno} 行 {e.colno} 文字目: "
            f"{e.msg}）。書きかけ・文字化けの可能性がある。"
            "内容を直すか、config.json を削除すると既定値で起動する") from None
    if not isinstance(data, dict):
        raise ConfigError("config.json のトップレベルは JSON オブジェクト "
                          "（{ ... }）にする")
    unknown = sorted(set(data) - set(Config.__dataclass_fields__))
    if unknown:
        raise ConfigError(f"config.json に未知のキーがある: {', '.join(unknown)}")
    return _validate(Config(**data))


def save_config(cfg: Config, path: str | Path | None = None) -> None:
    p = Path(path) if path else config_path()
    data = asdict(cfg)
    # last_template_fallback_reason は1回限りの診断情報であって設定では
    # ない（M-1）。config.json に書くと利用者設定と混ざって見えるため除く
    data.pop("last_template_fallback_reason", None)
    # 一時ファイル + os.replace（issue #97・api_budget._save と同型）。本番の
    # 呼び出し元は現状 GUI 側（Rust の write_config）だが、書き込み経路ごとに
    # 保護の有無が違う状態を残さないため Python 側も揃える。tmp 名をプロセス
    # 固有にするのは #91 と同じ理由
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
