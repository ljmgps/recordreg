"""说话人分离质量报告。

读 output/ 下的 json，给出几个能判断"分得好不好"的指标：

- 单段轮次占比：一个轮次只有 1 段就换人，是聚类抖动的典型表现，越低越好
- 说话人时长占比：2 人对谈应大致均衡，一边 < 10% 通常是把噪音聚成了人
- 平滑改动率：被后处理改过的段占比，过高说明聚类本身不稳
- 平均轮次时长：正常对话通常在 5-30 秒

用法：
    .venv\\Scripts\\python.exe scripts\\diarization_report.py
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def analyze(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    turns = d.get("turns") or []
    segs = d.get("segments") or []
    speakers = d.get("speakers") or []

    turn_sizes = [t.get("segment_count", 1) for t in turns]
    single = sum(1 for s in turn_sizes if s <= 1)
    total_dur = sum(s.get("total_sec", 0) for s in speakers) or 1.0
    smoothed = sum(1 for s in segs if s.get("smoothed"))

    return {
        "name": d["file"]["name"],
        "dur_min": d["file"]["duration_sec"] / 60,
        "speakers": len(speakers),
        "turns": len(turns),
        "segments": len(segs),
        "single_turn_ratio": single / len(turns) if turns else 0.0,
        "avg_turn_sec": (
            sum(t["end"] - t["start"] for t in turns) / len(turns) if turns else 0.0
        ),
        "max_share": max((s.get("total_sec", 0) / total_dur for s in speakers), default=0),
        "smoothed_ratio": smoothed / len(segs) if segs else 0.0,
        "rtf": d.get("stats", {}).get("rtf", 0),
    }


def main() -> int:
    files = sorted(glob.glob(str(PROJECT_ROOT / "output" / "*.json")))
    if not files:
        print("output/ 下没有 json，先跑一次转写")
        return 1

    rows = [analyze(Path(f)) for f in files]
    header = (
        f"{'文件':38} {'时长':>7} {'人':>3} {'轮':>4} {'单段轮':>7} "
        f"{'均轮长':>7} {'最大占比':>8} {'平滑率':>7}"
    )
    print(header)
    print("-" * 96)
    for r in rows:
        name = r["name"][:36]
        print(
            f"{name:38} {r['dur_min']:>6.1f}分 {r['speakers']:>3} {r['turns']:>4} "
            f"{r['single_turn_ratio']*100:>6.1f}% {r['avg_turn_sec']:>6.1f}s "
            f"{r['max_share']*100:>7.1f}% {r['smoothed_ratio']*100:>6.1f}%"
        )

    print("\n判读建议：")
    print("  单段轮次占比 > 30%  → 聚类抖动明显，可考虑开大 min_segment_ms 或加强平滑")
    print("  最大占比 > 90%      → 几乎判成一个人，可能是单人录音或聚类失败")
    print("  平均轮次 < 3s       → 分段过碎，可调小 vad.split_if_over_time 的切分力度")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
