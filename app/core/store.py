"""SQLite 索引：记录每个文件的处理状态，支撑去重与断点续跑。

为什么要这一层：
100 个文件跑到第 60 个崩了，没有索引就得从头再来；同一份录音换个目录
再拖一次，也会白白重跑一遍。用 sha256 作主键，内容一样就是同一个文件。

状态机：pending → running → done | failed
程序异常退出时，残留的 running 会在下次启动时重置为 pending。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256        TEXT NOT NULL UNIQUE,
    path          TEXT NOT NULL,
    name          TEXT NOT NULL,
    ext           TEXT,
    size_bytes    INTEGER,
    duration_sec  REAL,
    mtime         REAL,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    out_md        TEXT,
    out_json      TEXT,
    speaker_count INTEGER,
    turn_count    INTEGER,
    char_count    INTEGER,
    elapsed_sec   REAL,
    model_ver     TEXT,
    created_at    TEXT,
    processed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);

CREATE TABLE IF NOT EXISTS speaker_aliases (
    sha256 TEXT NOT NULL,
    spk_id TEXT NOT NULL,
    alias  TEXT NOT NULL,
    PRIMARY KEY (sha256, spk_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    total       INTEGER,
    success     INTEGER,
    skipped     INTEGER,
    failed      INTEGER
);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    """单文件、单连接，串行使用（CLI 与 GUI 的 worker 都是串行处理）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # GUI 场景下主线程查重、worker 线程写入，共用一条连接，必须加锁
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self._reset_interrupted()

    # ---- 生命周期 ----
    def _reset_interrupted(self) -> None:
        with self._lock:
            """上次异常退出留下的 running，重置为 pending。"""
            cur = self.conn.execute("SELECT COUNT(*) FROM files WHERE status='running'")
            n = cur.fetchone()[0]
            if n:
                self.conn.execute(
                    "UPDATE files SET status='pending', error=? WHERE status='running'",
                    ("上次运行被中断",),
                )
                self.conn.commit()
                log.info("已重置 %d 个中断的任务", n)

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 查询 ----
    def get(self, sha256: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self.conn.execute("SELECT * FROM files WHERE sha256=?", (sha256,))
            return cur.fetchone()

    def is_done(self, sha256: str, model_ver: str | None = None) -> bool:
        row = self.get(sha256)
        if row is None or row["status"] != "done":
            return False
        if model_ver and row["model_ver"] and row["model_ver"] != model_ver:
            return False
        return True

    def counts(self) -> dict[str, int]:
        with self._lock:
            cur = self.conn.execute("SELECT status, COUNT(*) AS n FROM files GROUP BY status")
            out = {r["status"]: r["n"] for r in cur.fetchall()}
            for k in ("pending", "running", "done", "failed"):
                out.setdefault(k, 0)
            out["total"] = sum(out.values())
            return out

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM files ORDER BY COALESCE(processed_at, created_at) DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()

    # ---- 写入 ----
    def mark_running(self, sha256: str, path: Path, name: str, ext: str,
                     size_bytes: int, mtime: float) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO files (sha256, path, name, ext, size_bytes, mtime, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    path=excluded.path, name=excluded.name, ext=excluded.ext,
                    size_bytes=excluded.size_bytes, mtime=excluded.mtime,
                    status='running', error=NULL
                """,
                (sha256, str(path), name, ext, size_bytes, mtime, _now()),
            )
            self.conn.commit()

    def mark_done(self, sha256: str, *, out_md: str | None, out_json: str | None,
                  duration_sec: float, speaker_count: int, turn_count: int,
                  char_count: int, elapsed_sec: float, model_ver: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE files SET status='done', error=NULL, out_md=?, out_json=?,
                    duration_sec=?, speaker_count=?, turn_count=?, char_count=?,
                    elapsed_sec=?, model_ver=?, processed_at=?
                WHERE sha256=?
                """,
                (out_md, out_json, duration_sec, speaker_count, turn_count,
                 char_count, elapsed_sec, model_ver, _now(), sha256),
            )
            self.conn.commit()

    def mark_failed(self, sha256: str, error: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE files SET status='failed', error=?, processed_at=? WHERE sha256=?",
                (error[:500], _now(), sha256),
            )
            self.conn.commit()

    def set_alias(self, sha256: str, spk_id: str, alias: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO speaker_aliases (sha256, spk_id, alias) VALUES (?, ?, ?) "
                "ON CONFLICT(sha256, spk_id) DO UPDATE SET alias=excluded.alias",
                (sha256, spk_id, alias),
            )
            self.conn.commit()

    def get_aliases(self, sha256: str) -> dict[str, str]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT spk_id, alias FROM speaker_aliases WHERE sha256=?", (sha256,)
            )
            return {r["spk_id"]: r["alias"] for r in cur.fetchall()}

    # ---- 批次 ----
    def start_batch(self, total: int) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO batches (started_at, total) VALUES (?, ?)", (_now(), total)
            )
            self.conn.commit()
            return int(cur.lastrowid or 0)

    def finish_batch(self, batch_id: int, success: int, skipped: int, failed: int) -> None:
        with self._lock:
            if not batch_id:
                return
            self.conn.execute(
                "UPDATE batches SET finished_at=?, success=?, skipped=?, failed=? WHERE id=?",
                (_now(), success, skipped, failed, batch_id),
            )
            self.conn.commit()

    # ---- 维护 ----
    def db_stats(self) -> dict[str, Any]:
        """索引占用情况，用于设置界面展示。"""
        with self._lock:
            size = self.db_path.stat().st_size if self.db_path.exists() else 0
            total = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            done = self.conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='done'"
            ).fetchone()[0]
            failed = self.conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='failed'"
            ).fetchone()[0]
        return {
            "size_bytes": int(size),
            "size_mb": size / 1024 / 1024,
            "total": int(total),
            "done": int(done),
            "failed": int(failed),
        }

    def purge_missing(self) -> int:
        """删掉源文件已经不存在的记录（录音删了/移走了，记录留着没意义）。"""
        with self._lock:
            rows = self.conn.execute("SELECT sha256, path FROM files").fetchall()
            missing = [r["sha256"] for r in rows if not Path(r["path"]).exists()]
            for sha in missing:
                self.conn.execute("DELETE FROM files WHERE sha256=?", (sha,))
                self.conn.execute("DELETE FROM speaker_aliases WHERE sha256=?", (sha,))
            self.conn.commit()
            if missing:
                try:
                    self.conn.execute("VACUUM")
                except Exception:  # noqa: BLE001
                    pass
        return len(missing)

    def clear_all(self) -> None:
        """清空全部索引。清空后所有文件都会被当作新文件重新处理。"""
        with self._lock:
            self.conn.execute("DELETE FROM files")
            self.conn.execute("DELETE FROM speaker_aliases")
            self.conn.execute("DELETE FROM batches")
            self.conn.commit()
            try:
                self.conn.execute("VACUUM")
            except Exception:  # noqa: BLE001
                pass

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            cur = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self.conn.commit()
