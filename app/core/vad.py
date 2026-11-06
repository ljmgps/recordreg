"""VAD 语音活动检测与语音段规整。

VAD 的价值不只是"找到语音"，更是给后续的说话人分离提供分段边界。
分段质量直接决定声纹提取的质量，所以这里做了三道规整：
1. 合并间隔极小的相邻段（同一句话被切碎）
2. 吸收过短的碎片（"嗯""对"、咳嗽，embedding 噪声大）
3. 切分超长段（超 30s 会撑爆显存，且在能量低谷处切更自然）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Segment:
    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def start_sec(self) -> float:
        return self.start_ms / 1000.0

    @property
    def end_sec(self) -> float:
        return self.end_ms / 1000.0

    def as_tuple(self) -> tuple[float, float]:
        return (self.start_sec, self.end_sec)


def _extract_value(result) -> list:
    """兼容 funasr 不同版本的 VAD 返回结构。"""
    if not result:
        return []
    first = result[0] if isinstance(result, (list, tuple)) else result
    if isinstance(first, dict):
        value = first.get("value")
        if value is None:
            # 部分版本直接返回区间
            for v in first.values():
                if isinstance(v, (list, tuple)):
                    return list(v)
            return []
        return list(value)
    if isinstance(first, (list, tuple)):
        # 形如 [[start, end], ...]
        if first and isinstance(first[0], (list, tuple)):
            return [list(x) for x in first]
    return []


def detect_speech(
    vad_model,
    wav_path: str,
    max_segment_ms: int = 30000,
    speech_noise_thres: float = 0.6,
    max_end_silence_ms: int | None = None,
) -> list[tuple[int, int]]:
    """返回毫秒级语音区间列表。

    ``max_end_silence_ms`` 控制"停顿多久算断句"。对谈录音要调小（500ms 左右），
    否则两人之间稍短的话轮会被并成一大段，后续说话人分离就没法做了。
    """
    kwargs = dict(
        input=wav_path,
        cache={},
        max_single_segment_time=max_segment_ms,
        speech_noise_thres=speech_noise_thres,
        disable_update=True,
    )
    if max_end_silence_ms is not None:
        kwargs["max_end_silence_time"] = max_end_silence_ms

    res = vad_model.generate(**kwargs)
    value = _extract_value(res)
    out: list[tuple[int, int]] = []
    for item in value:
        try:
            s, e = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if e > s > -1:
            out.append((s, e))
    out.sort(key=lambda x: x[0])
    return out


def _merge_close(segs: list[tuple[int, int]], gap_ms: int) -> list[tuple[int, int]]:
    if not segs:
        return []
    out = [segs[0]]
    for s, e in segs[1:]:
        ps, pe = out[-1]
        if s - pe <= gap_ms:
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def _absorb_short(
    segs: list[tuple[int, int]], min_ms: int, max_ms: int
) -> tuple[list[tuple[int, int]], int]:
    """把过短的段并入相邻段；并进去会超长的则丢弃。返回 (结果, 丢弃数)。"""
    if not segs:
        return [], 0
    out: list[tuple[int, int]] = []
    dropped = 0
    for s, e in segs:
        dur = e - s
        if dur >= min_ms:
            out.append((s, e))
            continue
        merged = False
        # 优先并入前一段
        if out:
            ps, pe = out[-1]
            if (e - ps) <= max_ms:
                out[-1] = (ps, e)
                merged = True
        # 其次并入后一段（需看下一个元素，这里用向前探测）
        if not merged:
            out.append((s, e))  # 暂存，循环结束后统一处理尾部
    # 二次处理：尾部残留的孤立短段并入前段
    final: list[tuple[int, int]] = []
    for s, e in out:
        if (e - s) < min_ms and final:
            ps, pe = final[-1]
            if (e - ps) <= max_ms:
                final[-1] = (ps, e)
                continue
        if (e - s) < min_ms and len(final) == 0:
            dropped += 1
            continue
        final.append((s, e))
    return final, dropped


def _find_low_energy_point(
    data: np.ndarray, sr: int, around_sec: float, window_sec: float = 1.5, frame_sec: float = 0.02
) -> float:
    """在目标点附近找能量最低的短窗口中心，作为切分点。"""
    lo = max(0, int((around_sec - window_sec) * sr))
    hi = min(len(data), int((around_sec + window_sec) * sr))
    frame = max(1, int(frame_sec * sr))
    n = (hi - lo) // frame
    if n < 4:
        return around_sec
    block = data[lo: lo + n * frame].reshape(n, frame).astype(np.float32)
    rms = np.sqrt(np.mean(block ** 2, axis=1) + 1e-12)
    idx = int(np.argmin(rms))
    return (lo + (idx + 0.5) * frame) / sr


def _split_long(
    segs: list[tuple[int, int]],
    max_ms: int,
    target_ms: int,
    split_if_over_ms: int,
    data: np.ndarray | None,
    sr: int,
) -> list[tuple[int, int]]:
    """把过长的段切开。

    说话人分离要求每段"只含一个人的声音"，10 秒以上的段里往往已经换了人，
    声纹是混合的。所以这里按 split_if_over_ms 主动切细，切点选能量低谷
    （自然停顿处），尽量不切在字中间。
    """
    out: list[tuple[int, int]] = []
    threshold = min(split_if_over_ms, max_ms)
    for s, e in segs:
        dur = e - s
        if dur <= threshold:
            out.append((s, e))
            continue
        pieces = max(2, int(np.ceil(dur / max(target_ms, 1000))))
        bounds = [s]
        for i in range(1, pieces):
            target_ms_pos = s + dur * i / pieces
            if data is not None and sr > 0:
                point = _find_low_energy_point(data, sr, target_ms_pos / 1000.0) * 1000.0
            else:
                point = target_ms_pos
            point = float(np.clip(point, bounds[-1] + 500, e - 500))
            bounds.append(point)
        bounds.append(e)
        for i in range(len(bounds) - 1):
            a, b = int(bounds[i]), int(bounds[i + 1])
            if b - a > 200:
                out.append((a, b))
    return out


def normalize_segments(
    raw: list[tuple[int, int]],
    min_ms: int = 400,
    max_ms: int = 30000,
    merge_gap_ms: int = 200,
    split_target_ms: int = 15000,
    split_if_over_ms: int = 30000,
    data: np.ndarray | None = None,
    sr: int = 16000,
) -> tuple[list[Segment], int]:
    """规整语音段。返回 (Segment 列表, 被丢弃的段数)。"""
    if not raw:
        return [], 0
    # 顺序很关键：先合并碎段、再吸收短段，最后才切长段。
    # 如果切分之后再合并，刚切开的相邻段会因为间隔为 0 被重新粘连回去。
    merged = _merge_close(sorted(raw, key=lambda x: x[0]), merge_gap_ms)
    absorbed, dropped = _absorb_short(merged, min_ms, max_ms)
    final = _split_long(absorbed, max_ms, split_target_ms, split_if_over_ms, data, sr)
    segments = [Segment(index=i + 1, start_ms=s, end_ms=e) for i, (s, e) in enumerate(final)]
    return segments, dropped
