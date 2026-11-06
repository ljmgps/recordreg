"""配置加载：读取 config.yaml，缺失字段用默认值补齐，相对路径按项目根解析。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _as_path(value: str | os.PathLike, root: Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p)


@dataclass
class PathsCfg:
    models: Path = field(default_factory=lambda: PROJECT_ROOT / "models")
    output: Path = field(default_factory=lambda: PROJECT_ROOT / "output")
    cache: Path = field(default_factory=lambda: PROJECT_ROOT / "cache")
    db: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "app.db")
    ffmpeg: str = ""


@dataclass
class AudioCfg:
    target_sample_rate: int = 16000
    channels: int = 1
    keep_cache_wav: bool = False


@dataclass
class VadCfg:
    max_single_segment_time: int = 30000
    min_segment_time: int = 400
    merge_gap_time: int = 200
    split_target_time: int = 5000
    split_if_over_time: int = 8000
    speech_noise_thres: float = 0.6
    max_end_silence_time: int = 500


@dataclass
class AsrCfg:
    language: str = "auto"
    use_itn: bool = True
    batch_size_s: int = 120


@dataclass
class SpeakerCfg:
    enable: bool = True
    mode: str = "fixed_k"        # fixed_k / auto
    k: int = 2                   # mode=fixed_k 时的说话人数
    max_k: int = 4               # mode=auto 时的搜索上限
    min_segment_ms: int = 600    # 短于此值的段不参与聚类，由相邻段继承
    smoothing: bool = True
    turn_gap_sec: float = 1.5    # 相邻同人段合并成一个轮次的间隔阈值


@dataclass
class OutputCfg:
    write_markdown: bool = True
    write_json: bool = True


@dataclass
class Config:
    paths: PathsCfg
    audio: AudioCfg
    vad: VadCfg
    asr: AsrCfg
    output: OutputCfg
    speaker: SpeakerCfg = field(default_factory=SpeakerCfg)
    hotwords: list[list[str]] | list[str] = field(default_factory=list)
    source: Path | None = None

    # 常用模型目录便捷属性
    @property
    def model_dir_sensevoice(self) -> Path:
        return self.paths.models / "SenseVoiceSmall"

    @property
    def model_dir_vad(self) -> Path:
        return self.paths.models / "speech_fsmn_vad_zh-cn-16k-common-pytorch"

    @property
    def model_dir_campplus(self) -> Path:
        return self.paths.models / "speech_campplus_sv_zh-cn_16k-common"

    def ensure_dirs(self) -> None:
        for d in (self.paths.output, self.paths.cache):
            d.mkdir(parents=True, exist_ok=True)


def _pick(section: dict[str, Any], key: str, default: Any) -> Any:
    v = section.get(key, default)
    return default if v is None else v


def load_config(path: str | os.PathLike | None = None) -> Config:
    """加载配置。文件不存在时返回全默认配置，不抛异常。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    root = PROJECT_ROOT

    p = raw.get("paths") or {}
    a = raw.get("audio") or {}
    v = raw.get("vad") or {}
    s = raw.get("asr") or {}
    o = raw.get("output") or {}
    sp = raw.get("speaker") or {}

    paths = PathsCfg(
        models=_as_path(_pick(p, "models", "models"), root),
        output=_as_path(_pick(p, "output", "output"), root),
        cache=_as_path(_pick(p, "cache", "cache"), root),
        db=_as_path(_pick(p, "db", "data/app.db"), root),
        ffmpeg=str(_pick(p, "ffmpeg", "") or ""),
    )
    audio = AudioCfg(
        target_sample_rate=int(_pick(a, "target_sample_rate", 16000)),
        channels=int(_pick(a, "channels", 1)),
        keep_cache_wav=bool(_pick(a, "keep_cache_wav", False)),
    )
    vad = VadCfg(
        max_single_segment_time=int(_pick(v, "max_single_segment_time", 30000)),
        min_segment_time=int(_pick(v, "min_segment_time", 400)),
        merge_gap_time=int(_pick(v, "merge_gap_time", 200)),
        split_target_time=int(_pick(v, "split_target_time", 5000)),
        split_if_over_time=int(_pick(v, "split_if_over_time", 8000)),
        speech_noise_thres=float(_pick(v, "speech_noise_thres", 0.6)),
        max_end_silence_time=int(_pick(v, "max_end_silence_time", 500)),
    )
    asr = AsrCfg(
        language=str(_pick(s, "language", "auto")),
        use_itn=bool(_pick(s, "use_itn", True)),
        batch_size_s=int(_pick(s, "batch_size_s", 120)),
    )
    out = OutputCfg(
        write_markdown=bool(_pick(o, "write_markdown", True)),
        write_json=bool(_pick(o, "write_json", True)),
    )
    speaker = SpeakerCfg(
        enable=bool(_pick(sp, "enable", True)),
        mode=str(_pick(sp, "mode", "fixed_k")),
        k=int(_pick(sp, "k", 2)),
        max_k=int(_pick(sp, "max_k", 4)),
        min_segment_ms=int(_pick(sp, "min_segment_ms", 600)),
        smoothing=bool(_pick(sp, "smoothing", True)),
        turn_gap_sec=float(_pick(sp, "turn_gap_sec", 1.5)),
    )

    return Config(
        paths=paths,
        audio=audio,
        vad=vad,
        asr=asr,
        output=out,
        speaker=speaker,
        hotwords=raw.get("hotwords") or [],
        source=cfg_path if cfg_path.exists() else None,
    )


def setup_runtime_env() -> None:
    """在导入 torch/funasr 之前调用，锁定离线模式，杜绝任何联网下载。"""
    root = str(PROJECT_ROOT)
    os.environ.setdefault("OFFLINE_MODE", "1")
    os.environ.setdefault("MODELSCOPE_CACHE", os.path.join(root, "cache", "modelscope"))
    os.environ.setdefault("HF_HOME", os.path.join(root, "cache", "hf"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # funasr 内部会检查该变量，非 0 时不尝试从远端拉取模型
    os.environ.setdefault("FUNASR_DISABLE_UPDATE", "1")
    os.environ.setdefault("PYTHONUTF8", "1")


def setup_logging(verbose: bool = False, log_file: Path | None = None) -> None:
    import logging

    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    root.addHandler(handler)

    # 同时落盘：窗口闪退（进程崩溃）时，这是唯一能事后查的东西
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:  # noqa: BLE001
            pass

    # 压制第三方库的噪音
    for noisy in ("modelscope", "funasr", "urllib3", "filelock", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def make_stdout_safe() -> None:
    """控制台编码异常不应让程序崩溃。

    Windows 控制台默认 GBK，模型偶尔会输出表情符号或生僻字，
    此时 print 会抛 UnicodeEncodeError 并导致进程退出——用 replace 兜住。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
