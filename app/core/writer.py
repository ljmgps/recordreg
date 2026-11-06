"""结果输出：Markdown（人类可读 + 知识库友好）与 JSON（结构化全量）。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from ..config import Config
from .pipeline import DocResult

log = logging.getLogger(__name__)

TOOL_VERSION = "0.2.0"
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def fmt_ts(sec: float, always_hours: bool = False) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0 or always_hours:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def safe_stem(name: str) -> str:
    return _ILLEGAL.sub("_", name).strip().strip(".") or "untitled"


def output_paths(doc: DocResult, cfg: Config) -> tuple[Path, Path]:
    """决定输出路径。

    同名但内容不同（比如同一段音频的 .m4a 和 .aac 两个版本）时，
    后来的会覆盖前面的。这里检测已有 json 里的 sha256，不同就加扩展名后缀。
    """
    cfg.paths.output.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(doc.source.stem)
    md_path = cfg.paths.output / f"{stem}.md"
    json_path = cfg.paths.output / f"{stem}.json"

    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_sha = (old.get("file") or {}).get("sha256")
            if old_sha and old_sha != doc.sha256:
                ext = doc.source.suffix.lstrip(".").lower() or "audio"
                stem = f"{stem}_{ext}"
                md_path = cfg.paths.output / f"{stem}.md"
                json_path = cfg.paths.output / f"{stem}.json"
        except Exception:  # noqa: BLE001
            pass

    return md_path, json_path


def merge_for_display(
    segments,
    gap_sec: float = 1.2,
    min_block_sec: float = 8.0,
    max_block_sec: float = 25.0,
):
    """把细碎的 VAD 段合并成适合阅读的块。

    VAD 按呼吸停顿切分，一句话常被拆成 2-3 段；连续讲话时段间几乎没有空隙，
    所以不能只看间隔，还要保证每块有足够长度：
    - 块时长未达 min_block_sec 时，即使间隔很小也继续合并，避免"两秒一段"；
    - 块时长达标后，遇到超过 gap_sec 的停顿才断开；
    - 任何情况下不超过 max_block_sec，防止整篇糊成一段。

    JSON 保留原始细粒度分段（M2 的说话人分离需要），只有 Markdown 走这套合并。
    """
    if not segments:
        return []
    blocks: list[list] = [[segments[0]]]
    for seg in segments[1:]:
        cur = blocks[-1]
        dur = cur[-1].end - cur[0].start
        gap = seg.start - cur[-1].end
        should_break = (dur >= min_block_sec and gap > gap_sec) or dur >= max_block_sec
        if should_break:
            blocks.append([seg])
        else:
            cur.append(seg)
    return blocks


def write_markdown(doc: DocResult, cfg: Config, path: Path) -> Path:
    """极简 Markdown：源文件名 + 按说话人轮次排列的正文。

    所有元数据（时长、语种、统计、分段细节、声纹、模型版本）一律放 JSON，
    Markdown 只做人能一眼读完的阅读版。
    """
    title = doc.source.stem

    lines: list[str] = [
        f"# {title}",
        "",
        f"源文件：`{doc.source.name}`",
        "",
    ]

    if doc.has_speaker:
        # 有说话人：一个轮次一块，标签+时间戳在同一行
        for turn in doc.turns:
            spk = int(turn["speaker"]) + 1
            lines.append(
                f"**说话人{spk}** `[{fmt_ts(turn['start'])} → {fmt_ts(turn['end'])}]`"
            )
            lines.append("")
            lines.append(str(turn["text"]).strip())
            lines.append("")
    else:
        # 无说话人（或单人）：按语音停顿合并成阅读块
        for block in merge_for_display(doc.segments, gap_sec=1.2):
            start, end = block[0].start, block[-1].end
            text = "".join(s.text for s in block)
            lines.append(f"**`[{fmt_ts(start)} → {fmt_ts(end)}]`**")
            lines.append("")
            lines.append(text)
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_json(doc: DocResult, cfg: Config, path: Path) -> Path:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "schema_version": "1.0",
        "tool_version": TOOL_VERSION,
        "file": {
            "name": doc.source.name,
            "ext": doc.source.suffix.lstrip(".").lower(),
            "path": str(doc.source),
            "size_bytes": doc.info.size_bytes,
            "sha256": doc.sha256,
            "duration_sec": round(doc.info.duration_sec, 3),
            "origin_sample_rate": doc.info.sample_rate,
            "origin_channels": doc.info.channels,
            "codec": doc.info.codec,
        },
        "models": {
            "asr": "SenseVoiceSmall",
            "vad": "fsmn-vad-zh-cn-16k-common",
            "speaker": "campplus_sv_zh-cn_16k-common" if doc.cluster_k > 0 else None,
            "device": doc.device,
        },
        "params": {
            "vad_max_segment_ms": cfg.vad.max_single_segment_time,
            "cluster_mode": doc.cluster_mode,
            "cluster_k": doc.cluster_k,
            "smoothing": bool(cfg.speaker.smoothing),
        },
        "processed_at": now,
        "language": doc.language,
        "speakers": [
            {
                "id": s.id,
                "alias": s.id.replace("SPK_", "说话人"),
                "total_sec": round(s.total_sec, 2),
                "segment_count": s.segment_count,
                "turn_count": s.turn_count,
                "char_count": s.char_count,
            }
            for s in doc.speakers
        ],
        "turns": [
            {
                "idx": i + 1,
                "speaker": f"SPK_{int(t['speaker']) + 1}" if doc.has_speaker else None,
                "start": round(float(t["start"]), 3),
                "end": round(float(t["end"]), 3),
                "text": t["text"],
                "segment_count": t["segment_count"],
            }
            for i, t in enumerate(doc.turns)
        ],
        "segments": [
            {
                "idx": s.index,
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "duration": round(s.duration, 3),
                "speaker": s.speaker,
                "raw_speaker": s.raw_speaker,
                "smoothed": s.smoothed,
                "text": s.text,
                "lang": s.lang,
                "emotion": s.emotion,
                "event": s.event,
            }
            for s in doc.segments
        ],
        "stats": {
            "segment_count": len(doc.segments),
            "turn_count": len(doc.turns),
            "speaker_count": len(doc.speakers),
            "dropped_segments": doc.dropped_segments,
            "char_count": doc.char_count,
            "rtf": round(doc.rtf, 4),
            "elapsed_sec": round(doc.elapsed_sec, 2),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_all(doc: DocResult, cfg: Config) -> dict[str, Path]:
    md_path, json_path = output_paths(doc, cfg)
    out: dict[str, Path] = {}
    if cfg.output.write_markdown:
        out["md"] = write_markdown(doc, cfg, md_path)
    if cfg.output.write_json:
        out["json"] = write_json(doc, cfg, json_path)
    return out
