# -*- coding: utf-8 -*-
"""
资源管理模块 API —— 覆盖 PRD 需求 2~7（不含需求 1 内置浏览器）.
所有路由挂在 /rs 前缀下，与 MinIO /minio 互不冲突.
"""
import os
import io
import csv
import json
import time
import sqlite3

from flask import Blueprint, request, jsonify, Response, send_file, abort

import db

bp = Blueprint("rs", __name__, url_prefix="/rs")

# ---------- 工具 ----------
MAX_BATCH = 1000

def _ok(data=None, **kw):
    r = {"ok": True}
    if data is not None:
        r["data"] = data
    r.update(kw)
    return jsonify(r)

def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code

def _now():
    return db.now_iso()


# ============================================================
# 分类管理（通用，各模块独立维护）
# ============================================================

@bp.route("/category/<module>", methods=["GET"])
def cat_list(module):
    """返回某模块的分类树."""
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT * FROM category WHERE module=? ORDER BY level, sort, name",
        (module,)
    ).fetchall()
    # 组装成三级嵌套
    levels = {1: [], 2: [], 3: []}
    by_id = {}
    for r in rows:
        d = dict(r)
        by_id[d["id"]] = d
        levels[d["level"]].append(d)
    for c in levels[2]:
        p = by_id.get(c.get("parentId") or "")
        if p is not None:
            p.setdefault("children", []).append(c)
        else:
            levels[1].append(c)
    for c in levels[3]:
        p = by_id.get(c.get("parentId") or "")
        if p is not None:
            p.setdefault("children", []).append(c)
        else:
            levels[2].append(c)
    return _ok(levels[1])


@bp.route("/category", methods=["POST"])
def cat_create():
    """新增分类. body: {module, name, level, parentId?, sort?}"""
    data = request.get_json(silent=True) or {}
    module = data.get("module", "").strip()
    name = data.get("name", "").strip()
    level = int(data.get("level", 1))
    parent_id = data.get("parentId", "") or ""
    sort = int(data.get("sort", 0))
    if not module or not name or level not in (1, 2, 3):
        return _err("module/name/level 必填，level 取值 1/2/3")
    conn = db._get_conn()
    try:
        conn.execute(
            "INSERT INTO category(id,module,name,level,parentId,sort) VALUES (?,?,?,?,?,?)",
            (db.gen_id(), module, name, level, parent_id, sort)
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return _err("分类已存在: " + str(e))
    return _ok(message="分类已创建")


@bp.route("/category/<cid>", methods=["PUT"])
def cat_update(cid):
    data = request.get_json(silent=True) or {}
    sets, args = [], []
    for f in ["name", "parentId", "sort"]:
        if f in data:
            sets.append(f"{f}=?")
            args.append(data[f])
    if not sets:
        return _err("无更新字段")
    args.append(cid)
    conn = db._get_conn()
    conn.execute(f"UPDATE category SET {','.join(sets)} WHERE id=?", args)
    conn.commit()
    return _ok(message="分类已更新")


@bp.route("/category/<cid>", methods=["DELETE"])
def cat_delete(cid):
    conn = db._get_conn()
    # 级联删子级
    conn.execute("DELETE FROM category WHERE id=? OR parentId=?", (cid, cid))
    conn.commit()
    return _ok(message="分类已删除")


# ============================================================
# 通用列表/增/删/改 helper（URL、在线文档、线下文档、MD 文件共用结构）
# ============================================================

def _list_resource(table, keyword_fields, extra_where="", extra_args=()):
    """通用列表端点处理（分页 + 关键字 + 分类筛选）."""
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("pageSize", 20, type=int), 1000)
    keyword = request.args.get("keyword", "").strip()
    cat1 = request.args.get("categoryId1", "").strip()
    cat2 = request.args.get("categoryId2", "").strip()
    cat3 = request.args.get("categoryId3", "").strip()

    clauses = list(extra_where) if isinstance(extra_where, list) else ([extra_where] if extra_where else [])
    args = list(extra_args)
    for cid, f in [(cat1, "categoryId1"), (cat2, "categoryId2"), (cat3, "categoryId3")]:
        if cid:
            clauses.append(f"{f}=?")
            args.append(cid)
    where_sql = " AND ".join(clauses)

    conn = db._get_conn()
    p = db.paginate(conn, table, where=where_sql, params=tuple(args),
                   page=page, page_size=page_size,
                   keyword_fields=keyword_fields, keyword=keyword)
    return _ok(p)


def _create_resource(table, fields, unique_field=None):
    """通用单条创建. fields: list of (col, json_key, required, default)."""
    data = request.get_json(silent=True) or {}
    col_list, val_list = ["id", "createdAt", "updatedAt"], [db.gen_id(), _now(), _now()]
    for col, key, required, default in fields:
        v = data.get(key, default)
        if required and (v is None or (isinstance(v, str) and not v.strip())):
            return _err(f"字段 {key} 必填")
        col_list.append(col)
        val_list.append(v)
    conn = db._get_conn()
    try:
        conn.execute(
            f"INSERT INTO {table}({','.join(col_list)}) VALUES ({','.join(['?'] * len(col_list))})",
            val_list
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        return _err("数据冲突（重复或外键错误）: " + str(e))
    return _ok(message="创建成功", id=val_list[0])


def _update_resource(table, rid, fields):
    """通用单条/批量更新. fields: list of (col, json_key)."""
    data = request.get_json(silent=True) or {}
    ids = [rid] if rid else (data.get("ids") or [])
    if not ids:
        return _err("id(s) 必填")
    sets, args = [], []
    for col, key in fields:
        if key in data:
            sets.append(f"{col}=?")
            args.append(data[key])
    if not sets:
        return _err("无更新字段")
    sets.append("updatedAt=?")
    args.append(_now())
    conn = db._get_conn()
    placeholders = ",".join("?" * len(ids))
    args.extend(ids)
    conn.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id IN ({placeholders})", args)
    conn.commit()
    return _ok(message=f"已更新 {len(ids)} 条")


def _delete_resource(table, rid_field="id"):
    """通用单条/批量删除."""
    data = request.get_json(silent=True) or {}
    ids = [request.view_args.get("rid")] if request.view_args.get("rid") else (data.get("ids") or [])
    if not ids:
        return _err("id(s) 必填")
    if len(ids) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    conn = db._get_conn()
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM {table} WHERE {rid_field} IN ({placeholders})", ids)
    conn.commit()
    return _ok(message=f"已删除 {len(ids)} 条", count=len(ids))


# ============================================================
# 需求 2: URL 地址管理
# ============================================================

URL_FIELDS = [
    ("name",        "name",        True,  ""),
    ("url",         "url",         True,  ""),
    ("categoryId1", "categoryId1", False, ""),
    ("categoryId2", "categoryId2", False, ""),
    ("categoryId3", "categoryId3", False, ""),
    ("remark",      "remark",      False, ""),
]
URL_UPDATE_FIELDS = [(c, k) for c, k, _, _ in URL_FIELDS]

@bp.route("/url/list", methods=["GET"])
def url_list():
    return _list_resource("url_item", ["name", "url", "remark"])

@bp.route("/url", methods=["POST"])
def url_create():
    return _create_resource("url_item", URL_FIELDS, unique_field="url")

@bp.route("/url/batch", methods=["POST"])
def url_batch_create():
    """批量新增. body: {items: [{name,url,...}, ...]} —— 单次 ≤1000."""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return _err("items 不能为空")
    if len(items) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    conn = db._get_conn()
    success, fail, details = 0, 0, []
    with db.tx() as tx:
        for it in items:
            try:
                cols = ["id", "createdAt", "updatedAt"] + [f[0] for f in URL_FIELDS]
                vals = [db.gen_id(), _now(), _now()]
                for col, key, req, default in URL_FIELDS:
                    v = it.get(key, default)
                    if req and not v:
                        raise ValueError(f"{key} 必填")
                    vals.append(v)
                tx.execute(f"INSERT INTO url_item({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
                success += 1
            except Exception as e:
                fail += 1
                details.append({"row": it.get("name", "?"), "error": str(e)})
    db.log_batch("url", "create", len(items), success, fail, details)
    return _ok(total=len(items), success=success, fail=fail, detail=details)

@bp.route("/url/batch-update", methods=["POST"])
def url_batch_update():
    return _update_resource("url_item", None, URL_UPDATE_FIELDS)

@bp.route("/url/<rid>", methods=["PUT"])
def url_update_one(rid):
    return _update_resource("url_item", rid, URL_UPDATE_FIELDS)

@bp.route("/url/<rid>", methods=["DELETE"])
def url_delete_one(rid):
    return _delete_resource("url_item")

@bp.route("/url/batch-delete", methods=["POST"])
def url_batch_delete():
    return _delete_resource("url_item")


# ============================================================
# 需求 3: 在线文档管理
# ============================================================

ONLINE_FIELDS = [
    ("name",        "name",        True,  ""),
    ("url",         "url",         True,  ""),
    ("categoryId1", "categoryId1", False, ""),
    ("categoryId2", "categoryId2", False, ""),
    ("categoryId3", "categoryId3", False, ""),
    ("remark",      "remark",      False, ""),
]
ONLINE_UPDATE = [(c, k) for c, k, _, _ in ONLINE_FIELDS]

@bp.route("/online-doc/list", methods=["GET"])
def online_doc_list():
    return _list_resource("online_doc", ["name", "url", "remark"])

@bp.route("/online-doc", methods=["POST"])
def online_doc_create():
    return _create_resource("online_doc", ONLINE_FIELDS, unique_field="url")

@bp.route("/online-doc/batch", methods=["POST"])
def online_doc_batch():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if len(items) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    conn = db._get_conn()
    success, fail, details = 0, 0, []
    with db.tx() as tx:
        for it in items:
            try:
                cols = ["id", "createdAt", "updatedAt"] + [f[0] for f in ONLINE_FIELDS]
                vals = [db.gen_id(), _now(), _now()]
                for col, key, req, default in ONLINE_FIELDS:
                    v = it.get(key, default)
                    if req and not v:
                        raise ValueError(f"{key} 必填")
                    vals.append(v)
                tx.execute(f"INSERT INTO online_doc({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
                success += 1
            except Exception as e:
                fail += 1
                details.append({"row": it.get("name", "?"), "error": str(e)})
    db.log_batch("onlineDoc", "create", len(items), success, fail, details)
    return _ok(total=len(items), success=success, fail=fail, detail=details)

@bp.route("/online-doc/<rid>", methods=["PUT"])
def online_doc_update(rid):
    return _update_resource("online_doc", rid, ONLINE_UPDATE)

@bp.route("/online-doc/batch-update", methods=["POST"])
def online_doc_batch_update():
    return _update_resource("online_doc", None, ONLINE_UPDATE)

@bp.route("/online-doc/<rid>", methods=["DELETE"])
def online_doc_delete_one(rid):
    return _delete_resource("online_doc")

@bp.route("/online-doc/batch-delete", methods=["POST"])
def online_doc_batch_delete():
    return _delete_resource("online_doc")


# ============================================================
# 需求 4: 线下文档管理
# ============================================================

LOCAL_FIELDS = [
    ("name",        "name",        True,  ""),
    ("filePath",    "filePath",    True,  ""),
    ("fileType",    "fileType",    False, ""),
    ("fileSize",    "fileSize",    False, 0),
    ("categoryId1", "categoryId1", False, ""),
    ("categoryId2", "categoryId2", False, ""),
    ("categoryId3", "categoryId3", False, ""),
    ("remark",      "remark",      False, ""),
]
LOCAL_UPDATE = [(c, k) for c, k, _, _ in LOCAL_FIELDS]

def _fill_local_file_info(data):
    """从 filePath 自动补 fileType/fileSize/name（若未填）."""
    fp = data.get("filePath", "")
    if not data.get("name") and fp:
        data["name"] = os.path.basename(fp)
    if os.path.isfile(fp):
        if not data.get("fileType"):
            data["fileType"] = os.path.splitext(fp)[1].lstrip(".").upper()
        if not data.get("fileSize"):
            data["fileSize"] = os.path.getsize(fp)
    return data

@bp.route("/local-doc/list", methods=["GET"])
def local_doc_list():
    """自己处理，因为要附加文件存在性标记."""
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("pageSize", 20, type=int), 1000)
    keyword = request.args.get("keyword", "").strip()
    cat1 = request.args.get("categoryId1", "").strip()
    cat2 = request.args.get("categoryId2", "").strip()
    cat3 = request.args.get("categoryId3", "").strip()
    clauses, args = [], []
    for cid, f in [(cat1, "categoryId1"), (cat2, "categoryId2"), (cat3, "categoryId3")]:
        if cid:
            clauses.append(f"{f}=?")
            args.append(cid)
    where_sql = " AND ".join(clauses)
    conn = db._get_conn()
    p = db.paginate(conn, "local_doc", where=where_sql, params=tuple(args),
                   page=page, page_size=page_size,
                   keyword_fields=["name", "filePath", "remark"], keyword=keyword)
    for r in p["rows"]:
        r["exists"] = os.path.isfile(r.get("filePath", ""))
    return _ok(p)

@bp.route("/local-doc", methods=["POST"])
def local_doc_create():
    data = request.get_json(silent=True) or {}
    data = _fill_local_file_info(data)
    return _create_resource("local_doc", LOCAL_FIELDS)

@bp.route("/local-doc/batch", methods=["POST"])
def local_doc_batch():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if len(items) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    success, fail, details = 0, 0, []
    with db.tx() as tx:
        for it in items:
            it = _fill_local_file_info(it)
            try:
                cols = ["id", "createdAt", "updatedAt"] + [f[0] for f in LOCAL_FIELDS]
                vals = [db.gen_id(), _now(), _now()]
                for col, key, req, default in LOCAL_FIELDS:
                    v = it.get(key, default)
                    if req and not v:
                        raise ValueError(f"{key} 必填")
                    vals.append(v)
                tx.execute(f"INSERT INTO local_doc({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
                success += 1
            except Exception as e:
                fail += 1
                details.append({"row": it.get("filePath", "?"), "error": str(e)})
    db.log_batch("localDoc", "create", len(items), success, fail, details)
    return _ok(total=len(items), success=success, fail=fail, detail=details)

@bp.route("/local-doc/<rid>", methods=["PUT"])
def local_doc_update(rid):
    return _update_resource("local_doc", rid, LOCAL_UPDATE)

@bp.route("/local-doc/batch-update", methods=["POST"])
def local_doc_batch_update():
    return _update_resource("local_doc", None, LOCAL_UPDATE)

@bp.route("/local-doc/<rid>", methods=["DELETE"])
def local_doc_delete_one(rid):
    data = request.get_json(silent=True) or {}
    del_file = bool(data.get("deleteLocalFile", False))
    conn = db._get_conn()
    row = conn.execute("SELECT filePath FROM local_doc WHERE id=?", (rid,)).fetchone()
    conn.execute("DELETE FROM local_doc WHERE id=?", (rid,))
    conn.commit()
    if del_file and row and os.path.isfile(row["filePath"]):
        try:
            os.remove(row["filePath"])
        except Exception:
            pass
    return _ok(message="已删除")

@bp.route("/local-doc/batch-delete", methods=["POST"])
def local_doc_batch_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    del_file = bool(data.get("deleteLocalFile", False))
    if len(ids) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    conn = db._get_conn()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT filePath FROM local_doc WHERE id IN ({placeholders})", ids).fetchall()
    conn.execute(f"DELETE FROM local_doc WHERE id IN ({placeholders})", ids)
    conn.commit()
    if del_file:
        for r in rows:
            try:
                if os.path.isfile(r["filePath"]):
                    os.remove(r["filePath"])
            except Exception:
                pass
    return _ok(message=f"已删除 {len(ids)} 条", count=len(ids))

@bp.route("/local-doc/preview/<rid>", methods=["GET"])
def local_doc_preview(rid):
    """文件预览：返回文件内容或元信息."""
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM local_doc WHERE id=?", (rid,)).fetchone()
    if not row:
        return _err("记录不存在", 404)
    fp = row["filePath"]
    if not os.path.isfile(fp):
        return _err(f"文件不存在: {fp}", 404)
    # 文本/MD/图片 直接返回，其他返回元信息
    ext = os.path.splitext(fp)[1].lower()
    text_exts = {".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv", ".log", ".ini", ".cfg", ".conf", ".py", ".js", ".ts", ".html", ".css", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sh", ".bat"}
    img_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}
    if ext in text_exts:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return _ok({"type": "text", "text": content, "encoding": "utf-8"})
        except Exception as e:
            return _err("读取失败: " + str(e))
    elif ext in img_exts:
        return send_file(fp)
    else:
        # Office / PDF 等前端自行预览，后端只返回元信息
        return _ok({
            "type": "binary",
            "name": row["name"],
            "filePath": fp,
            "fileType": row["fileType"],
            "fileSize": row["fileSize"],
            "downloadUrl": f"/rs/local-doc/download/{rid}",
        })

@bp.route("/local-doc/download/<rid>", methods=["GET"])
def local_doc_download(rid):
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM local_doc WHERE id=?", (rid,)).fetchone()
    if not row or not os.path.isfile(row["filePath"]):
        abort(404)
    return send_file(row["filePath"], as_attachment=True)


# ============================================================
# 需求 5: MD 文件管理
# ============================================================

MD_FIELDS = [
    ("name",        "name",        True,  ""),
    ("filePath",    "filePath",    True,  ""),
    ("categoryId",  "categoryId", False, ""),
    ("remark",      "remark",      False, ""),
]
MD_UPDATE = [(c, k) for c, k, _, _ in MD_FIELDS]

@bp.route("/md/list", methods=["GET"])
def md_list():
    conn = db._get_conn()
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("pageSize", 20, type=int), 1000)
    keyword = request.args.get("keyword", "").strip()
    cat = request.args.get("categoryId", "").strip()
    where, args = "", ()
    if cat:
        where, args = "categoryId=?", (cat,)
    p = db.paginate(conn, "md_file", where=where, params=args,
                    page=page, page_size=page_size,
                    keyword_fields=["name", "filePath", "remark"], keyword=keyword)
    for r in p["rows"]:
        r["exists"] = os.path.isfile(r.get("filePath", ""))
    return _ok(p)

@bp.route("/md", methods=["POST"])
def md_create():
    data = request.get_json(silent=True) or {}
    if not data.get("name") and data.get("filePath"):
        data["name"] = os.path.basename(data["filePath"])
    return _create_resource("md_file", MD_FIELDS)

@bp.route("/md/batch", methods=["POST"])
def md_batch():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if len(items) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    success, fail, details = 0, 0, []
    with db.tx() as tx:
        for it in items:
            if not it.get("name") and it.get("filePath"):
                it["name"] = os.path.basename(it["filePath"])
            try:
                cols = ["id", "createdAt", "updatedAt"] + [f[0] for f in MD_FIELDS]
                vals = [db.gen_id(), _now(), _now()]
                for col, key, req, default in MD_FIELDS:
                    v = it.get(key, default)
                    if req and not v:
                        raise ValueError(f"{key} 必填")
                    vals.append(v)
                tx.execute(f"INSERT INTO md_file({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
                success += 1
            except Exception as e:
                fail += 1
                details.append({"row": it.get("name", "?"), "error": str(e)})
    db.log_batch("md", "create", len(items), success, fail, details)
    return _ok(total=len(items), success=success, fail=fail, detail=details)

@bp.route("/md/<rid>", methods=["GET"])
def md_read(rid):
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM md_file WHERE id=?", (rid,)).fetchone()
    if not row:
        return _err("记录不存在", 404)
    fp = row["filePath"]
    if not os.path.isfile(fp):
        return _err(f"文件不存在: {fp}", 404)
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return _ok({"id": row["id"], "name": row["name"], "filePath": fp, "content": content})

@bp.route("/md/<rid>/page", methods=["GET"])
def md_page(rid):
    """返回 MD 文件的 HTML 页面（供内置浏览器标签页打开）."""
    import html as _html
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM md_file WHERE id=?", (rid,)).fetchone()
    if not row:
        return _err("记录不存在", 404)
    fp = row["filePath"]
    if not os.path.isfile(fp):
        return _err(f"文件不存在: {fp}", 404)
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    page = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(row['name'])}</title>"
        "<style>body{font-family:-apple-system,'PingFang SC',sans-serif;max-width:900px;"
        "margin:0 auto;padding:24px;line-height:1.7;color:#1f2937;background:#fff;}"
        "pre{background:#f5f7fb;padding:14px;border-radius:6px;overflow-x:auto;white-space:pre-wrap;}"
        "code{background:#f5f7fb;padding:1px 4px;border-radius:3px;}</style></head><body>"
        f"<h1>{_html.escape(row['name'])}</h1>"
        f"<p style=\"color:#94a3b8;font-size:.8rem;\">{_html.escape(fp)}</p>"
        f"<pre>{_html.escape(content)}</pre></body></html>"
    )
    return Response(page, mimetype="text/html; charset=utf-8")

@bp.route("/md/<rid>", methods=["PUT"])
def md_update(rid):
    return _update_resource("md_file", rid, MD_UPDATE)

@bp.route("/md/batch-update", methods=["POST"])
def md_batch_update():
    return _update_resource("md_file", None, MD_UPDATE)

@bp.route("/md/<rid>", methods=["DELETE"])
def md_delete_one(rid):
    data = request.get_json(silent=True) or {}
    del_file = bool(data.get("deleteLocalFile", False))
    conn = db._get_conn()
    row = conn.execute("SELECT filePath FROM md_file WHERE id=?", (rid,)).fetchone()
    conn.execute("DELETE FROM md_file WHERE id=?", (rid,))
    conn.commit()
    if del_file and row and os.path.isfile(row["filePath"]):
        try:
            os.remove(row["filePath"])
        except Exception:
            pass
    return _ok(message="已删除")

@bp.route("/md/batch-delete", methods=["POST"])
def md_batch_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    del_file = bool(data.get("deleteLocalFile", False))
    if len(ids) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    conn = db._get_conn()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT filePath FROM md_file WHERE id IN ({placeholders})", ids).fetchall()
    conn.execute(f"DELETE FROM md_file WHERE id IN ({placeholders})", ids)
    conn.commit()
    if del_file:
        for r in rows:
            try:
                if os.path.isfile(r["filePath"]):
                    os.remove(r["filePath"])
            except Exception:
                pass
    return _ok(message=f"已删除 {len(ids)} 条", count=len(ids))

@bp.route("/md/<rid>/save", methods=["POST"])
def md_save(rid):
    """编辑保存. body: {content}"""
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM md_file WHERE id=?", (rid,)).fetchone()
    if not row:
        return _err("记录不存在", 404)
    try:
        with open(row["filePath"], "w", encoding="utf-8") as f:
            f.write(content)
        conn.execute("UPDATE md_file SET updatedAt=? WHERE id=?", (_now(), rid))
        conn.commit()
    except Exception as e:
        return _err("保存失败: " + str(e))
    return _ok(message="已保存")


# ============================================================
# 需求 6 + 8: 菜单配置（分组 + 名称 + 批量）
# ============================================================

@bp.route("/menu/list", methods=["GET"])
def menu_list():
    """按 groupKey 分组返回."""
    conn = db._get_conn()
    rows = conn.execute("SELECT * FROM menu_config ORDER BY groupKey, sort").fetchall()
    # 组装分组
    groups = {}
    group_order = ["resources", "ai", "system"]
    group_labels = {"resources": "资源管理", "ai": "AI 能力", "system": "系统设置"}
    for r in rows:
        d = dict(r)
        gk = d["groupKey"]
        groups.setdefault(gk, []).append(d)
    ordered = []
    for gk in group_order:
        if gk in groups:
            ordered.append({
                "groupKey": gk,
                "groupLabel": group_labels.get(gk, gk),
                "items": groups[gk],
            })
    # 可能有不在预置分组里的
    for gk, items in groups.items():
        if gk not in group_order:
            ordered.append({"groupKey": gk, "groupLabel": group_labels.get(gk, gk), "items": items})
    return _ok(ordered)

@bp.route("/menu/<mid>", methods=["PUT"])
def menu_update(mid):
    data = request.get_json(silent=True) or {}
    sets, args = [], []
    for f in ["displayName", "groupKey", "sort", "visible", "icon", "panel"]:
        if f in data:
            sets.append(f"{f}=?")
            args.append(data[f])
    if not sets:
        return _err("无更新字段")
    args.append(mid)
    conn = db._get_conn()
    conn.execute(f"UPDATE menu_config SET {','.join(sets)} WHERE id=?", args)
    conn.commit()
    return _ok(message="已更新")

@bp.route("/menu/batch-update", methods=["POST"])
def menu_batch_update():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not ids:
        return _err("ids 必填")
    sets, args = [], []
    for f in ["groupKey", "sort", "visible"]:
        if f in data:
            sets.append(f"{f}=?")
            args.append(data[f])
    if not sets:
        return _err("无更新字段")
    placeholders = ",".join("?" * len(ids))
    args.extend(ids)
    conn = db._get_conn()
    conn.execute(f"UPDATE menu_config SET {','.join(sets)} WHERE id IN ({placeholders})", args)
    conn.commit()
    return _ok(message=f"已更新 {len(ids)} 条")

@bp.route("/menu/reset", methods=["POST"])
def menu_reset():
    """恢复默认配置."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or None
    conn = db._get_conn()
    if ids:
        for (k, n, g, s, ic, p) in db.DEFAULT_MENUS:
            conn.execute(
                "UPDATE menu_config SET displayName=?, groupKey=?, sort=?, visible=1, icon=?, panel=? WHERE menuKey=?",
                (n, g, s, ic, p, k)
            )
    else:
        conn.executemany(
            "UPDATE menu_config SET displayName=?, groupKey=?, sort=?, visible=1, icon=?, panel=? WHERE menuKey=?",
            [(n, g, s, ic, p, k) for (k, n, g, s, ic, p) in db.DEFAULT_MENUS]
        )
    conn.commit()
    return _ok(message="已恢复默认")


# ============================================================
# 需求 7: AI 预设配置
# ============================================================

@bp.route("/ai-preset/list", methods=["GET"])
def ai_preset_list():
    conn = db._get_conn()
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("pageSize", 20, type=int), 1000)
    keyword = request.args.get("keyword", "").strip()
    enabled = request.args.get("enabled")
    extra = []
    args = []
    if enabled is not None:
        extra.append("enabled=?")
        args.append(1 if enabled in ("1", "true") else 0)
    p = db.paginate(conn, "ai_preset",
                    where=" AND ".join(extra) if extra else "",
                    params=tuple(args),
                    page=page, page_size=page_size,
                    keyword_fields=["name", "description", "formatPrompt"], keyword=keyword)
    return _ok(p)

@bp.route("/ai-preset", methods=["POST"])
def ai_preset_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    format_prompt = data.get("formatPrompt") or ""
    if not name or not format_prompt:
        return _err("name 和 formatPrompt 必填")
    desc = data.get("description", "")
    example = data.get("exampleOutput", "")
    enabled = int(bool(data.get("enabled", True)))
    is_default = int(bool(data.get("isDefault", False)))
    conn = db._get_conn()
    try:
        with db.tx() as tx:
            if is_default:
                tx.execute("UPDATE ai_preset SET isDefault=0")
            tx.execute(
                "INSERT INTO ai_preset(id,name,description,formatPrompt,exampleOutput,isDefault,enabled) "
                "VALUES (?,?,?,?,?,?,?)",
                (db.gen_id(), name, desc, format_prompt, example, is_default, enabled)
            )
    except sqlite3.IntegrityError:
        return _err("名称已存在")
    return _ok(message="已创建")

@bp.route("/ai-preset/<pid>", methods=["PUT"])
def ai_preset_update(pid):
    data = request.get_json(silent=True) or {}
    conn = db._get_conn()
    if "isDefault" in data and data["isDefault"]:
        conn.execute("UPDATE ai_preset SET isDefault=0")
    sets, args = [], []
    for f in ["name", "description", "formatPrompt", "exampleOutput", "enabled", "isDefault"]:
        if f in data:
            sets.append(f"{f}=?")
            args.append(int(data[f]) if f in ("enabled", "isDefault") else data[f])
    sets.append("updatedAt=?")
    args.append(_now())
    args.append(pid)
    if sets:
        conn.execute(f"UPDATE ai_preset SET {','.join(sets)} WHERE id=?", args)
    conn.commit()
    return _ok(message="已更新")

@bp.route("/ai-preset/batch-update", methods=["POST"])
def ai_preset_batch_update():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    sets, args = [], []
    for f in ["enabled", "description"]:
        if f in data:
            sets.append(f"{f}=?")
            args.append(int(data[f]) if f == "enabled" else data[f])
    if not sets:
        return _err("无批量可更新字段（formatPrompt 建议逐条编辑）")
    placeholders = ",".join("?" * len(ids))
    args.extend(ids)
    conn = db._get_conn()
    conn.execute(f"UPDATE ai_preset SET {','.join(sets)} WHERE id IN ({placeholders})", args)
    conn.commit()
    return _ok(message=f"已更新 {len(ids)} 条")

@bp.route("/ai-preset/<pid>", methods=["DELETE"])
def ai_preset_delete_one(pid):
    conn = db._get_conn()
    conn.execute("DELETE FROM ai_preset WHERE id=?", (pid,))
    conn.commit()
    return _ok(message="已删除")

@bp.route("/ai-preset/batch-delete", methods=["POST"])
def ai_preset_batch_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if len(ids) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条")
    conn = db._get_conn()
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM ai_preset WHERE id IN ({placeholders})", ids)
    conn.commit()
    return _ok(message=f"已删除 {len(ids)} 条", count=len(ids))

@bp.route("/ai-preset/active", methods=["GET"])
def ai_preset_active():
    """返回所有启用的预设（供 AI 界面下拉用）."""
    conn = db._get_conn()
    rows = conn.execute("SELECT id,name,description,formatPrompt,isDefault FROM ai_preset WHERE enabled=1 ORDER BY isDefault DESC, name").fetchall()
    return _ok([dict(r) for r in rows])


# ============================================================
# 批量导入（JSON / CSV / Excel 基础版）
# ============================================================

@bp.route("/import/<module>", methods=["POST"])
def batch_import(module):
    """从 JSON/CSV 文本导入. body: {format: 'json'|'csv'|'tsv', text: '...'}"""
    data = request.get_json(silent=True) or {}
    fmt = (data.get("format") or "json").lower()
    text = data.get("text") or ""
    if not text:
        return _err("text 必填")

    # 解析
    items = []
    try:
        if fmt == "json":
            arr = json.loads(text)
            if isinstance(arr, dict):
                arr = arr.get("items", [arr])
            items = list(arr)
        elif fmt in ("csv", "tsv"):
            delim = "\t" if fmt == "tsv" else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delim)
            items = [dict(r) for r in reader]
        else:
            return _err(f"不支持的格式: {fmt}")
    except Exception as e:
        return _err("解析失败: " + str(e))

    if len(items) == 0:
        return _err("无数据行")
    if len(items) > MAX_BATCH:
        return _err(f"单次最多 {MAX_BATCH} 条（当前 {len(items)}）")

    # 按模块分发到对应 batch-create
    url_map = {
        "url": url_batch_create,
        "onlineDoc": online_doc_batch,
        "localDoc": local_doc_batch,
        "md": md_batch,
        "aiPreset": ai_preset_create,  # 单条, 循环调用
    }
    if module not in url_map:
        return _err(f"未知模块: {module}")

    if module == "aiPreset":
        # AI 预设单条接口，批量循环
        success, fail, details = 0, 0, []
        for it in items:
            try:
                from flask import Request as _R
                # 直接走底层 DB（绕过 request 上下文）
                name = (it.get("name") or "").strip()
                fp = it.get("formatPrompt") or ""
                if not name or not fp:
                    raise ValueError("name/formatPrompt 必填")
                conn = db._get_conn()
                conn.execute(
                    "INSERT INTO ai_preset(id,name,description,formatPrompt,exampleOutput,isDefault,enabled) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (db.gen_id(), name, it.get("description", ""), fp,
                     it.get("exampleOutput", ""), int(bool(it.get("isDefault", False))),
                     int(bool(it.get("enabled", True))))
                )
                conn.commit()
                success += 1
            except Exception as e:
                fail += 1
                details.append({"row": it.get("name", "?"), "error": str(e)})
        db.log_batch(module, "import", len(items), success, fail, details)
        return _ok(total=len(items), success=success, fail=fail, detail=details)

    # 其他模块走 batch 接口的底层逻辑：直接调 Flask test client 会破坏上下文，这里直接复用 db 层
    # 简单做法：构造一个临时 request body 然后手动调函数
    import flask
    # 把 items 塞进模拟的 request 对象（复用函数签名）
    # 由于函数内部直接用 Flask request，需要临时替换. 这里用 test client 更简洁.
    from minio_server import app
    with app.test_request_context(f"/rs/{module}/batch", method="POST",
                                  json={"items": items}):
        resp = url_map[module]()
    if isinstance(resp, tuple):
        body, code = resp
        return body, code
    return resp


# ============================================================
# 内置浏览器辅助：打开 URL（pywebview create_window 方式）
# ============================================================

@bp.route("/browser/open", methods=["POST"])
def browser_open():
    """返回一个可用于前端 iframe 内嵌的页面地址 —— 或 pywebview 多窗口."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return _err("url 必填")
    # 简单的验证
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    # 这里不真正打开浏览器窗口（pywebview 必须在主线程 create_window），
    # 而是返回一个标记，由前端通过 pywebview 的 JS API window.open 打开或 iframe 内嵌
    return _ok({"url": url, "action": "open"})


# ---------- 注册 ----------
def register_module_blueprint(app):
    app.register_blueprint(bp)
