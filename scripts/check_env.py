"""环境自检：一次性看清哪里没装好。

出问题时先跑这个，比逐条试错快得多。
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ROWS: list[tuple[str, str, bool, str]] = []


def add(name: str, value: str, ok: bool, hint: str = "") -> None:
    ROWS.append((name, value, ok, hint))


def check_python() -> None:
    v = sys.version_info
    add("Python", f"{v.major}.{v.minor}.{v.micro}", v.major == 3 and v.minor == 11,
        "建议使用 Python 3.11")


def check_torch() -> None:
    try:
        import torch

        add("torch", torch.__version__, True)
        cuda_ok = torch.cuda.is_available()
        if cuda_ok:
            name = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            free, total = torch.cuda.mem_get_info()
            add("CUDA", f"{name} sm_{cap[0]}{cap[1]} 可用 {(total-free)/1024**3:.1f}/{total/1024**3:.1f}GB",
                True)
            if "cpu" in torch.__version__:
                add("torch 变体", "CPU 版", False, "装成了 CPU 版，需重装 cu121 版本")
        else:
            add("CUDA", "不可用", False, "显卡驱动或 torch 版本不匹配")
    except Exception as e:  # noqa: BLE001
        add("torch", f"导入失败: {e}", False, "运行 安装环境.bat")


def check_deps() -> None:
    for mod, label in [
        ("funasr", "funasr"),
        ("modelscope", "modelscope"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("sklearn", "scikit-learn"),
        ("soundfile", "soundfile"),
        ("yaml", "pyyaml"),
    ]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            add(label, ver, True)
        except Exception as e:  # noqa: BLE001
            add(label, f"缺失: {e}", False, "pip install -r requirements.txt")


def check_ffmpeg() -> None:
    from app.config import load_config
    from app.core.audio import resolve_binary

    cfg = load_config()
    for name in ("ffmpeg", "ffprobe"):
        try:
            p = resolve_binary(name, cfg.paths.ffmpeg)
            add(name, p, True)
        except Exception as e:  # noqa: BLE001
            add(name, str(e), False, "安装 ffmpeg 并在 config.yaml 指定路径")


def check_models() -> None:
    from app.config import load_config

    cfg = load_config()
    checks = [
        ("SenseVoiceSmall", cfg.model_dir_sensevoice, True),
        ("fsmn-vad", cfg.model_dir_vad, True),
        ("cam++（M2）", cfg.model_dir_campplus, False),
    ]
    for label, path, required in checks:
        if not path.is_dir():
            add(label, "目录不存在", not required, "运行 下载模型.bat")
            continue
        has_cfg = (path / "config.yaml").is_file()
        has_weight = any(
            (path / w).is_file() for w in ("model.pt", "campplus_cn_common.bin", "model.bin")
        )
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024
        ok = has_cfg and has_weight
        detail = f"{size:.0f} MB" + ("" if ok else "，文件不完整")
        add(label, detail, ok or not required, "运行 下载模型.bat 重新下载")


def check_paths() -> None:
    from app.config import load_config

    cfg = load_config()
    for label, p in [("output", cfg.paths.output), ("cache", cfg.paths.cache)]:
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / "._write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            add(f"目录 {label}", str(p), True)
        except Exception as e:  # noqa: BLE001
            add(f"目录 {label}", f"不可写: {e}", False)


def render() -> None:
    print("=" * 72)
    print(f"RecordReg 环境自检   {platform.system()} {platform.release()}")
    print(f"项目根目录: {PROJECT_ROOT}")
    print("=" * 72)
    w = max(len(r[0]) for r in ROWS) + 2
    for name, value, ok, hint in ROWS:
        mark = "✓" if ok else "✗"
        print(f" {mark} {name.ljust(w)}{value}")
        if not ok and hint:
            print(f"   {' ' * w}→ {hint}")
    print("=" * 72)
    bad = [r for r in ROWS if not r[2]]
    if bad:
        print(f"存在 {len(bad)} 项问题，请按上面的提示处理。")
    else:
        print("全部通过，可以运行 启动M1.bat")


def main() -> int:
    check_python()
    check_torch()
    check_deps()
    check_ffmpeg()
    check_models()
    check_paths()
    render()
    return 0 if all(r[2] for r in ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
