"""单文件转写流水线：解码 → VAD → 识别 → 声纹 → 聚类 → 平滑 → 组装。"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import Config
from . import audio as audio_mod
from . import cluster as cluster_mod
from . import embed as embed_mod
from . import smooth as smooth_mod
from . import vad as vad_mod
from .asr import AsrResult, transcribe, transcribe_hotword_correct
from .errors import DecodeError, NoSpeechFound
from .registry import ModelRegistry

log = logging.getLogger(__name__)

# 单次送入 ASR 的最大切片数。长录音可能产生数千个切片，
# 一次性传给 funasr 会让它的动态 batch 缓冲占用失控，分批更稳。
ASR_CHUNK_SEGMENTS = 200


def spk_name(label: int) -> str:
    return f"SPK_{label + 1}"


def _report(progress, stage: str, frac: float) -> None:
    """向 GUI 回报进度；任何异常都不能影响主流程。"""
    if progress is None:
        return
    try:
        progress(stage, max(0.0, min(1.0, frac)))
    except Exception:  # noqa: BLE001
        pass


@dataclass
class SegmentResult:
    index: int
    start: float
    end: float
    text: str = ""
    lang: str = ""
    emotion: str = ""
    event: str = ""
    speaker: str | None = None
    raw_speaker: str | None = None
    smoothed: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class SpeakerStat:
    id: str
    total_sec: float = 0.0
    segment_count: int = 0
    turn_count: int = 0
    char_count: int = 0


@dataclass
class DocResult:
    source: Path
    info: audio_mod.AudioInfo
    sha256: str
    segments: list[SegmentResult] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    speakers: list[SpeakerStat] = field(default_factory=list)
    dropped_segments: int = 0
    elapsed_sec: float = 0.0
    device: str = "cpu"
    language: str = "unknown"
    cluster_mode: str = "fixed_k"
    cluster_k: int = 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def rtf(self) -> float:
        d = self.info.duration_sec or 1.0
        return self.elapsed_sec / d

    @property
    def has_speaker(self) -> bool:
        return len(self.speakers) > 1


def _segment_speakers(
    pieces: list[np.ndarray],
    segments: list[vad_mod.Segment],
    cfg: Config,
    registry: ModelRegistry,
    progress=None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """返回 (最终标签数组, 平滑前的标签数组)。"""
    n = len(segments)
    starts = np.array([s.start_sec for s in segments], dtype=np.float64)
    ends = np.array([s.end_sec for s in segments], dtype=np.float64)
    durations = np.clip(ends - starts, 0.0, None)

    t0 = time.time()

    def _p(done: int, total: int) -> None:
        _report(progress, "声纹", 0.65 + 0.25 * (done / max(total, 1)))

    embeddings = embed_mod.extract_embeddings(registry.sv(), pieces, progress=_p)
    log.debug("声纹提取 %.2fs，%d 段 × %d 维", time.time() - t0, len(embeddings),
              embeddings.shape[1] if len(embeddings) else 0)

    if len(embeddings) != n:
        log.warning("声纹条数 %d 与段数 %d 不符，跳过说话人分离", len(embeddings), n)
        return np.zeros(n, dtype=int), None

    # 太短的段提不出可靠声纹，不参与聚类
    valid = (durations * 1000) >= cfg.speaker.min_segment_ms
    valid &= np.any(embeddings != 0, axis=1)
    if valid.sum() < 2:
        log.warning("可用于聚类的段不足（%d），退化为单说话人", int(valid.sum()))
        return np.zeros(n, dtype=int), None

    raw_labels = cluster_mod.cluster_speakers(
        embeddings, durations, valid,
        mode=cfg.speaker.mode,
        k=cfg.speaker.k,
        max_k=cfg.speaker.max_k,
    )
    if log.isEnabledFor(logging.DEBUG):
        for c in sorted({int(x) for x in raw_labels if x >= 0}):
            m = raw_labels == c
            log.debug("簇 %d: %d 段, 总时长 %.1fs", c, int(m.sum()), float(durations[m].sum()))
    origin = smooth_mod.fill_invalid(raw_labels)

    if cfg.speaker.smoothing:
        # fixed_k 下人数已被限定，不启用幽灵簇合并（否则会吃掉真实的少数方）；
        # auto 模式才需要较激进的判定。
        auto_mode = cfg.speaker.mode == "auto"
        smoothed = smooth_mod.smooth_labels(
            origin, starts, ends,
            ghost_min_sec=30.0,
            ghost_min_count=8,
            enable_ghost=auto_mode,
            enable=True,
        )
    else:
        smoothed = origin

    final, mapping = cluster_mod.canonicalize(smoothed, durations)
    # 平滑前的标签套用同一套编号，才能逐段判断"是否被改过"
    origin_aligned = np.array(
        [mapping.get(int(x), int(x)) for x in origin], dtype=int
    )
    return final, origin_aligned


def process_file(
    path: Path,
    cfg: Config,
    registry: ModelRegistry,
    sha: str | None = None,
    progress=None,
) -> DocResult:
    """处理单个音频文件，返回结构化结果。异常由调用方捕获。

    ``sha`` 可由调用方预先算好传入，避免同一文件被哈希两遍。
    ``progress(stage, frac)`` 可选，用于 GUI 显示阶段与百分比。
    """
    t0 = time.time()
    _report(progress, "准备", 0.01)
    path = Path(path)
    info = audio_mod.probe(path, cfg.paths.ffmpeg)
    if info.is_low_quality_source:
        log.warning("源音频采样率 %d Hz 偏低，识别准确率可能受影响", info.sample_rate)

    sha = sha or audio_mod.sha256_of(path)
    work_dir = cfg.paths.cache / sha[:12]
    work_dir.mkdir(parents=True, exist_ok=True)
    wav_path = work_dir / "audio16k.wav"

    try:
        # 1. 解码
        t_dec = time.time()
        _report(progress, "解码", 0.03)
        audio_mod.decode_to_wav(
            path, wav_path,
            sample_rate=cfg.audio.target_sample_rate,
            channels=cfg.audio.channels,
            ffmpeg_exe=cfg.paths.ffmpeg,
        )
        # .aac 等容器 ffprobe 常读不出时长，用解码后的 WAV 实际长度补齐；
        # 同时校验解码结果，避免把损坏文件送进模型（会报 mat1/mat2 这类难懂的错）
        real_dur = audio_mod.wav_duration(wav_path)
        if real_dur < 0.5:
            raise DecodeError(
                str(path), f"解码后仅 {real_dur:.2f} 秒有效音频，文件可能已损坏"
            )
        if info.duration_sec <= 0:
            info.duration_sec = real_dur
        log.debug("解码完成 %.2fs，时长 %.1fs", time.time() - t_dec, info.duration_sec)

        # 2. VAD
        t_vad = time.time()
        _report(progress, "分段", 0.15)
        raw = vad_mod.detect_speech(
            registry.vad(), str(wav_path),
            max_segment_ms=cfg.vad.max_single_segment_time,
            speech_noise_thres=cfg.vad.speech_noise_thres,
            max_end_silence_ms=cfg.vad.max_end_silence_time,
        )
        if not raw:
            raise NoSpeechFound(str(path))

        data, sr = audio_mod.read_wav(wav_path)
        segments, dropped = vad_mod.normalize_segments(
            raw,
            min_ms=cfg.vad.min_segment_time,
            max_ms=cfg.vad.max_single_segment_time,
            merge_gap_ms=cfg.vad.merge_gap_time,
            split_target_ms=cfg.vad.split_target_time,
            split_if_over_ms=cfg.vad.split_if_over_time,
            data=data,
            sr=sr,
        )
        log.debug("VAD 完成 %.2fs，原始 %d 段 → 规整 %d 段（丢弃 %d）",
                  time.time() - t_vad, len(raw), len(segments), dropped)
        if not segments:
            raise NoSpeechFound(str(path))

        # 3. 内存切片（不落盘，避免长录音产生几百个小文件）
        wav_pieces = audio_mod.slice_segments_mem(
            wav_path, [s.as_tuple() for s in segments]
        )
        if len(wav_pieces) != len(segments):
            log.warning("切片数 %d 与段数 %d 不一致，按较短的截断",
                        len(wav_pieces), len(segments))
            segments = segments[: len(wav_pieces)]

        # 4. 识别（分批，防止长录音一次传入上千个切片导致内存暴涨）
        t_asr = time.time()
        _report(progress, "识别", 0.30)
        asr_results: list[AsrResult] = []
        total_pieces = max(len(wav_pieces), 1)
        for i in range(0, len(wav_pieces), ASR_CHUNK_SEGMENTS):
            piece_batch = wav_pieces[i: i + ASR_CHUNK_SEGMENTS]
            asr_results.extend(
                transcribe(
                    registry.asr(), piece_batch,
                    language=cfg.asr.language,
                    use_itn=cfg.asr.use_itn,
                    batch_size_s=cfg.asr.batch_size_s,
                )
            )
            _report(progress, "识别", 0.30 + 0.32 * min(i + ASR_CHUNK_SEGMENTS, total_pieces) / total_pieces)
        log.debug("识别完成 %.2fs", time.time() - t_asr)

        # 5. 热词纠正（config 为空时是空操作）
        asr_results = transcribe_hotword_correct(asr_results, cfg.hotwords)

        # 6. 说话人分离
        t_spk = time.time()
        _report(progress, "声纹", 0.65)
        if cfg.speaker.enable:
            labels, origin_labels = _segment_speakers(
                wav_pieces, segments, cfg, registry, progress=progress
            )
        else:
            labels = np.zeros(len(segments), dtype=int)
            origin_labels = None
        _report(progress, "聚类", 0.93)
        log.debug("说话人分离 %.2fs", time.time() - t_spk)

        # 7. 组装
        kept: list[tuple[int, vad_mod.Segment, AsrResult]] = []
        for i, (seg, r) in enumerate(zip(segments, asr_results)):
            text = (r.text or "").strip()
            if text:
                kept.append((i, seg, r))

        if not kept:
            raise NoSpeechFound(str(path))

        starts = np.array([seg.start_sec for _, seg, _ in kept])
        ends = np.array([seg.end_sec for _, seg, _ in kept])
        kept_labels = np.array([int(labels[i]) for i, _, _ in kept])
        texts = [r.text for _, _, r in kept]

        turns = smooth_mod.merge_into_turns(
            kept_labels, starts, ends, texts, gap_sec=cfg.speaker.turn_gap_sec
        )

        results: list[SegmentResult] = []
        for idx, (i, seg, r) in enumerate(kept, start=1):
            lab = int(labels[i])
            raw_lab = int(origin_labels[i]) if origin_labels is not None else lab
            results.append(
                SegmentResult(
                    index=idx,
                    start=seg.start_sec,
                    end=seg.end_sec,
                    text=r.text,
                    lang=r.lang,
                    emotion=r.emotion,
                    event=r.event,
                    speaker=spk_name(lab) if cfg.speaker.enable else None,
                    raw_speaker=spk_name(raw_lab) if (raw_lab != lab) else None,
                    smoothed=bool(raw_lab != lab),
                )
            )

        # 说话人统计
        stats: dict[str, SpeakerStat] = {}
        for res in results:
            if res.speaker:
                st = stats.setdefault(res.speaker, SpeakerStat(id=res.speaker))
                st.total_sec += res.duration
                st.segment_count += 1
                st.char_count += len(res.text)
        for turn in turns:
            key = spk_name(int(turn["speaker"])) if cfg.speaker.enable else None
            if key and key in stats:
                stats[key].turn_count += 1
        speakers = sorted(stats.values(), key=lambda s: s.id)

        # 整体语种按段投票，避免单段误判直接写进元数据
        lang_counts = Counter(s.lang for s in results if s.lang)
        dominant = lang_counts.most_common(1)[0][0] if lang_counts else "unknown"

        k_used = len({int(x) for x in kept_labels}) if cfg.speaker.enable else 0

        _report(progress, "整理", 0.98)
        return DocResult(
            source=path,
            info=info,
            sha256=sha,
            segments=results,
            turns=turns,
            speakers=speakers,
            dropped_segments=dropped,
            elapsed_sec=time.time() - t0,
            device=registry.device,
            language=dominant,
            cluster_mode=cfg.speaker.mode if cfg.speaker.enable else "off",
            cluster_k=k_used,
        )
    finally:
        if not cfg.audio.keep_cache_wav:
            # 必须逐文件删除：一次性 rmtree 几百个切片会触发批量删除保护，
            # 导致进程被直接终止（长录音"闪退"的根因）
            audio_mod.safe_remove_dir(work_dir)
