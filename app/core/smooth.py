"""说话人标签的后处理平滑。

聚类是"逐段独立"判断的，会把同一个人的连续发言切成 A/B/A/B 的锯齿，
也会把一声咳嗽判成第三个人。这里用几条保守的规则修正，
但必须保住真实的长插话——所以所有规则都要求"段足够短"才动手。
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

# 默认阈值（毫秒），与设计文档 §6.3 一致
SHORT_MS = 600        # R1：短于此值视为"嗯/对"这类语气词
ISOLATED_MS = 1200    # R2：短于此值且前后同人，判为抖动
GHOST_MIN_SEC = 30.0  # R4：簇总时长低于此值……
GHOST_MIN_COUNT = 8   # ……且段数低于此值，才判为噪音误聚
INHERIT_MAX_GAP_S = 2.0  # R1 补充：与上一段间隔过大时不继承（可能是新的一轮发言）


def _merge_ghost(
    labels: np.ndarray,
    durations: np.ndarray,
    min_total_sec: float = 30.0,
    min_count: int = 8,
) -> np.ndarray:
    """R4：清掉"幽灵簇"。

    幽灵簇的典型特征不是"占比小"，而是"又少又短"——几段咳嗽、笑声被聚成一类。
    如果只看占比，会把真实场景里"说话少的那一方"误杀（比如一方只说了 40 秒，
    占全程 5%），所以这里用绝对时长 + 段数双条件。
    """
    uniq = [int(c) for c in set(labels.tolist()) if c >= 0]
    if len(uniq) < 2:
        return labels
    weights = {c: float(durations[labels == c].sum()) for c in uniq}
    counts = {c: int((labels == c).sum()) for c in uniq}
    major = max(weights, key=lambda c: weights[c])
    out = labels.copy()
    merged = []
    for c in uniq:
        if c == major:
            continue
        if weights[c] < min_total_sec and counts[c] < min_count:
            out[labels == c] = major
            merged.append((c, round(weights[c], 1), counts[c]))
    if merged:
        log.debug("R4 合并幽灵簇 %s → %s", merged, major)
    return out


def _inherit_short(
    labels: np.ndarray, starts: np.ndarray, ends: np.ndarray, short_ms: float
) -> np.ndarray:
    """R1：过短的段继承前一段的说话人。"""
    out = labels.copy()
    prev_label = -1
    prev_end = -1e9
    fixed = 0
    for i in range(len(out)):
        if out[i] < 0:
            continue
        dur_ms = (ends[i] - starts[i]) * 1000
        gap = starts[i] - prev_end
        if dur_ms < short_ms and prev_label >= 0 and gap <= INHERIT_MAX_GAP_S:
            if out[i] != prev_label:
                out[i] = prev_label
                fixed += 1
        prev_label = out[i]
        prev_end = ends[i]
    if fixed:
        log.debug("R1 碎段归并 %d 段", fixed)
    return out


def _fix_isolated(
    labels: np.ndarray, starts: np.ndarray, ends: np.ndarray, isolated_ms: float
) -> np.ndarray:
    """R2：被同一说话人前后包夹的短段，判为聚类抖动并改正。"""
    out = labels.copy()
    fixed = 0
    for i in range(1, len(out) - 1):
        if out[i] < 0:
            continue
        dur_ms = (ends[i] - starts[i]) * 1000
        if (
            dur_ms < isolated_ms
            and out[i - 1] == out[i + 1]
            and out[i] != out[i - 1]
        ):
            out[i] = out[i - 1]
            fixed += 1
    if fixed:
        log.debug("R2 孤立段修正 %d 段", fixed)
    return out


def fill_invalid(labels: np.ndarray) -> np.ndarray:
    """把未参与聚类的段（-1）用相邻段的标签补上。

    这些段太短、提不出可靠声纹，但它们的内容仍属于某个人，
    按"就近继承"比随便猜更合理。
    """
    out = np.asarray(labels, dtype=int).copy()
    last = -1
    for i in range(len(out)):
        if out[i] >= 0:
            last = out[i]
        elif last >= 0:
            out[i] = last
    nxt = -1
    for i in range(len(out) - 1, -1, -1):
        if out[i] >= 0:
            nxt = out[i]
        elif nxt >= 0:
            out[i] = nxt
    out[out < 0] = 0
    return out


def smooth_labels(
    labels: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    short_ms: float = SHORT_MS,
    isolated_ms: float = ISOLATED_MS,
    ghost_min_sec: float = GHOST_MIN_SEC,
    ghost_min_count: int = GHOST_MIN_COUNT,
    enable_ghost: bool = True,
    enable: bool = True,
) -> np.ndarray:
    """依次执行 R4 → R1 → R2，返回修正后的标签。

    ``enable_ghost`` 在 fixed_k 模式下应关闭：人数已经被用户限定，
    不存在"多出第三个人"的问题，此时任何簇都是真实说话人，
    哪怕它只占 4 秒。
    """
    if not enable or len(labels) == 0:
        return labels
    out = labels
    if enable_ghost:
        out = _merge_ghost(out, np.clip(ends - starts, 0, None),
                           ghost_min_sec, ghost_min_count)
    out = _inherit_short(out, starts, ends, short_ms)
    out = _fix_isolated(out, starts, ends, isolated_ms)
    return out


def merge_into_turns(
    labels: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    texts: list[str],
    gap_sec: float = 1.5,
) -> list[dict]:
    """R5：把相邻同说话人的段合成一个轮次。

    这是最终展示单位：一个轮次 = 一个人连续说的一段话。
    """
    turns: list[dict] = []
    for i in range(len(labels)):
        lab = int(labels[i])
        text = (texts[i] or "").strip()
        if not text:
            continue
        if (
            turns
            and turns[-1]["speaker"] == lab
            and (starts[i] - turns[-1]["end"]) <= gap_sec
        ):
            turns[-1]["end"] = float(ends[i])
            turns[-1]["text"] += text
            turns[-1]["segment_count"] += 1
        else:
            turns.append(
                {
                    "speaker": lab,
                    "start": float(starts[i]),
                    "end": float(ends[i]),
                    "text": text,
                    "segment_count": 1,
                }
            )
    return turns
