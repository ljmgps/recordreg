"""核心纯函数的自测（不依赖 torch，可直接运行）。

    .venv\\Scripts\\python.exe tests\\test_core.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from app.core.asr import parse_rich, transcribe_hotword_correct, AsrResult
from app.core.vad import _merge_close, normalize_segments
from app.core.writer import fmt_ts, safe_stem

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


def test_merge_close() -> None:
    print("merge_close")
    segs = [(0, 1000), (1100, 2000), (3000, 4000)]
    out = _merge_close(segs, gap_ms=200)
    check("相邻小间隔合并", out == [(0, 2000), (3000, 4000)], str(out))

    out2 = _merge_close(segs, gap_ms=50)
    check("大间隔不合并", out2 == [(0, 1000), (1100, 2000), (3000, 4000)], str(out2))


def test_normalize() -> None:
    print("normalize_segments")
    # 一个 800ms 短段夹在两个正常段中间
    raw = [(0, 2000), (2100, 2500), (2600, 5000)]
    segs, dropped = normalize_segments(
        raw, min_ms=400, max_ms=30000, merge_gap_ms=200, split_target_ms=15000
    )
    check("全部落在合理范围", all(s.duration_ms >= 400 for s in segs), str([s.duration_ms for s in segs]))
    check("覆盖到结尾", segs[-1].end_ms == 5000)

    # 超长段必须被切开
    long_raw = [(0, 40000)]
    data = np.zeros(16000 * 41, dtype=np.float32)
    segs2, _ = normalize_segments(
        long_raw, min_ms=400, max_ms=30000, merge_gap_ms=200,
        split_target_ms=15000, data=data, sr=16000,
    )
    check("超长段被切分", len(segs2) >= 2, f"切成 {len(segs2)} 段")
    check("切分后均不超上限", all(s.duration_ms <= 30000 for s in segs2),
          str([s.duration_ms for s in segs2]))

    # 空输入
    segs3, dropped3 = normalize_segments([])
    check("空输入返回空", segs3 == [] and dropped3 == 0)


def test_parse_rich() -> None:
    print("parse_rich")
    raw = "<|zh|><|NEUTRAL|><|Speech|><|woitn|>今天天气不错，我们出去走走。"
    r = parse_rich(raw)
    check("剥离标记", r.text == "今天天气不错，我们出去走走。", repr(r.text))
    check("语种", r.lang == "zh", r.lang)
    check("情感", r.emotion == "NEUTRAL", r.emotion)
    check("事件", r.event == "Speech", r.event)

    r2 = parse_rich("")
    check("空输入", r2.text == "" and r2.lang == "")


def test_hotword() -> None:
    print("hotword correct")
    rs = [AsrResult(text="我们用的是聚水弹系统"), AsrResult(text="聚水弹很好用")]
    out = transcribe_hotword_correct(rs, [["聚水弹", "聚水潭"]])
    check("替换生效", out[0].text == "我们用的是聚水潭系统", out[0].text)
    check("多处替换", out[1].text == "聚水潭很好用", out[1].text)

    rs2 = [AsrResult(text="聚水弹")]
    out2 = transcribe_hotword_correct(rs2, [])
    check("空热词表不改变", out2[0].text == "聚水弹")


def test_writer_utils() -> None:
    print("writer 工具函数")
    check("fmt_ts 分钟级", fmt_ts(72) == "01:12", fmt_ts(72))
    check("fmt_ts 小时级", fmt_ts(3672) == "01:01:12", fmt_ts(3672))
    check("fmt_ts 强制小时", fmt_ts(72, always_hours=True) == "00:01:12")
    check("非法字符清洗", safe_stem('a/b:c*d?e"f') == "a_b_c_d_e_f", safe_stem('a/b:c*d?e"f'))
    check("空名兜底", safe_stem("   ") == "untitled")


def main() -> int:
    test_merge_close()
    test_normalize()
    test_parse_rich()
    test_hotword()
    test_writer_utils()
    print()
    if FAILS:
        print(f"{len(FAILS)} 项失败: {FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
