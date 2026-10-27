# -*- coding: utf-8 -*-
"""
SQLite 数据持久化层 —— 按 PRD 第五章数据模型建表.
各模块共用同一个 DB 文件，通过 module 字段区分分类归属.
"""
import os
import sqlite3
import json
import time
import uuid
import threading
from contextlib import contextmanager

# ---------- 路径 ----------
def _resolve_db_file():
    """DB 文件与 exe/脚本同目录，便于打包后持久化."""
    import sys
    try:
        base = sys._MEIPASS  # PyInstaller 解压目录
        # 对 DB 应该放 exe 同级，不在 _MEIPASS 里
        base = os.path.dirname(sys.executable)
    except AttributeError:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, ".resource_manager.db")

DB_FILE = _resolve_db_file()

# sqlite3 连接对多线程不安全 —— 用每线程独立连接
_local = threading.local()

def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_FILE, timeout=10, isolation_level=None)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn

@contextmanager
def tx():
    conn = _get_conn()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------- 初始化 ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS category (
    id          TEXT PRIMARY KEY,
    module      TEXT NOT NULL,           -- url / onlineDoc / localDoc / md / aiPreset
    name        TEXT NOT NULL,
    level       INTEGER NOT NULL,        -- 1/2/3
    parentId    TEXT DEFAULT '',         -- 一级为空字符串
    sort        INTEGER DEFAULT 0,
    createdAt   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(module, name, level, parentId)
);
CREATE INDEX IF NOT EXISTS idx_category_module ON category(module, level);

CREATE TABLE IF NOT EXISTS url_item (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    categoryId1 TEXT DEFAULT '',
    categoryId2 TEXT DEFAULT '',
    categoryId3 TEXT DEFAULT '',
    remark      TEXT DEFAULT '',
    createdAt   TEXT DEFAULT (datetime('now','localtime')),
    updatedAt   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(url)
);

CREATE TABLE IF NOT EXISTS online_doc (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    categoryId1 TEXT DEFAULT '',
    categoryId2 TEXT DEFAULT '',
    categoryId3 TEXT DEFAULT '',
    remark      TEXT DEFAULT '',
    createdAt   TEXT DEFAULT (datetime('now','localtime')),
    updatedAt   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(url)
);

CREATE TABLE IF NOT EXISTS local_doc (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    filePath    TEXT NOT NULL,
    fileType    TEXT DEFAULT '',
    fileSize    INTEGER DEFAULT 0,
    categoryId1 TEXT DEFAULT '',
    categoryId2 TEXT DEFAULT '',
    categoryId3 TEXT DEFAULT '',
    remark      TEXT DEFAULT '',
    createdAt   TEXT DEFAULT (datetime('now','localtime')),
    updatedAt   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS md_file (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    filePath    TEXT NOT NULL,
    categoryId  TEXT DEFAULT '',
    remark      TEXT DEFAULT '',
    createdAt   TEXT DEFAULT (datetime('now','localtime')),
    updatedAt   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS ai_preset (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT DEFAULT '',
    formatPrompt  TEXT NOT NULL,
    exampleOutput TEXT DEFAULT '',
    isDefault     INTEGER DEFAULT 0,
    enabled       INTEGER DEFAULT 1,
    createdAt     TEXT DEFAULT (datetime('now','localtime')),
    updatedAt     TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS menu_config (
    id          TEXT PRIMARY KEY,
    menuKey     TEXT NOT NULL UNIQUE,        -- 内部 key, 稳定不变
    displayName TEXT NOT NULL,               -- 用户可见名
    groupKey    TEXT NOT NULL,               -- 所属分组 key
    sort        INTEGER DEFAULT 0,
    visible     INTEGER DEFAULT 1,
    icon        TEXT DEFAULT '',
    panel       TEXT DEFAULT ''              -- 对应 data-panel
);

CREATE TABLE IF NOT EXISTS batch_log (
    id            TEXT PRIMARY KEY,
    module        TEXT NOT NULL,
    operationType TEXT NOT NULL,              -- create/delete/update/import/export
    totalCount    INTEGER DEFAULT 0,
    successCount  INTEGER DEFAULT 0,
    failCount     INTEGER DEFAULT 0,
    detail        TEXT DEFAULT '{}',          -- JSON
    createdAt     TEXT DEFAULT (datetime('now','localtime'))
);
"""

# 菜单默认种子（需求 8 分组结构）
DEFAULT_MENUS = [
    # --- 资源管理 ---
    ("menu_minio",       "MinIO 文件", "resources", 1, "📦", "files"),
    ("menu_url",         "URL 地址",   "resources", 2, "🔗", "urls"),
    ("menu_online_doc",  "在线文档",   "resources", 3, "📚", "online-docs"),
    ("menu_local_doc",   "线下文档",   "resources", 4, "📄", "local-docs"),
    ("menu_md",          "MD 文件",    "resources", 5, "📝", "md-files"),
    # --- AI 能力 ---
    ("menu_ai",          "AI 助手",    "ai",         1, "🤖", "ai"),
    ("menu_ai_preset",   "AI 预设",    "ai",         2, "✨", "ai-presets"),
    # --- 系统设置 ---
    ("menu_config",      "连接配置",   "system",     1, "⚙️", "config"),
    ("menu_menu",        "菜单设置",   "system",     2, "📋", "menu-settings"),
]

def init_db():
    """首次启动时建表 + 种默认菜单."""
    conn = _get_conn()
    conn.executescript(SCHEMA)
    # 种菜单（幂等：只在 menu_config 空时插入）
    cur = conn.execute("SELECT COUNT(*) FROM menu_config")
    if cur.fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO menu_config(id, menuKey, displayName, groupKey, sort, visible, icon, panel) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(uuid.uuid4().hex, k, n, g, s, 1, ic, p) for (k, n, g, s, ic, p) in DEFAULT_MENUS]
        )
    conn.commit()
    return DB_FILE


# ---------- 通用 CRUD ----------
def _row_to_dict(row):
    return dict(row) if row else None

def _rows_to_list(rows):
    return [dict(r) for r in rows]

def gen_id():
    return uuid.uuid4().hex

def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ---------- 分页查询 helper ----------
def paginate(conn, table, where="", params=(), page=1, page_size=20,
             keyword_fields=(), keyword="", order_by="createdAt DESC"):
    """通用分页. 返回 {total, rows, page, pageSize}."""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 1000))
    offset = (page - 1) * page_size

    clauses = []
    args = list(params)
    if where:
        clauses.append("(" + where + ")")
    if keyword and keyword_fields:
        like = " OR ".join(f"{f} LIKE ?" for f in keyword_fields)
        clauses.append("(" + like + ")")
        for kw in keyword.split():
            args.extend(["%" + kw + "%"] * len(keyword_fields))

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    cur = conn.execute(f"SELECT COUNT(*) FROM {table} {where_sql}", args)
    total = cur.fetchone()[0]

    cur = conn.execute(
        f"SELECT * FROM {table} {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
        args + [page_size, offset]
    )
    return {
        "total": total,
        "rows": _rows_to_list(cur.fetchall()),
        "page": page,
        "pageSize": page_size,
    }


# ---------- 批量日志 ----------
def log_batch(module, op_type, total, success, fail, detail=None):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO batch_log(id, module, operationType, totalCount, successCount, failCount, detail) "
        "VALUES (?,?,?,?,?,?,?)",
        (gen_id(), module, op_type, total, success, fail,
         json.dumps(detail or {}, ensure_ascii=False))
    )
    conn.commit()
