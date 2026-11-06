"""RecordReg 命令行入口。

用法示例：
    .venv\\Scripts\\python.exe app\\cli.py "D:\\录音" --limit 3
    .venv\\Scripts\\python.exe app\\cli.py "D:\\录音" --force      # 强制重跑
    .venv\\Scripts\\python.exe app\\cli.py --status                # 查看索引
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterable

# 保证以脚本方式直接运行时能找到 app 包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import (  # noqa: E402
    PROJECT_ROOT,
    load_config,
    make_stdout_safe,
    setup_logging,
    setup_runtime_env,
)
from app.core.audio import SUPPORTED_EXTS  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="recordreg",
        description="录音批量转写 + 说话人分离（输出 Markdown / JSON）",
    )
    p.add_argument("inputs", nargs="*", help="音频文件或目录，可多个")
    p.add_argument("-c", "--config", default=None, help="配置文件路径（默认 ./config.yaml）")
    p.add_argument("-o", "--output", default=None, help="输出目录（覆盖配置）")
    p.add_argument("-n", "--limit", type=int, default=0, help="最多处理 N 个文件（0=不限）")
    p.add_argument("-d", "--device", default="auto", choices=["auto", "cuda", "cpu"],
                   help="推理设备，默认 auto")
    p.add_argument("-f", "--force", action="store_true", help="忽略索引，强制重跑已处理的文件")
    p.add_argument("--status", action="store_true", help="查看索引状态后退出")
    p.add_argument("--keep-cache", action="store_true", help="保留缓存 WAV")
    p.add_argument("--no-warmup", action="store_true", help="跳过预热")
    p.add_argument("--list-only", action="store_true", help="只列出将要处理的文件")
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return p.parse_args(argv)


def collect_files(inputs: Iterable[str], limit: int = 0) -> list[Path]:
    found: list[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser()
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTS:
                found.append(p)
            else:
                print(f"[跳过] 不支持的扩展名: {p.name}")
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS:
                    found.append(f)
        else:
            print(f"[跳过] 路径不存在: {p}")
    # 去重并保持顺序
    seen: set[str] = set()
    uniq: list[Path] = []
    for f in found:
        key = str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq[:limit] if limit > 0 else uniq


def print_status(store) -> None:
    c = store.counts()
    print("=" * 62)
    print(f"索引状态  {store.db_path}")
    print("=" * 62)
    print(f"  共 {c['total']} 个文件")
    print(f"    已完成 {c['done']}")
    print(f"    失败   {c['failed']}")
    print(f"    待处理 {c['pending']}")
    if c["total"] == 0:
        return
    print("\n最近处理：")
    print(f"  {'状态':6} {'时长':>7} {'人':>3} {'字':>6}  文件名")
    for r in store.recent(15):
        dur = f"{r['duration_sec']/60:.1f}分" if r["duration_sec"] else "-"
        spk = str(r["speaker_count"]) if r["speaker_count"] else "-"
        ch = str(r["char_count"]) if r["char_count"] else "-"
        print(f"  {r['status']:6} {dur:>7} {spk:>3} {ch:>6}  {r['name'][:40]}")
    failed = [r for r in store.recent(100) if r["status"] == "failed"]
    if failed:
        print("\n失败原因（最近 5 条）：")
        for r in failed[:5]:
            print(f"  {r['name'][:30]}: {r['error']}")


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # 必须在导入 torch / funasr 之前锁定离线环境
    setup_runtime_env()
    cfg = load_config(args.config)
    if args.output:
        cfg.paths.output = Path(args.output).expanduser().resolve()
    if args.keep_cache:
        cfg.audio.keep_cache_wav = True
    cfg.ensure_dirs()
    setup_logging(args.verbose, log_file=PROJECT_ROOT / "logs" / "run.log")

    from app.core.store import Store
    from app.core.writer import TOOL_VERSION

    store = Store(cfg.paths.db)

    if args.status:
        print_status(store)
        return 0

    if not args.inputs:
        print("请提供音频文件或目录（--status 可查看索引状态）")
        return 2

    files = collect_files(args.inputs, args.limit)
    if not files:
        print("没有找到可处理的音频文件（支持 .mp3 / .aac / .m4a）")
        return 2

    # 先按 sha256 判重：内容相同就不重复跑
    from app.core.audio import sha256_of

    todo: list[tuple[Path, str]] = []
    skipped = 0
    for f in files:
        try:
            sha = sha256_of(f)
        except OSError as e:
            print(f"[跳过] 无法读取 {f.name}: {e}")
            continue
        if not args.force and store.is_done(sha, TOOL_VERSION):
            skipped += 1
            print(f"[已处理] {f.name}（--force 可强制重跑）")
            continue
        todo.append((f, sha))

    print(f"待处理 {len(todo)} 个，跳过 {skipped} 个（索引中已完成）")
    for f, _ in todo:
        print(f"  - {f}")
    if args.list_only:
        return 0
    if not todo:
        print("\n没有需要处理的文件。")
        return 0

    # 延迟导入，避免无谓的环境依赖
    from app.core.audio import resolve_binary
    from app.core.pipeline import process_file
    from app.core.registry import ModelRegistry
    from app.core.writer import write_all

    try:
        ffmpeg_path = resolve_binary("ffmpeg", cfg.paths.ffmpeg)
        print(f"ffmpeg: {ffmpeg_path}")
    except Exception as e:  # noqa: BLE001
        print(f"[错误] {e}")
        return 3

    device = None
    if args.device == "cuda":
        device = "cuda:0"
    elif args.device == "cpu":
        device = "cpu"

    registry = ModelRegistry.get(cfg, device)
    print(f"推理设备: {registry.device}")

    try:
        registry.vad()
        registry.asr()
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 模型加载失败: {e}")
        return 4
    if not args.no_warmup:
        registry.warmup()

    batch_id = store.start_batch(len(todo))
    ok, failed = 0, 0
    total_audio = 0.0
    t_start = time.time()
    failures: list[tuple[Path, str]] = []

    for i, (f, sha) in enumerate(todo, start=1):
        print(f"\n[{i}/{len(todo)}] {f.name}")
        try:
            st = f.stat()
            store.mark_running(sha, f, f.name, f.suffix.lstrip(".").lower(),
                               st.st_size, st.st_mtime)
        except Exception:  # noqa: BLE001
            pass

        try:
            doc = process_file(f, cfg, registry, sha=sha)
        except Exception as e:  # noqa: BLE001
            failed += 1
            failures.append((f, str(e)))
            store.mark_failed(sha, str(e))
            print(f"  ✗ 失败: {e}")
            continue

        paths = write_all(doc, cfg)
        store.mark_done(
            sha,
            out_md=str(paths["md"]) if paths.get("md") else None,
            out_json=str(paths["json"]) if paths.get("json") else None,
            duration_sec=doc.info.duration_sec,
            speaker_count=len(doc.speakers),
            turn_count=len(doc.turns),
            char_count=doc.char_count,
            elapsed_sec=doc.elapsed_sec,
            model_ver=TOOL_VERSION,
        )
        ok += 1
        total_audio += doc.info.duration_sec
        spk = f"{len(doc.speakers)}人" if doc.has_speaker else "单人"
        print(
            f"  ✓ {spk} | {len(doc.turns)} 轮 | {len(doc.segments)} 段 | "
            f"{doc.char_count} 字 | {doc.info.duration_sec:.0f}s 音频 | "
            f"耗时 {doc.elapsed_sec:.1f}s | RTF {doc.rtf:.3f}"
        )
        for k, v in paths.items():
            print(f"    {k}: {v}")

    store.finish_batch(batch_id, ok, skipped, failed)

    elapsed = time.time() - t_start
    print("\n" + "=" * 60)
    print(f"完成：成功 {ok} / 跳过 {skipped} / 失败 {failed}")
    if total_audio > 0:
        print(f"音频总时长 {total_audio/60:.1f} 分钟，总耗时 {elapsed/60:.1f} 分钟，"
              f"整体 RTF {elapsed/total_audio:.3f}")
    if failures:
        print("\n失败清单：")
        for f, msg in failures:
            print(f"  - {f.name}: {msg}")
    print(f"输出目录：{cfg.paths.output}")
    print(f"索引库：  {cfg.paths.db}")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """最外层兜底。

    Windows 上任何未捕获异常都会让 bat 窗口啪一下关掉，用户什么都看不到。
    这里统一捕获、打印、并且额外写一份 error.log。
    """
    make_stdout_safe()
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("\n已手动中断")
        return 130
    except BaseException:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        try:
            err_log = PROJECT_ROOT / "logs" / "error.log"
            err_log.parent.mkdir(parents=True, exist_ok=True)
            with open(err_log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            print(f"\n详细错误已写入: {err_log}")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
