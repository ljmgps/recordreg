"""模型注册中心：单例、懒加载、常驻显存。

为什么坚持用单例：
100 个文件若每个都重新加载模型，光加载就要几十秒 × 100，且显存会反复分配释放。
模型只加载一次，处理完所有文件才释放。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import Config
from .errors import ModelMissing

log = logging.getLogger(__name__)


def verify_model_dir(path: Path, require_files: tuple[str, ...] = ("config.yaml", "model.pt")) -> None:
    if not path.is_dir():
        raise ModelMissing(str(path), "目录不存在，请运行 下载模型.bat")
    missing = [f for f in require_files if not (path / f).is_file()]
    if missing:
        raise ModelMissing(str(path), f"缺少 {', '.join(missing)}，请重新下载")


def detect_device(prefer: str = "auto") -> str:
    if prefer and prefer.lower() == "cpu":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
    except Exception as e:  # noqa: BLE001
        log.warning("CUDA 探测失败，回退 CPU：%s", e)
    return "cpu"


def _gpu_mem_str() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return f"显存 {(total - free) / 1024**3:.2f}/{total / 1024**3:.1f}GB"
    except Exception:  # noqa: BLE001
        pass
    return ""


class ModelRegistry:
    _instances: dict[tuple[str, str], "ModelRegistry"] = {}

    def __init__(self, cfg: Config, device: str | None = None) -> None:
        self.cfg = cfg
        self.device = device or detect_device()
        self._vad = None
        self._asr = None
        self._sv = None

    @classmethod
    def get(cls, cfg: Config, device: str | None = None) -> "ModelRegistry":
        key = (str(cfg.paths.models), device or "auto")
        if key not in cls._instances:
            cls._instances[key] = cls(cfg, device)
        return cls._instances[key]

    # ---- 模型加载 ----
    def vad(self):
        if self._vad is None:
            verify_model_dir(self.cfg.model_dir_vad)
            from funasr import AutoModel

            t0 = time.time()
            log.info("加载 VAD 模型（%s）...", self.device)
            self._vad = AutoModel(
                model=str(self.cfg.model_dir_vad),
                device=self.device,
                disable_update=True,
                disable_pbar=True,
                disable_log=True,
            )
            log.info("VAD 就绪，用时 %.1fs %s", time.time() - t0, _gpu_mem_str())
        return self._vad

    def asr(self):
        if self._asr is None:
            verify_model_dir(self.cfg.model_dir_sensevoice, ("config.yaml", "model.pt", "tokens.json"))
            from funasr import AutoModel

            t0 = time.time()
            log.info("加载 SenseVoice 模型（%s）...", self.device)
            self._asr = AutoModel(
                model=str(self.cfg.model_dir_sensevoice),
                device=self.device,
                disable_update=True,
                disable_pbar=True,
                disable_log=True,
            )
            log.info("SenseVoice 就绪，用时 %.1fs %s", time.time() - t0, _gpu_mem_str())
        return self._asr

    def sv(self):
        """cam++ 声纹模型。权重文件是 campplus_cn_common.bin，不是 model.pt。"""
        if self._sv is None:
            verify_model_dir(self.cfg.model_dir_campplus, ("config.yaml",))
            from funasr import AutoModel

            t0 = time.time()
            log.info("加载 cam++ 声纹模型（%s）...", self.device)
            self._sv = AutoModel(
                model=str(self.cfg.model_dir_campplus),
                device=self.device,
                disable_update=True,
                disable_pbar=True,
                disable_log=True,
            )
            log.info("cam++ 就绪，用时 %.1fs %s", time.time() - t0, _gpu_mem_str())
        return self._sv

    def warmup(self) -> None:
        """用一段 1 秒静音预热，把首次调用的 CUDA kernel 编译开销挪到批处理之前。"""
        import numpy as np

        from .vad import Segment

        try:
            t0 = time.time()
            pcm = (np.random.randn(16000) * 0.001).astype(np.float32)
            from .audio import write_wav

            tmp = self.cfg.paths.cache / "_warmup.wav"
            write_wav(tmp, pcm, 16000)
            self.vad().generate(input=str(tmp), cache={}, disable_update=True)
            seg = Segment(index=1, start_ms=0, end_ms=1000)
            self.asr().generate(
                input=str(tmp), cache={}, language="auto", use_itn=True, disable_update=True
            )
            tmp.unlink(missing_ok=True)
            log.info("预热完成，用时 %.1fs %s", time.time() - t0, _gpu_mem_str())
        except Exception as e:  # noqa: BLE001
            log.debug("预热跳过：%s", e)

    def release(self) -> None:
        self._vad = None
        self._asr = None
        self._sv = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    @property
    def loaded(self) -> bool:
        return self._vad is not None and self._asr is not None
