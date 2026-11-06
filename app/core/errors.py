"""自定义异常，便于在批处理中区分失败原因。"""

from __future__ import annotations


class RecordRegError(Exception):
    """业务异常基类。"""


class FFmpegNotFound(RecordRegError):
    pass


class DecodeError(RecordRegError):
    def __init__(self, path: str, detail: str = "") -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"音频解码失败: {path} {detail}".strip())


class ModelMissing(RecordRegError):
    def __init__(self, model_dir: str, hint: str = "") -> None:
        msg = f"模型目录不完整: {model_dir}"
        if hint:
            msg += f"（{hint}）"
        super().__init__(msg)


class NoSpeechFound(RecordRegError):
    def __init__(self, path: str = "") -> None:
        super().__init__(f"未检测到有效语音: {path}".strip())
