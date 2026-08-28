"""射影プロファイルからの罫線位置検出（共有の原始関数・D-25）。

もともと grid.py（detect-grid・テンプレート編集画面向け）にあった検出を、
実行時の平行移動推定（align.py・D-25）と共用するためここへ抽出した。
run のホットパスが GUI 向けモジュールへ依存する層の逆転を避ける。
"""
from __future__ import annotations

import numpy as np

H_COVERAGE = 0.50   # 水平線: 行射影の被覆率下限
V_COVERAGE = 0.35   # 垂直線: 列射影の被覆率下限（かすれ・交差切れに寛容）
LINE_GAP = 6        # 同一線とみなす画素間隔


def line_positions(profile: "np.ndarray", threshold: float) -> list[int]:
    """射影プロファイル中で threshold を超える帯の中心位置（px）。"""
    idx = np.where(profile > threshold)[0]
    if len(idx) == 0:
        return []
    groups: list[list[int]] = [[int(idx[0])]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= LINE_GAP:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    return [int(np.mean(g)) for g in groups]
