# -*- coding: utf-8 -*-
"""DuckDB 本地存储 + 通用字段操作 + 工作流引擎

- 数据文件：exe 同目录 靓号查询数据.duckdb（源码运行 = 当前目录）
- 工作流文件：exe 同目录 工作流.json
- duckdb 连接非线程安全，全部操作走 _lock；表/列名用双引号转义防注入
"""
import io
import json
import os
import sys
import threading

DB_FILENAME = "靓号查询数据.duckdb"
WORKFLOW_FILENAME = "工作流.json"

_lock = threading.Lock()


def _base_dir():
    """exe：exe 同目录（双击/快捷方式 cwd 都可能变）；源码：当前目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def get_db_path():
    return os.path.join(_base_dir(), DB_FILENAME)


def get_workflow_path():
    return os.path.join(_base_dir(), WORKFLOW_FILENAME)


def _quote(name):
    """SQL 双引号标识符转义（防表/列名注入）"""
    return '"' + str(name).replace('"', '""') + '"'


def _col_exists(con, qtable, column):
    cols = con.execute('SELECT * FROM ' + qtable + ' LIMIT 0').description
    return column in [d[0] for d in cols]


def write_table(table_name, headers, rows):
    """写入/覆盖一张表（列全 VARCHAR，中文列名），返回行数"""
    import duckdb
    path = get_db_path()
    cols = ','.join(_quote(h) + ' VARCHAR' for h in headers)
    q = _quote(table_name)
    with _lock:
        con = duckdb.connect(path)
        try:
            con.execute('DROP TABLE IF EXISTS ' + q)
            con.execute('CREATE TABLE ' + q + ' (' + cols + ')')
            placeholders = ','.join(['?'] * len(headers))
            CH = 500  # 分批插入，避免大表单条 INSERT 过长
            for i in range(0, len(rows), CH):
                chunk = rows[i:i + CH]
                con.executemany(
                    'INSERT INTO ' + q + ' VALUES (' + placeholders + ')',
                    [tuple('' if v is None else v for v in r) for r in chunk])
        finally:
            con.close()
    return len(rows)


def list_tables():
    """全部表：[{name, rows, cols}]，按表名倒序"""
    import duckdb
    path = get_db_path()
    if not os.path.exists(path):
        return []
    with _lock:
        con = duckdb.connect(path)
        try:
            names = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' ORDER BY table_name DESC").fetchall()
            out = []
            for (name,) in names:
                q = _quote(name)
                n = con.execute('SELECT count(*) FROM ' + q).fetchone()[0]
                cols = len(con.execute('SELECT * FROM ' + q + ' LIMIT 0').description)
                out.append({"name": name, "rows": n, "cols": cols})
            return out
        finally:
            con.close()


def get_table(name):
    """单表元信息：{name, columns, count}；表不存在返回 None"""
    import duckdb
    path = get_db_path()
    if not os.path.exists(path):
        return None
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            try:
                desc = con.execute('SELECT * FROM ' + q + ' LIMIT 0').description
            except Exception:
                return None  # 表不存在
            count = con.execute('SELECT count(*) FROM ' + q).fetchone()[0]
            return {"name": name, "columns": [d[0] for d in desc], "count": count}
        finally:
            con.close()


def preview_table(name, offset=0, limit=100):
    """分页预览：{name, columns, total, rows:[{id: rowid, cells:[...]}]}；表不存在 None"""
    import duckdb
    path = get_db_path()
    if not os.path.exists(path):
        return None
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            try:
                desc = con.execute('SELECT * FROM ' + q + ' LIMIT 0').description
            except Exception:
                return None
            cols = [d[0] for d in desc]
            total = con.execute('SELECT count(*) FROM ' + q).fetchone()[0]
            rows = con.execute(
                'SELECT rowid, * FROM ' + q + ' ORDER BY rowid LIMIT ? OFFSET ?',
                [limit, offset]).fetchall()
            out = [{"id": r[0], "cells": ['' if v is None else str(v) for v in r[1:]]}
                   for r in rows]
            return {"name": name, "columns": cols, "total": total, "rows": out}
        finally:
            con.close()


def export_table_xlsx(name):
    """导出该表为 xlsx bytes（列顺序 = 建表顺序）；表不存在返回 None"""
    import pandas as pd
    import duckdb
    path = get_db_path()
    if not os.path.exists(path):
        return None
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            try:
                df = con.execute('SELECT * FROM ' + q).df()
            except Exception:
                return None  # 表不存在
        finally:
            con.close()
    bio = io.BytesIO()
    df.to_excel(bio, index=False)
    bio.seek(0)
    return bio.getvalue()


# ---------- 通用字段操作（rowid 定位行；返回值 = 影响行数或布尔） ----------

def _sql_str(value):
    """VARCHAR 默认值 → SQL 字符串字面量（单引号转义，防注入）"""
    return "'" + str(value).replace("'", "''") + "'"


def _count_rows(con, q, where=None, args=None):
    """受影响行数：duckdb 没有 SQLite 的 changes()，先按同一 WHERE 统计（行数不受 UPDATE 影响）"""
    if where:
        return con.execute('SELECT count(*) FROM ' + q + ' WHERE ' + where, args or []).fetchone()[0]
    return con.execute('SELECT count(*) FROM ' + q).fetchone()[0]


def edit_cell(name, rowid, column, value):
    """改单个单元格：UPDATE SET col=? WHERE rowid=?；返回是否成功"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            try:
                exists = _col_exists(con, q, column)
            except Exception:
                return False  # 表不存在
            if not exists:
                return False
            con.execute('UPDATE ' + q + ' SET ' + _quote(column) + '=? WHERE rowid=?',
                        [str(value), int(rowid)])
            return True
        finally:
            con.close()


def set_column_value(name, column, value):
    """整列替换为常量；返回影响行数"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, column):
                raise ValueError("列不存在: " + column)
            n = _count_rows(con, q)
            con.execute('UPDATE ' + q + ' SET ' + _quote(column) + '=?', [str(value)])
            return n
        finally:
            con.close()


def set_column_from(name, target, source):
    """整列用另一列的值；返回影响行数"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, target) or not _col_exists(con, q, source):
                raise ValueError("列不存在")
            n = _count_rows(con, q)
            con.execute('UPDATE ' + q + ' SET ' + _quote(target) + ' = ' + _quote(source))
            return n
        finally:
            con.close()


def replace_text(name, column, pattern, replacement, regex=False):
    """列内文本替换（regex=False 为字面替换全部出现）；返回影响行数"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, column):
                raise ValueError("列不存在: " + column)
            # 影响行数 = 实际包含匹配串的行（整列无 WHERE，不能直接统计总数）
            n = _count_rows(con, q,
                            "regexp_matches(" + _quote(column) + ", ?)" if regex
                            else "contains(" + _quote(column) + ", ?)",
                            [str(pattern)])
            if regex:
                con.execute("UPDATE " + q + " SET " + _quote(column) +
                            " = regexp_replace(" + _quote(column) + ", ?, ?)",
                            [str(pattern), str(replacement)])
            else:
                con.execute("UPDATE " + q + " SET " + _quote(column) +
                            " = replace(" + _quote(column) + ", ?, ?)",
                            [str(pattern), str(replacement)])
            return n
        finally:
            con.close()


def _append_prepend(name, column, text, match_col, keyword, at_start):
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, column):
                raise ValueError("列不存在: " + column)
            col = _quote(column)
            expr = ("(? || " + col + ")") if at_start else ("(" + col + " || ?)")
            where, where_args = None, None
            if match_col and keyword:
                if not _col_exists(con, q, match_col):
                    raise ValueError("列不存在: " + match_col)
                # duckdb contains(列, ?) = 子串包含匹配，无需 LIKE 转义（参数化防注入）
                where = "contains(" + _quote(match_col) + ", ?)"
                where_args = [str(keyword)]
            n = _count_rows(con, q, where, where_args)
            sql = "UPDATE " + q + " SET " + col + " = " + expr
            args = [str(text)]
            if where:
                sql += " WHERE " + where
                args += where_args
            con.execute(sql, args)
            return n
        finally:
            con.close()


def append_text(name, column, text, match_col="", keyword=""):
    """字段末尾批量追加文字（可限定：匹配列包含关键词才操作）"""
    return _append_prepend(name, column, text, match_col, keyword, at_start=False)


def prepend_text(name, column, text, match_col="", keyword=""):
    """字段开头批量插入文字（可限定条件）"""
    return _append_prepend(name, column, text, match_col, keyword, at_start=True)


def delete_rows_contains(name, column, keyword):
    """删除匹配行：该字段值包含关键词；返回删除行数"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, column):
                raise ValueError("列不存在: " + column)
            where = "contains(" + _quote(column) + ", ?)"
            args = [str(keyword)]
            n = _count_rows(con, q, where, args)
            con.execute('DELETE FROM ' + q + ' WHERE ' + where, args)
            return n
        finally:
            con.close()


def set_if_contains(name, match_col, keyword, target_col, value):
    """条件置值：匹配列包含关键词 → 目标列设为 value；返回影响行数"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, match_col) or not _col_exists(con, q, target_col):
                raise ValueError("列不存在")
            where = "contains(" + _quote(match_col) + ", ?)"
            args = [str(keyword)]
            n = _count_rows(con, q, where, args)
            con.execute('UPDATE ' + q + ' SET ' + _quote(target_col) + '=? WHERE ' + where,
                        [str(value)] + args)
            return n
        finally:
            con.close()


def add_column(name, column, default=""):
    """新增列（VARCHAR）；返回是否成功"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if _col_exists(con, q, column):
                raise ValueError("列已存在: " + column)
            # duckdb 的 ALTER ADD COLUMN 不允许 DEFAULT 参数，用转义后的字面量
            con.execute('ALTER TABLE ' + q + ' ADD COLUMN ' + _quote(column) +
                        ' VARCHAR DEFAULT ' + _sql_str(default))
            return True
        finally:
            con.close()


def drop_column(name, column):
    """删除列；返回是否成功"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, column):
                raise ValueError("列不存在: " + column)
            con.execute('ALTER TABLE ' + q + ' DROP COLUMN ' + _quote(column))
            return True
        finally:
            con.close()


def rename_column(name, old, new):
    """重命名列；返回是否成功"""
    import duckdb
    path = get_db_path()
    q = _quote(name)
    with _lock:
        con = duckdb.connect(path)
        try:
            if not _col_exists(con, q, old):
                raise ValueError("列不存在: " + old)
            con.execute('ALTER TABLE ' + q + ' RENAME COLUMN ' + _quote(old) + ' TO ' + _quote(new))
            return True
        finally:
            con.close()


# ---------- 工作流（步骤引擎 + JSON 存取） ----------
# 步骤：{op, ...args}
# op: set_column{column,value} / copy_column{target,source} / replace_text{column,pattern,replacement}
#     append_text{column,text,match_column?,match_keyword?} / prepend_text{...}
#     delete_rows{column,keyword} / set_if_contains{match_column,keyword,target_column,value}
#     add_column{column,default?} / drop_column{column} / rename_column{old,new}

DEFAULT_WORKFLOW = {
    "active": False,
    "name": "",
    "steps": [],
}


def load_workflow():
    """读工作流；文件不存在返回默认（未启用）"""
    path = get_workflow_path()
    if not os.path.exists(path):
        return dict(DEFAULT_WORKFLOW)
    try:
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
        return {"active": bool(wf.get("active")), "name": str(wf.get("name", "")),
                "steps": wf.get("steps") or []}
    except Exception:
        return dict(DEFAULT_WORKFLOW)


def save_workflow(wf):
    """保存工作流（原子写：先写临时文件再替换）"""
    path = get_workflow_path()
    data = {"active": bool(wf.get("active")), "name": str(wf.get("name", "")),
            "steps": wf.get("steps") or []}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return data


def step_describe(step):
    """步骤 → 中文描述（工作流面板展示）"""
    op = step.get("op", "?")
    c = step.get("column") or step.get("match_column") or ""
    t = step.get("target_column") or ""
    kw = step.get("keyword") or ""
    v = step.get("value") or step.get("replacement") or ""
    if op == "set_column":
        return "整列 [{}] 替换为 “{}”".format(c, v)
    if op == "copy_column":
        return "复制 [{}] 到 [{}]".format(step.get("source", ""), t)
    if op == "replace_text":
        return "[{}] 文本替换 “{}” → “{}”".format(c, step.get("pattern", ""), v)
    if op == "append_text":
        s = "[{}] 末尾追加 “{}”".format(c, step.get("text", ""))
        return s + ("（仅当 [{}] 含 “{}”）".format(step.get("match_column", ""), kw) if kw else "")
    if op == "prepend_text":
        s = "[{}] 开头插入 “{}”".format(c, step.get("text", ""))
        return s + ("（仅当 [{}] 含 “{}”）".format(step.get("match_column", ""), kw) if kw else "")
    if op == "delete_rows":
        return "删除 [{}] 包含 “{}” 的行".format(c, kw)
    if op == "set_if_contains":
        return "[{}] 含 “{}” → [{}] = “{}”".format(c, kw, t, v)
    if op == "add_column":
        return "新增列 [{}]（默认 “{}”）".format(c, v)
    if op == "drop_column":
        return "删除列 [{}]".format(c)
    if op == "rename_column":
        return "重命名 [{}] → [{}]".format(step.get("old", ""), c)
    return "未知步骤: " + op


def apply_workflow(name, wf):
    """对表顺序执行工作流全部步骤；每步独立 try，返回每步结果列表"""
    steps = wf.get("steps") or []
    results = []
    for i, step in enumerate(steps):
        op = step.get("op", "")
        try:
            n = _run_step(name, step)
            results.append({"i": i, "op": op, "desc": step_describe(step),
                            "ok": True, "affected": n})
        except Exception as e:
            results.append({"i": i, "op": op, "desc": step_describe(step),
                            "ok": False, "error": str(e)})
    return results


def _run_step(name, step):
    op = step.get("op", "")
    if op == "set_column":
        return set_column_value(name, step["column"], step.get("value", ""))
    if op == "copy_column":
        return set_column_from(name, step["target"], step["source"])
    if op == "replace_text":
        return replace_text(name, step["column"], step["pattern"],
                            step.get("replacement", ""), regex=bool(step.get("regex")))
    if op == "append_text":
        return append_text(name, step["column"], step.get("text", ""),
                           step.get("match_column", ""), step.get("match_keyword", ""))
    if op == "prepend_text":
        return prepend_text(name, step["column"], step.get("text", ""),
                            step.get("match_column", ""), step.get("match_keyword", ""))
    if op == "delete_rows":
        return delete_rows_contains(name, step["column"], step.get("keyword", ""))
    if op == "set_if_contains":
        return set_if_contains(name, step["match_column"], step.get("keyword", ""),
                               step["target_column"], step.get("value", ""))
    if op == "add_column":
        add_column(name, step["column"], step.get("default", ""))
        return 1
    if op == "drop_column":
        drop_column(name, step["column"])
        return 1
    if op == "rename_column":
        rename_column(name, step["old"], step["column"])
        return 1
    raise ValueError("未知操作: " + op)
