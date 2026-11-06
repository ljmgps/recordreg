"""SenseVoice 识别封装。

要点：
- 只接受本地模型目录，绝不传模型 ID，杜绝联网自动下载。
- SenseVoice 输出富文本标记（<|zh|><|NEUTRAL|><|Speech|><|woitn|>正文），
  需要剥离标记并保留语种/情感/事件三个元信息。
- 批量识别后必须校验返回条数与输入一致，不一致则退化为逐条识别。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<\|([^|]*)\|>")

# SenseVoice 的 rich_transcription_postprocess 会把情感标签替换成 emoji（😡😊），
# 但情感信息已经在 emotion 字段里结构化保存了，正文里再出现图表符号只会干扰阅读和检索。
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U0001F1E6-\U0001F1FF"
    "\u2600-\u27BF"
    "\uFE0F"
    "\u200D"
    "]+"
)

_KNOWN_META = {"zh", "en", "yue", "ja", "ko", "nospeech", "auto"}
_KNOWN_EMO = {
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "FEARFUL", "DISGUSTED", "SURPRISED",
    "EMO_UNKNOWN",
}
_KNOWN_EVENT = {
    "Speech", "BGM", "Applause", "Laughter", "Crying", "Cough", "Sneeze",
    "Breath", "Event_UNK",
}


@dataclass
class AsrResult:
    text: str = ""
    lang: str = ""
    emotion: str = ""
    event: str = ""
    raw: str = ""
    extra: dict = field(default_factory=dict)


def strip_emoji(text: str) -> str:
    """去掉 emoji 与多余的空白，保留中文标点。"""
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def parse_rich(raw: str) -> AsrResult:
    """解析 SenseVoice 富文本输出。

    优先使用官方后处理函数（会处理 ITN 细节），失败则回退到纯正则。
    """
    raw = (raw or "").strip()
    if not raw:
        return AsrResult()

    text = ""
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        text = rich_transcription_postprocess(raw).strip()
    except Exception:  # noqa: BLE001
        text = _TAG_RE.sub("", raw).strip()

    text = strip_emoji(text)

    tags = _TAG_RE.findall(raw)
    lang = next((t for t in tags if t in _KNOWN_META), "")
    emotion = next((t for t in tags if t in _KNOWN_EMO), "")
    event = next((t for t in tags if t in _KNOWN_EVENT), "")
    return AsrResult(text=text, lang=lang, emotion=emotion, event=event, raw=raw)


def _normalize_result_items(res) -> list:
    if res is None:
        return []
    if isinstance(res, dict):
        return [res]
    return list(res)


def _text_of(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or item.get("sentence") or "")
    return str(item or "")


def transcribe(
    asr_model,
    wav_paths: list,
    language: str = "auto",
    use_itn: bool = True,
    batch_size_s: int = 120,
) -> list[AsrResult]:
    """批量识别切片音频，返回与输入等长的结果列表。

    ``wav_paths`` 的元素可以是文件路径，也可以是内存中的 np.ndarray
    （funasr 原生支持后者，长录音因此不必在磁盘上生成几百个切片文件）。
    """
    if not wav_paths:
        return []

    inputs = [str(p) if isinstance(p, (str, Path)) else p for p in wav_paths]
    res = asr_model.generate(
        input=inputs,
        cache={},
        language=language,
        use_itn=use_itn,
        batch_size_s=batch_size_s,
        disable_update=True,
    )
    items = _normalize_result_items(res)

    if len(items) != len(inputs):
        log.warning(
            "批量识别返回条数不匹配（输入 %d，返回 %d），退化为逐条识别",
            len(inputs), len(items),
        )
        results: list[AsrResult] = []
        for one in inputs:
            r = asr_model.generate(
                input=one,
                cache={},
                language=language,
                use_itn=use_itn,
                disable_update=True,
            )
            its = _normalize_result_items(r)
            results.append(parse_rich(_text_of(its[0])) if its else AsrResult())
        return results

    return [parse_rich(_text_of(it)) for it in items]


def transcribe_hotword_correct(results: list[AsrResult], hotwords) -> list[AsrResult]:
    """高频名词纠正（M2 使用）。

    SenseVoice 不支持原生热词，这里用确定性替换兜底：
    先替换长的、再替换短的，避免短词先把长词的片段吃掉。
    """
    if not hotwords:
        return results
    pairs: list[tuple[str, str]] = []
    for item in hotwords:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append((str(item[0]), str(item[1])))
        elif isinstance(item, str) and "=" in item:
            k, v = item.split("=", 1)
            pairs.append((k.strip(), v.strip()))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)

    for r in results:
        for wrong, right in pairs:
            if wrong and wrong in r.text:
                r.text = r.text.replace(wrong, right)
    return results
