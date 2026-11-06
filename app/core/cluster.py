"""说话人聚类：受约束的层次聚类。

核心思路（见设计文档 §6.1）：2 人对谈的先验非常强，直接告诉算法"只有 2 个人"，
比让算法自己猜人数准确得多。自动模式只在设置里显式切换时才用。
"""

from __future__ import annotations

import logging

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score

log = logging.getLogger(__name__)

# 两类的最低分离度。低于此值认为"其实是一个人"，退回单说话人。
MIN_SILHOUETTE = 0.10
# 低于这个段数时不启用分离度保护（样本太少，silhouette 不可靠）
MIN_SILHOUETTE_SAMPLES = 12


def _agglomerative(X: np.ndarray, n_clusters: int) -> np.ndarray:
    """层次聚类。

    用 complete（最远距离）而不是 average：在余弦空间里 average 会把
    存在"链式相似"的一组点并成一个大簇，结果是 90% 的段归一个人、
    剩下几个离群段归另一个人——实测 13 分钟对话被分成 169:13。
    complete 强制簇间保持最大距离，两类更均衡，silhouette 也更高。
    """
    return AgglomerativeClustering(
        n_clusters=n_clusters,
        metric="cosine",
        linkage="complete",
    ).fit_predict(X)


def _estimate_k(X: np.ndarray, durations: np.ndarray, max_k: int = 4) -> int:
    """自动估计人数：silhouette 选优，并对"碎片簇"施加惩罚。

    真实对话里常出现"咳嗽/笑声被聚成第三个人"的碎片簇，
    表现为某簇总时长占比极低，这里直接压低它的分数。
    """
    n = len(X)
    best_k, best_score = 2, -2.0
    total = float(durations.sum()) or 1.0

    for k in range(2, min(max_k, n - 1) + 1):
        try:
            labels = _agglomerative(X, k)
        except Exception:  # noqa: BLE001
            continue
        if len(set(labels)) < 2:
            continue
        try:
            score = float(silhouette_score(X, labels, metric="cosine"))
        except Exception:  # noqa: BLE001
            continue
        props = [durations[labels == c].sum() for c in set(labels)]
        min_prop = min(props) / total if total else 0.0
        if min_prop < 0.10:
            score -= 0.15
        log.debug("k=%d silhouette=%.3f min_cluster_ratio=%.2f", k, score, min_prop)
        if score > best_score:
            best_k, best_score = k, score
    return best_k


def cluster_speakers(
    embeddings: np.ndarray,
    durations: np.ndarray,
    valid: np.ndarray | None = None,
    mode: str = "fixed_k",
    k: int = 2,
    max_k: int = 4,
) -> np.ndarray:
    """返回每段的说话人编号（0 起始）；无效段标记为 -1。"""
    n = len(embeddings)
    labels = np.full(n, -1, dtype=int)
    if valid is None:
        valid = np.ones(n, dtype=bool)

    idx = np.where(valid)[0]
    if len(idx) == 0:
        return labels
    X = embeddings[idx]

    if len(idx) == 1:
        labels[idx] = 0
        return labels

    if mode == "auto":
        k_eff = _estimate_k(X, durations[idx], max_k=max_k)
    else:
        k_eff = max(1, min(int(k), len(idx)))

    if k_eff <= 1:
        labels[idx] = 0
        return labels

    try:
        sub = _agglomerative(X, k_eff)
    except Exception as e:  # noqa: BLE001
        log.warning("聚类失败，退化为单说话人: %s", e)
        labels[idx] = 0
        return labels

    # 分离度太低说明数据本身就不支持分成这么多人（比如单人独白被强行要求 K=2），
    # 这种情况下强行切分会把一个人的话割裂给两个人，宁可退回单说话人。
    # 但样本太少时 silhouette 方差很大（8 段的短录音会被误判），此时信任用户设定的 K。
    if k_eff >= 2 and len(X) >= MIN_SILHOUETTE_SAMPLES and len(set(sub.tolist())) >= 2:
        try:
            sil = float(silhouette_score(X, sub, metric="cosine"))
        except Exception:  # noqa: BLE001
            sil = 1.0
        if sil < MIN_SILHOUETTE:
            log.info("说话人分离度偏低（silhouette=%.2f < %.2f），按单说话人处理",
                     sil, MIN_SILHOUETTE)
            labels[idx] = 0
            return labels

    labels[idx] = sub
    return labels


def canonicalize(labels: np.ndarray, durations: np.ndarray) -> tuple[np.ndarray, dict[int, int]]:
    """把编号稳定下来：说话时长最长的簇固定为 0。

    聚类输出的编号顺序是不确定的（同一文件两次跑可能互换），
    按总时长排序后，结果就具备可重复性。同时返回映射表，
    便于把平滑前的标签对齐到同一套编号上做对比。
    """
    uniq = sorted(set(int(x) for x in labels if x >= 0))
    if not uniq:
        return labels, {}
    weights = {c: float(durations[labels == c].sum()) for c in uniq}
    order = sorted(uniq, key=lambda c: (-weights[c], c))
    mapping = {old: new for new, old in enumerate(order)}
    out = labels.copy()
    for old, new in mapping.items():
        out[labels == old] = new
    return out, mapping
