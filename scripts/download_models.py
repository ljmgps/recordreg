"""从 ModelScope 下载本地模型到 models/ 目录。

刻意不使用 funasr 的自动下载：程序运行时只认本地路径，
任何"悄悄联网拉模型"的行为都会在离线环境下直接失败。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_ROOT = PROJECT_ROOT / "models"

MODELS: list[dict] = [
    {
        "repo": "iic/SenseVoiceSmall",
        "local": "SenseVoiceSmall",
        "files": ("config.yaml", "model.pt", "tokens.json"),
        "size": "约 897 MB",
        "desc": "语音识别主模型",
    },
    {
        "repo": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "local": "speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "files": ("config.yaml", "model.pt"),
        "size": "约 4 MB",
        "desc": "VAD 语音活动检测",
    },
    {
        "repo": "iic/speech_campplus_sv_zh-cn_16k-common",
        "local": "speech_campplus_sv_zh-cn_16k-common",
        "files": ("config.yaml", "campplus_cn_common.bin"),
        "size": "约 28 MB",
        "desc": "声纹 embedding（M2 使用）",
        "optional": True,
    },
]


def is_complete(target: Path, required: tuple[str, ...]) -> bool:
    if not target.is_dir():
        return False
    if (target / "config.yaml").is_file() is False:
        return False
    # model.pt 或 campplus_cn_common.bin 任一存在即视为权重到位
    weight_candidates = ["model.pt", "campplus_cn_common.bin", "model.bin"]
    has_weight = any((target / w).is_file() for w in weight_candidates)
    return has_weight


def dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total / 1024 / 1024


def download_one(item: dict, force: bool = False) -> bool:
    target = MODELS_ROOT / item["local"]
    label = f"{item['local']}（{item['desc']}，{item['size']}）"

    if not force and is_complete(target, item["files"]):
        print(f"[跳过] {label} 已存在，{dir_size_mb(target):.1f} MB")
        return True

    print(f"[下载] {label}")
    print(f"       仓库: {item['repo']}")
    print(f"       目标: {target}")

    os.environ.setdefault("MODELSCOPE_CACHE", str(PROJECT_ROOT / "cache" / "modelscope"))
    target.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("[错误] 未安装 modelscope，请先运行 安装环境.bat")
        return False

    try:
        snapshot_download(item["repo"], local_dir=str(target))
    except TypeError:
        # 兼容旧版签名
        snapshot_download(item["repo"], cache_dir=str(target))
    except Exception as e:  # noqa: BLE001
        print(f"[失败] {e}")
        print("       可尝试手动下载：https://www.modelscope.cn/models/" + item["repo"])
        return False

    ok = is_complete(target, item["files"])
    status = "完成" if ok else "文件不完整"
    print(f"[{status}] 用时 {time.time() - t0:.0f}s，占用 {dir_size_mb(target):.1f} MB")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 RecordReg 所需的本地模型")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    ap.add_argument("--only", default=None, help="只下载指定 local 目录名")
    ap.add_argument("--skip-optional", action="store_true", help="跳过可选模型（声纹）")
    args = ap.parse_args()

    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"模型根目录: {MODELS_ROOT}\n")

    results: dict[str, bool] = {}
    for item in MODELS:
        if args.only and item["local"] != args.only:
            continue
        if args.skip_optional and item.get("optional"):
            continue
        results[item["local"]] = download_one(item, force=args.force)
        print()

    print("=" * 60)
    for name, ok in results.items():
        print(f"{'✓' if ok else '✗'} {name}")

    core_ok = all(
        results.get(m["local"], True) for m in MODELS if not m.get("optional")
    )
    if core_ok:
        print("\n核心模型已就绪，可以运行 启动M1.bat")
        return 0
    print("\n存在未完成的模型，请重试或手动下载后解压到 models/ 目录")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
