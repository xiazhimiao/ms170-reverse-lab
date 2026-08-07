# -*- coding: utf-8 -*-
"""民生靓号查询工具 v5（Web 版）

- 本地 Flask 服务 + 默认浏览器打开界面 + 系统托盘图标（pystray）
- 前端：单页 HTML 内嵌，毛玻璃（backdrop-filter）+ 二次元随机背景 + 三页签
- 查询：后台线程分页拉取，套餐信息并行查询，SSE 推送进度到页面
- 等级列表读取 类型.txt（17 个字母型等级，原样展示，不翻译）

运行：python web_gui.py   （--no-browser 不自动开浏览器，--port 指定端口）
"""

import io
import json
import os
import queue
import socket
import sys
import threading
import time
import webbrowser

from flask import Flask, Response, jsonify, request
from PIL import Image, ImageDraw
from werkzeug.serving import make_server

import anime_bg
import duckdb_store
from phone_number_fetcher import PhoneNumberFetcher

PORT = 8755
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- 等级列表（类型.txt 优先，内嵌兜底） ----------

RANKS_EMBEDDED = [
    "AAABBB", "ABCABC", "ABCD", "DCBA", "ABC", "ABCDABCD", "AAA", "ABAB",
    "ABABAB", "AABBCC", "AABB", "AAAAB", "AAAB", "AAAAAB", "ABBA",
    "ABABABAB", "AABBCCDD",
]


def load_ranks():
    """号码类型列表：优先 rankList 接口动态获取（小程序真实来源，40 种含中文类型），
    接口失败回退类型.txt / 内嵌兜底"""
    try:
        ranks = engine.fetcher.get_rank_list()
        if ranks:
            return ranks
    except Exception:
        pass
    try:
        with open(os.path.join(BASE_DIR, "类型.txt"), encoding="utf-8") as f:
            ranks = [ln.strip() for ln in f if ln.strip()]
        if ranks:
            return ranks
    except Exception:
        pass
    return list(RANKS_EMBEDDED)


# ---------- 查询引擎（后台线程 + SSE 事件推送） ----------

class QueryEngine:
    """靓号分页查询任务。worker 线程内自维护累计数，事件推给所有 SSE 订阅者"""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs = set()
        self._running = False
        self._stop_flag = False
        self._thread = None
        self.fetcher = PhoneNumberFetcher()

    # ---- 订阅 ----
    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def emit(self, event):
        with self._lock:
            for q in list(self._subs):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    # ---- 任务控制 ----
    def start(self, params):
        with self._lock:
            if self._running:
                return False
            self._running, self._stop_flag = True, False
        self._thread = threading.Thread(target=self._worker, args=(dict(params),), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop_flag = True
        self.emit({"type": "log", "level": "info", "msg": "正在停止..."})

    def is_running(self):
        return self._running

    def _done(self, reason, total):
        with self._lock:
            self._running = False
        self.emit({"type": "log", "level": "ok", "msg": reason})
        self.emit({"type": "done", "total": total, "reason": reason})

    def _worker(self, params):
        self.emit({"type": "log", "level": "info", "msg": "正在校验渠道二维码..."})
        if not self.fetcher._ensure_qr():
            self._done("二维码校验失败，请检查渠道码是否有效", 0)
            return

        series = params.get("series", "h5")
        province = params.get("province", "")
        city = params.get("city", "")
        rank = params.get("rank", "")
        msisdn = params.get("msisdn", "")   # 号码关键词搜索（与地区/类型独立可组合）
        try:
            max_pages = int(params.get("max_pages", 0) or 0)
        except (TypeError, ValueError):
            max_pages = 0
        try:
            interval = float(params.get("interval", 2))
        except (TypeError, ValueError):
            interval = 2
        # 间隔保护：不低于 1s，避免对服务器造成压力或被拉入 IP 黑名单
        interval = max(interval, 1.0)

        total, page = 0, 1
        try:
            while not self._stop_flag:
                if max_pages and page > max_pages:
                    self.emit({"type": "log", "level": "info", "msg": f"达到最大页数 {max_pages}，停止"})
                    break
                rows = self.fetcher.get_phone_numbers_parallel(
                    page, province, city, rank, msisdn, series, workers=5)
                if rows is None:
                    self.emit({"type": "log", "level": "error", "msg": f"第 {page} 页请求异常，查询终止"})
                    break
                if not rows:
                    self.emit({"type": "log", "level": "info", "msg": f"第 {page} 页无数据，查询结束"})
                    break
                total += len(rows)
                self.emit({"type": "page", "page": page, "rows": rows, "total": total})
                self.emit({"type": "log", "level": "ok", "msg": f"第 {page} 页完成（{len(rows)} 条），累计 {total} 条"})
                page += 1
                if interval > 0 and not self._stop_flag:
                    time.sleep(interval)
        finally:
            self._done("查询结束" if not self._stop_flag else "已停止", total)


engine = QueryEngine()

# ---------- Flask 应用 ----------

app = Flask(__name__)

SERIES = [("h5", "靓号专区 (h5)"), ("liu", "流量卡专区 (liu)")]


def ok(data=None, **extra):
    r = {"code": 0, "msg": "ok", "data": data or []}
    r.update(extra)
    return jsonify(r)


@app.route("/api/ranks")
def api_ranks():
    return ok(load_ranks())


@app.route("/api/series")
def api_series():
    return ok([{"id": sid, "name": name} for sid, name in SERIES])


@app.route("/api/provinces")
def api_provinces():
    try:
        return ok(engine.fetcher.get_provinces())
    except Exception as e:
        return jsonify({"code": 1, "msg": f"省份加载失败: {e}", "data": []})


@app.route("/api/cities/<province_id>")
def api_cities(province_id):
    try:
        return ok(engine.fetcher.get_cities(province_id))
    except Exception as e:
        return jsonify({"code": 1, "msg": f"城市加载失败: {e}", "data": []})


@app.route("/api/query/start", methods=["POST"])
def api_query_start():
    params = request.get_json(force=True, silent=True) or {}
    if engine.start(params):
        return jsonify({"code": 0, "msg": "查询已启动"})
    return jsonify({"code": 1, "msg": "已有查询在运行"})


@app.route("/api/query/stop", methods=["POST"])
def api_query_stop():
    engine.stop()
    return jsonify({"code": 0, "msg": "已请求停止"})


@app.route("/api/query/status")
def api_query_status():
    return jsonify({"code": 0, "running": engine.is_running()})


@app.route("/api/events")
def api_events():
    """SSE：查询进度推送"""
    q = engine.subscribe()

    def gen():
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            engine.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/categories")
def api_categories():
    try:
        return ok(engine.fetcher.get_categories())
    except Exception as e:
        return jsonify({"code": 1, "msg": f"分类加载失败: {e}", "data": []})


@app.route("/api/products")
def api_products():
    cat = request.args.get("cat", "")
    if not cat:
        return jsonify({"code": 1, "msg": "缺少分类 ID", "data": []})
    try:
        return ok(engine.fetcher.get_products(cat))
    except Exception as e:
        return jsonify({"code": 1, "msg": f"产品加载失败: {e}", "data": []})


@app.route("/api/product_info")
def api_product_info():
    pid = request.args.get("id", "")
    if not pid:
        return jsonify({"code": 1, "msg": "缺少产品 ID", "data": {}})
    try:
        return jsonify({"code": 0, "msg": "ok", "data": engine.fetcher.get_product_info(pid) or {}})
    except Exception as e:
        return jsonify({"code": 1, "msg": f"产品详情失败: {e}", "data": {}})


@app.route("/api/kd")
def api_kd():
    try:
        return ok(engine.fetcher.get_kd())
    except Exception as e:
        return jsonify({"code": 1, "msg": f"物流查询失败: {e}", "data": []})


@app.route("/api/orders")
def api_orders():
    cardno = request.args.get("cardno", "")
    phone = request.args.get("phone", "")
    if not cardno and not phone:
        return jsonify({"code": 1, "msg": "请至少填写证件号或手机号", "data": []})
    try:
        return ok(engine.fetcher.get_orders(cardno, phone))
    except Exception as e:
        return jsonify({"code": 1, "msg": f"订单查询失败: {e}", "data": []})


# 异步导出任务表（勾选「附带更多套餐」时套餐拉取耗时，后台任务 + 进度轮询）
_EXPORT_TASKS = {}    # task_id -> {status/done/total/data/mime/fname/error}
_EXPORT_LOCK = threading.Lock()


@app.route("/api/export", methods=["POST"])
def api_export():
    # 兜底：任何异常写 export_error.log（exe 无 console，靠文件拿 traceback）
    try:
        body = request.get_json(force=True, silent=True) or {}
        # [诊断] 记录导出请求，便于排查 500
        print("EXPORT_BODY:", json.dumps(body, ensure_ascii=False)[:3000], flush=True)
        if body.get("more"):
            # 附带更多套餐：每个号码都要并发拉套餐，耗时较长 → 异步任务，
            # 返回 task id，前端轮询 /api/export/status 显示进度条
            rows = body.get("rows") or []
            if not rows:
                return jsonify({"code": 1, "msg": "没有可导出的数据", "data": []})
            import uuid
            task_id = uuid.uuid4().hex[:12]
            with _EXPORT_LOCK:
                _EXPORT_TASKS[task_id] = {"status": "running", "done": 0,
                                          "total": len(rows), "data": None,
                                          "mime": "", "fname": "", "table": "",
                                          "rows": 0, "error": ""}
            threading.Thread(target=_export_worker, args=(task_id, body), daemon=True).start()
            return jsonify({"code": 0, "task": task_id})
        return _export_sync(body)
    except Exception:
        import traceback
        try:
            with open("export_error.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + traceback.format_exc() + "\n")
        except Exception:
            pass
        return jsonify({"code": 1, "msg": "导出失败"}), 500


def _export_worker(task_id, body):
    """异步导出任务：并发拉套餐（每完成一个更新进度）→ 写 duckdb 或生成文件 → 存结果"""
    try:
        def progress(done, total):
            with _EXPORT_LOCK:
                _EXPORT_TASKS[task_id]["done"] = done
        res = _do_export(body, progress)
        with _EXPORT_LOCK:
            t = _EXPORT_TASKS[task_id]
            t["status"] = "done"
            t["done"] = t["total"]
            if res["kind"] == "table":
                t["table"] = res["table"]
                t["rows"] = res["rows"]
                t["workflow"] = res.get("workflow", [])
            else:
                t["data"] = res["data"]
                t["mime"] = res["mime"]
                t["fname"] = res["fname"]
    except Exception as e:
        import traceback
        try:
            with open("export_error.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") +
                        f"[export task {task_id}] " + traceback.format_exc() + "\n")
        except Exception:
            pass
        with _EXPORT_LOCK:
            _EXPORT_TASKS[task_id]["status"] = "error"
            _EXPORT_TASKS[task_id]["error"] = str(e)


@app.route("/api/export/status")
def api_export_status():
    """异步导出进度：{status: running/done/error, done, total, error, table, rows}"""
    task = request.args.get("task", "")
    with _EXPORT_LOCK:
        t = _EXPORT_TASKS.get(task)
    if not t:
        return jsonify({"code": 1, "msg": "导出任务不存在"})
    return jsonify({"code": 0, "status": t["status"], "done": t["done"],
                    "total": t["total"], "error": t.get("error", ""),
                    "table": t.get("table", ""), "rows": t.get("rows", 0),
                    "workflow": t.get("workflow", [])})


@app.route("/api/export/download")
def api_export_download():
    """异步导出完成后取文件（文件名/内容存任务表）"""
    task = request.args.get("task", "")
    with _EXPORT_LOCK:
        t = _EXPORT_TASKS.get(task)
    if not t:
        return jsonify({"code": 1, "msg": "导出任务不存在"}), 404
    if t["status"] != "done":
        return jsonify({"code": 1, "msg": "导出尚未完成"}), 400
    if not t.get("fname"):
        return jsonify({"code": 1, "msg": "该任务写入 duckdb，无文件可下载"}), 400
    import urllib.parse
    fmt = t["fname"].rsplit(".", 1)[-1]
    quoted = urllib.parse.quote(t["fname"])
    return Response(t["data"], mimetype=t["mime"],
                    headers={"Content-Disposition": f"attachment; filename=export.{fmt}; filename*=UTF-8''{quoted}"})


def _export_sync(body):
    """同步导出（未勾选更多套餐：列表已加载，快）"""
    res = _do_export(body)
    if res["kind"] == "table":
        wf_msg = ""
        wf = res.get("workflow", [])
        if wf:
            ok = sum(1 for r in wf if r["ok"])
            wf_msg = " · 工作流 {}/{} 步成功".format(ok, len(wf))
        return jsonify({"code": 0, "table": res["table"], "rows": res["rows"],
                        "workflow": wf,
                        "msg": "已写入 duckdb：表 {}（{} 行）{}".format(
                            res["table"], res["rows"], wf_msg)})
    if res.get("data") is None:
        return jsonify({"code": 1, "msg": "没有可导出的数据", "data": []})
    import urllib.parse
    quoted = urllib.parse.quote(res["fname"])
    fmt = res["fname"].rsplit(".", 1)[-1]
    return Response(res["data"], mimetype=res["mime"],
                    headers={"Content-Disposition": f"attachment; filename=export.{fmt}; filename*=UTF-8''{quoted}"})


def _do_export(body, progress=None):
    """执行导出：整理行 → 拉更多套餐（进度回调）→ 写 duckdb 或生成文件。

    Args:
        body: 导出请求体（rows/fmt/more/fname_info）
        progress: 可选回调 progress(done, total)，「更多套餐」拉取阶段每完成一个号码调用

    Returns:
        {"kind": "table", "table", "rows"} 或 {"kind": "file", "data", "mime", "fname"}
    """
    rows = body.get("rows") or []
    fmt = body.get("fmt", "xlsx")
    more = bool(body.get("more"))          # 附带更多套餐
    fname_info = body.get("fname_info") or "全国_全部"  # 文件名信息（省_市）
    if not rows:
        return {"kind": "file", "data": None, "mime": "", "fname": ""}

    # 兼容旧版前端直接发送的对象行（{index, phone_number, ...}）：规范化为数组行。
    # 对象行的金额字段为「分」（worker 原始值），与前端 yuan() 一致转成元。
    if isinstance(rows[0], dict):
        rows = [[r.get("index", ""), r.get("phone_number", ""), r.get("province", ""),
                 r.get("city", ""), r.get("rank", ""),
                 (r.get("bossPrestore") or 0) // 100, (r.get("minConsume") or 0) // 100,
                 r.get("productName", "") or "", (r.get("productFee") or 0) // 100,
                 r.get("liuTotal", "") or "", r.get("callTotal", "") or "",
                 r.get("package", "") or ""] for r in rows]
    elif rows and len(rows[0]) < 12:
        # 更早版本的列表行列数不足：补齐空列，避免列数不匹配
        rows = [list(r) + [""] * (12 - len(r)) for r in rows]

    headers = ["序号", "手机号", "省份", "城市", "等级", "预存(元)", "月低消(元)",
               "套餐", "月费(元/月)", "流量(G)", "通话(分钟)", "套餐详情"]
    if more:
        # 并发拉取每个号码的可办套餐：全部合并进「其他套餐」字段
        # （第一个=默认/最划算套餐已在前面的「套餐/月费」列，这里放第2个及以后，| 分隔带详情）
        from concurrent.futures import ThreadPoolExecutor, as_completed
        phones = [r[1] for r in rows]
        extra = [None] * len(phones)
        done = [0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(engine.fetcher.get_products_for_number, ph): i
                    for i, ph in enumerate(phones)}
            for fut in as_completed(futs):
                i = futs[fut]
                pkgs = fut.result() or []
                rest = " | ".join("{}（{}元/月）：{}".format(
                    p.get("productName", ""), (p.get("productFee") or 0) // 100,
                    (p.get("serviceDesc") or "").replace("\n", " ").strip())
                    for p in pkgs[1:])
                extra[i] = [rest]
                done[0] += 1
                if progress:
                    progress(done[0], len(phones))
        headers += ["其他套餐"]
        rows = [list(r) + e for r, e in zip(rows, extra)]

    if fmt == "duckdb":
        # 写入本地 DuckDB 文件建表（表名 = 靓号查询_省_市_时间戳）
        table = "靓号查询_{}_{}".format(
            fname_info.replace("/", "_").replace("\\", "_"),
            time.strftime("%Y%m%d_%H%M%S"))
        n = duckdb_store.write_table(table, headers, rows)
        # 工作流（启用时）自动应用到新表
        wf = duckdb_store.load_workflow()
        wf_results = duckdb_store.apply_workflow(table, wf) if (wf["active"] and wf["steps"]) else []
        return {"kind": "table", "table": table, "rows": n, "workflow": wf_results}

    bio = io.BytesIO()
    fname = "靓号查询_{}_{}.{}".format(
        fname_info, time.strftime("%Y%m%d_%H%M%S"), fmt)
    if fmt == "csv":
        import csv
        text = io.StringIO()
        w = csv.writer(text)
        w.writerow(headers)
        w.writerows(rows)
        bio.write(("﻿" + text.getvalue()).encode("utf-8"))
        mime = "text/csv"
    else:
        import pandas as pd
        pd.DataFrame(rows, columns=headers).to_excel(bio, index=False)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    bio.seek(0)
    return {"kind": "file", "data": bio.getvalue(), "mime": mime, "fname": fname}


# ---------- 数据库管理（duckdb 表状态 + 导出 xlsx） ----------

@app.route("/api/db/tables")
def api_db_tables():
    return jsonify({"code": 0, "data": duckdb_store.list_tables()})


@app.route("/api/db/table")
def api_db_table():
    name = request.args.get("name", "")
    t = duckdb_store.get_table(name)
    if t is None:
        return jsonify({"code": 1, "msg": "表不存在"})
    return jsonify({"code": 0, "data": t})


@app.route("/api/db/data")
def api_db_data():
    """分页预览（带 rowid 供单元格编辑定位）：?name=&offset=&limit="""
    name = request.args.get("name", "")
    try:
        offset = max(0, int(request.args.get("offset", "0")))
        limit = min(500, max(1, int(request.args.get("limit", "100"))))
    except ValueError:
        offset, limit = 0, 100
    t = duckdb_store.preview_table(name, offset, limit)
    if t is None:
        return jsonify({"code": 1, "msg": "表不存在"})
    return jsonify({"code": 0, "data": t})


@app.route("/api/db/edit", methods=["POST"])
def api_db_edit():
    """编辑单个单元格：{name, rowid, column, value}"""
    body = request.get_json(force=True, silent=True) or {}
    ok = duckdb_store.edit_cell(body.get("name", ""), body.get("rowid"),
                                body.get("column", ""), body.get("value", ""))
    if not ok:
        return jsonify({"code": 1, "msg": "编辑失败（表/列不存在）"})
    return jsonify({"code": 0, "msg": "已保存"})


# 前端参数名 → duckdb_store 函数参数名 别名表（前端统一 match_column/match_keyword/target_column/column 风格）
_OP_ARG_ALIAS = {
    "append_text": {"match_column": "match_col", "match_keyword": "keyword"},
    "prepend_text": {"match_column": "match_col", "match_keyword": "keyword"},
    "set_if_contains": {"match_column": "match_col", "target_column": "target_col"},
    "rename_column": {"column": "new"},
}


@app.route("/api/db/op", methods=["POST"])
def api_db_op():
    """通用字段操作入口：{name, op, ...args}（op = duckdb_store 原语名）"""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name", "")
    op = body.get("op", "")
    fn = getattr(duckdb_store, op, None)
    if not fn:
        return jsonify({"code": 1, "msg": "未知操作: " + op})
    if duckdb_store.get_table(name) is None:
        return jsonify({"code": 1, "msg": "表不存在"})
    # 只传操作函数认识的参数（name 固定第一参数），按别名表翻译前端参数名
    import inspect
    params = inspect.signature(fn).parameters
    alias = _OP_ARG_ALIAS.get(op, {})
    kwargs = {}
    for k, v in body.items():
        if k in ("name", "op"):
            continue
        key = alias.get(k, k)
        if key in params:
            kwargs[key] = v
    try:
        result = fn(name, **kwargs)
        if isinstance(result, bool):
            result = 1 if result else 0
        return jsonify({"code": 0, "affected": result,
                        "msg": "操作成功（影响 {} 行）".format(result)})
    except Exception as e:
        return jsonify({"code": 1, "msg": "操作失败: {}".format(e)})


# ---------- 工作流（步骤引擎 + 导入导出 + 自动应用） ----------

@app.route("/api/workflow", methods=["GET"])
def api_workflow_get():
    wf = duckdb_store.load_workflow()
    wf["file"] = duckdb_store.get_workflow_path()
    return jsonify({"code": 0, "data": wf})


@app.route("/api/workflow", methods=["POST"])
def api_workflow_save():
    body = request.get_json(force=True, silent=True) or {}
    wf = duckdb_store.save_workflow(body)
    return jsonify({"code": 0, "data": wf, "msg": "工作流已保存"})


@app.route("/api/workflow/toggle", methods=["POST"])
def api_workflow_toggle():
    body = request.get_json(force=True, silent=True) or {}
    wf = duckdb_store.load_workflow()
    wf["active"] = bool(body.get("active"))
    duckdb_store.save_workflow(wf)
    return jsonify({"code": 0, "data": wf,
                    "msg": "工作流已{}".format("启用（新导出的表将自动执行）" if wf["active"] else "停用")})


@app.route("/api/workflow/apply", methods=["POST"])
def api_workflow_apply():
    """对指定表立即应用当前工作流（编辑过的内存版本）"""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name", "")
    if duckdb_store.get_table(name) is None:
        return jsonify({"code": 1, "msg": "表不存在"})
    wf = {"active": False, "name": body.get("name", ""),
          "steps": body.get("steps") or []}
    if not wf["steps"]:
        return jsonify({"code": 1, "msg": "工作流没有步骤"})
    results = duckdb_store.apply_workflow(name, wf)
    ok = sum(1 for r in results if r["ok"])
    return jsonify({"code": 0, "results": results,
                    "msg": "工作流执行完成：{}/{} 步成功".format(ok, len(results))})


@app.route("/api/workflow/download")
def api_workflow_download():
    """下载工作流 JSON（分享给他人：发这个文件即可看懂意图）"""
    wf = duckdb_store.load_workflow()
    import urllib.parse
    fname = "工作流_{}.json".format(time.strftime("%Y%m%d_%H%M%S"))
    quoted = urllib.parse.quote(fname)
    data = json.dumps(wf, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(data, mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=workflow.json; filename*=UTF-8''{quoted}"})


@app.route("/api/workflow/upload", methods=["POST"])
def api_workflow_upload():
    """导入工作流 JSON（raw body 或 multipart file）"""
    try:
        if request.files:
            f = request.files.get("file")
            raw = f.read().decode("utf-8", "ignore")
        else:
            raw = request.get_data(as_text=True)
        data = json.loads(raw)
        if not isinstance(data, dict) or "steps" not in data:
            return jsonify({"code": 1, "msg": "不是有效的工作流 JSON（缺少 steps）"})
        wf = duckdb_store.save_workflow(data)
        return jsonify({"code": 0, "data": wf,
                        "msg": "已导入工作流（{} 步）".format(len(wf["steps"]))})
    except Exception as e:
        return jsonify({"code": 1, "msg": "导入失败: {}".format(e)})


@app.route("/api/db/export")
def api_db_export():
    """导出整张表为 xlsx 下载"""
    name = request.args.get("name", "")
    data = duckdb_store.export_table_xlsx(name)
    if data is None:
        return jsonify({"code": 1, "msg": "表不存在"}), 404
    import urllib.parse
    fname = "靓号查询表_{}_{}.xlsx".format(
        name.replace("/", "_").replace("\\", "_")[:40],
        time.strftime("%Y%m%d_%H%M%S"))
    quoted = urllib.parse.quote(fname)
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=export.xlsx; filename*=UTF-8''{quoted}"})


# ---------- 随机二次元背景 ----------

_bg = {"img": None, "ts": 0}
_bg_lock = threading.Lock()


def _bg_load():
    # 短超时：图片源慢/挂时最多等 8s 放弃；失败用程序化壁纸兜底（时间 seed），
    # 保证「换背景」永远有响应（ts 必变），不被外部图源拖死
    img = anime_bg.fetch_random_bg(timeout=6, total_budget=8)
    if img is None:
        img = anime_bg.generate_anime_bg(seed=int(time.time()) % 100000)
    with _bg_lock:
        _bg["img"] = img
        _bg["ts"] = int(time.time() * 1000)


@app.route("/api/bg")
def api_bg():
    with _bg_lock:
        img = _bg["img"]
    if img is None:
        img = anime_bg.generate_anime_bg()
    bio = io.BytesIO()
    img.convert("RGB").save(bio, "JPEG", quality=80)
    bio.seek(0)
    return Response(bio.getvalue(), mimetype="image/jpeg",
                    headers={"Cache-Control": "no-cache"})


@app.route("/api/bg/refresh")
def api_bg_refresh():
    """同步拉一张新随机图（多源 fallback，一般 0.5-2s），返回时间戳供前端换图"""
    _bg_load()
    with _bg_lock:
        ts = _bg["ts"]
    return jsonify({"code": 0, "ts": ts})


@app.route("/api/bg/ts")
def api_bg_ts():
    """当前背景时间戳：前端轮询，API 图拉取完成后（ts 变化）自动替换程序化背景"""
    with _bg_lock:
        return jsonify({"code": 0, "ts": _bg["ts"], "ready": _bg["img"] is not None})


@app.route("/api/packages")
def api_packages():
    """号码可办套餐列表（qryProductList：productName/productCode/productFee/serviceDesc）"""
    msisdn = request.args.get("msisdn", "")
    if not msisdn:
        return jsonify({"code": 1, "msg": "缺少 msisdn 参数", "data": []})
    try:
        packages = engine.fetcher.get_products_for_number(msisdn)
        if packages is None:
            return jsonify({"code": 1, "msg": "套餐查询失败（接口异常）", "data": []})
        return jsonify({"code": 0, "data": packages})
    except Exception as e:
        return jsonify({"code": 1, "msg": f"套餐查询异常: {e}", "data": []})


@app.route("/")
def index():
    return render_index()


# ---------- 前端页面（内嵌 HTML/CSS/JS，毛玻璃 + 二次元背景） ----------

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>🌸 民生靓号查询</title>
<style>
  :root{
    --pink:#ff8fc4; --purple:#8e5ad8; --violet:#b388ff;
    --ink:#efe7ff; --dim:#c9b8ef; --faint:#8f7fb8;
    --alpha-glass:.50;   /* 毛玻璃透明度（滑块可调，0.10-0.90） */
    --glass:rgba(26,18,48,var(--alpha-glass));
    --glass-strong:rgba(22,14,42,min(calc(var(--alpha-glass) + .17), .95));
    --card:rgba(38,28,72,var(--alpha-glass)); --row:rgba(56,42,102,.45);
    --line:rgba(255,255,255,.14);
    --rad:16px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%}
  body{
    font-family:"Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;
    color:var(--ink); overflow:hidden; user-select:none;
  }
  /* 背景图 + 渐变遮罩 */
  .bg{position:fixed;inset:0;object-fit:cover;width:100%;height:100%;z-index:-2;
      transition:opacity .6s ease}
  .bg.fade{opacity:0}
  .veil{position:fixed;inset:0;z-index:-1;background:
      linear-gradient(180deg,rgba(16,10,34,calc(var(--alpha-glass) * .55)),
                      rgba(16,10,34,calc(var(--alpha-glass) * 1.05)) 40%,
                      rgba(12,8,28,min(calc(var(--alpha-glass) * 1.5), .95)))}
  /* 透明度滑块 */
  .op-ctl{display:flex;align-items:center;gap:7px}
  .op-ctl span{font-size:12px;color:var(--dim)}
  .op-ctl input[type=range]{width:96px;accent-color:var(--purple);cursor:pointer}
  .op-ctl .op-val{width:38px;text-align:right;font-variant-numeric:tabular-nums}
  /* 行内小按钮 */
  .mini{background:linear-gradient(135deg,var(--pink),var(--purple));border:none;color:#fff;
        border-radius:7px;padding:3px 12px;font-size:12px;cursor:pointer}
  .mini:hover{filter:brightness(1.12)}
  /* 下单二维码弹窗 */
  .modal-mask{position:fixed;inset:0;background:rgba(10,6,24,.55);z-index:50;
              display:flex;align-items:center;justify-content:center;
              backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)}
  .modal{width:340px;padding:18px;display:flex;flex-direction:column;align-items:center;gap:10px}
  .modal-head{width:100%;display:flex;justify-content:space-between;align-items:center;font-weight:600}
  .modal img{width:240px;height:240px;background:#fff;border-radius:10px;padding:6px}
  .qr-hint{font-size:11px;color:var(--faint);text-align:center}
  .modal-close{background:none;border:none;color:var(--dim);font-size:16px;cursor:pointer;padding:2px 6px}
  .modal-close:hover{color:#fff}
  /* 类型选择分页网格（20/页） */
  .rank-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;width:100%;
             max-height:300px;overflow-y:auto;margin-top:12px}
  .rank-grid button{padding:8px 4px;border-radius:8px;border:1px solid var(--line);
                    background:rgba(90,70,150,.25);color:var(--ink);cursor:pointer;font-size:13px}
  .rank-grid button:hover{border-color:var(--pink);color:#fff}
  .rank-grid button.sel{border-color:var(--pink);background:rgba(255,143,196,.15);color:var(--pink)}
  .rank-page{display:flex;justify-content:space-between;align-items:center;width:100%;margin-top:10px}
  /* 导出进度条 */
  .exp-bar{width:100%;height:10px;border-radius:5px;background:rgba(90,70,150,.35);
           overflow:hidden;margin:14px 0 4px}
  .exp-bar div{height:100%;width:0%;border-radius:5px;
               background:linear-gradient(90deg,var(--pink),var(--violet));transition:width .3s}
  /* 结果分页条（表格下方） */
  #pagerBar{display:flex;align-items:center;gap:8px;padding:8px 14px;flex:0 0 auto;
            border-top:1px solid rgba(255,255,255,.07);flex-wrap:wrap}
  .page-num{min-width:30px;padding:4px 8px;border-radius:7px;border:1px solid var(--line);
            background:rgba(90,70,150,.25);color:var(--ink);cursor:pointer;font-size:12.5px}
  .page-num:hover{border-color:var(--pink);color:#fff}
  .page-num.on{background:linear-gradient(135deg,var(--pink),var(--purple));color:#fff;
               border-color:transparent;font-weight:600}
  .page-gap{color:var(--faint);padding:0 3px;font-size:12.5px}
  /* 套餐选择弹窗 */
  .pkg-top{width:100%;display:flex;justify-content:space-between;align-items:center;font-size:13px}
  .pkg-pre{color:var(--pink);font-weight:700;font-size:13px}
  .pkg-card{width:100%;border:1px solid var(--line);border-radius:12px;padding:12px;
            background:var(--card);text-align:left}
  .pkg-name{font-size:15px;font-weight:700;color:var(--ink)}
  .pkg-fee{font-size:20px;font-weight:800;color:var(--pink);margin:4px 0}
  .pkg-quota{font-size:12px;color:var(--dim);margin-bottom:6px}
  .pkg-desc{font-size:12px;color:var(--faint);line-height:1.6;max-height:110px;overflow:auto;
            white-space:pre-wrap;word-break:break-all}
  .pkg-list{width:100%;display:flex;flex-direction:column;gap:8px;max-height:280px;overflow:auto}
  .pkg-item{border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer;
            background:var(--card);text-align:left}
  .pkg-item:hover{border-color:var(--purple)}
  .pkg-item.sel{border-color:var(--pink);background:rgba(255,143,196,.12)}
  .pkg-item .pkg-name{font-size:13px}
  .pkg-item .pkg-fee{font-size:15px;margin:2px 0}
  .cfm-rows{width:100%;display:flex;flex-direction:column;gap:8px;font-size:13px}
  .cfm-row{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line);
           padding-bottom:6px}
  .cfm-row b{color:var(--ink)}
  .disclaimer{margin-top:auto;text-align:center;font-size:12px;color:var(--faint);
              padding:8px 0 2px;line-height:1.7}
  .disclaimer a{color:var(--pink);text-decoration:none}
  .disclaimer a:hover{text-decoration:underline}
  .wrap{height:100vh;display:flex;flex-direction:column;gap:12px;padding:14px 18px;
        max-width:1280px;margin:0 auto}
  /* 毛玻璃条 */
  .glass{background:var(--glass);backdrop-filter:blur(22px) saturate(150%);
         -webkit-backdrop-filter:blur(22px) saturate(150%);
         border:1px solid var(--line);border-radius:var(--rad);box-shadow:0 8px 32px rgba(0,0,0,.35)}
  /* 顶栏 */
  header{display:flex;align-items:center;gap:14px;padding:10px 18px;flex:0 0 auto}
  header h1{font-size:17px;font-weight:700;letter-spacing:.5px}
  header h1 em{font-style:normal;background:linear-gradient(90deg,var(--pink),var(--violet));
               -webkit-background-clip:text;background-clip:text;color:transparent}
  #status{font-size:12px;color:var(--dim);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pill{background:linear-gradient(135deg,var(--pink),var(--purple));border:none;color:#fff;
        border-radius:999px;padding:7px 18px;font-size:13px;cursor:pointer;
        box-shadow:0 4px 14px rgba(142,90,216,.45);transition:transform .15s,filter .15s}
  .pill:hover{transform:translateY(-1px);filter:brightness(1.12)}
  .pill:active{transform:translateY(0)}
  .pill.ghost{background:rgba(90,70,150,.45);box-shadow:none;border:1px solid var(--line)}
  .pill:disabled{opacity:.45;cursor:not-allowed;transform:none;filter:none}
  /* 页签 */
  nav{display:flex;gap:8px;padding:8px 14px;flex:0 0 auto}
  .tab{background:rgba(60,45,110,.4);color:var(--dim);border:none;border-radius:11px;
       padding:8px 26px;font-size:13.5px;cursor:pointer;transition:all .18s}
  .tab.on{background:linear-gradient(135deg,var(--pink),var(--purple));color:#fff;
          box-shadow:0 4px 14px rgba(142,90,216,.5)}
  .tab:hover{color:#fff}
  /* 主区域 */
  main{flex:1;display:flex;flex-direction:column;gap:12px;min-height:0}
  .panel{display:none;flex-direction:column;min-height:0;flex:1}
  .panel.on{display:flex}
  /* 表单行 */
  .bar{display:flex;align-items:center;gap:10px;padding:10px 14px;flex-wrap:wrap;flex:0 0 auto}
  .bar label{font-size:12.5px;color:var(--dim);white-space:nowrap}
  .bar select,.bar input{
    background:rgba(20,13,42,.75);color:var(--ink);border:1px solid var(--line);
    border-radius:9px;padding:6px 10px;font-size:13px;outline:none;max-width:180px}
  .bar select:focus,.bar input:focus{border-color:var(--purple)}
  .hint{font-size:11.5px;color:var(--faint);margin-left:auto}
  /* 表格 */
  .tbl{flex:1;overflow:auto;margin:0 0 0 0;padding:0 4px;min-height:0;user-select:text}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  thead th{position:sticky;top:0;background:var(--glass-strong);color:var(--pink);
           font-weight:600;padding:8px 10px;text-align:left;white-space:nowrap;z-index:1}
  tbody td{padding:7px 10px;border-top:1px solid rgba(255,255,255,.07);white-space:nowrap}
  tbody tr:nth-child(even){background:var(--row)}
  tbody tr:hover{background:rgba(142,90,216,.28)}
  a{color:var(--pink);text-decoration:none}
  a:hover{text-decoration:underline}
  .empty{padding:26px;text-align:center;color:var(--faint);font-size:13px}
  /* 详情 / 日志 */
  .detail{padding:10px 14px;flex:0 0 auto;font-size:12px;line-height:1.7;color:var(--dim);
          white-space:pre-wrap;max-height:130px;overflow:auto;display:none}
  .detail.on{display:block}
  #log{flex:0 0 116px;padding:8px 14px;overflow-y:auto;font-family:Consolas,monospace;
       font-size:11.5px;line-height:1.65;user-select:text}
  #log div{padding:1px 0}
  #log .ok{color:#7ce7b0} #log .error{color:#ff8fa3} #log .info{color:var(--dim)}
  #log .t{color:var(--faint);margin-right:6px}
  ::-webkit-scrollbar{width:9px;height:9px}
  ::-webkit-scrollbar-thumb{background:rgba(142,90,216,.55);border-radius:5px}
  ::-webkit-scrollbar-track{background:rgba(0,0,0,.2)}
  .spin{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);
        border-top-color:#fff;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px}
  @keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<img id="bg" class="bg" alt="" src="/api/bg">
<div class="veil"></div>

<div class="wrap">
  <header class="glass">
    <h1><em>🌸 民生靓号查询</em></h1>
    <span id="status">就绪</span>
    <div class="op-ctl" title="调整毛玻璃透明度">
      <span>🌫</span>
      <input type="range" id="opSlider" min="0.10" max="0.90" step="0.01" value="0.50">
      <span class="op-val" id="opVal">50%</span>
    </div>
    <button class="pill ghost" id="btnBg">🎨 换背景</button>
  </header>

  <nav class="glass">
    <button class="tab on" data-tab="liang">🔢 靓号查询</button>
    <button class="tab" data-tab="flow">📦 流量专区</button>
    <button class="tab" data-tab="order">🧾 订单查询</button>
  </nav>

  <main>
    <!-- ============ 靓号查询 ============ -->
    <section class="panel on" id="p-liang">
      <div class="bar glass">
        <label>系列</label><select id="series">
          <option value="h5">靓号专区 (h5)</option><option value="liu">流量卡专区 (liu)</option>
        </select>
        <label>省份</label><select id="province"><option value="">— 加载省份 —</option></select>
        <label>城市</label><select id="city"><option value="">全部</option></select>
        <label>类型</label><button type="button" class="pill ghost" id="btnRank" style="padding:6px 14px">不限</button>
        <label>号码搜索</label><input id="msisdn" type="text" maxlength="11" placeholder="如：4 或 138" title="号码关键词，可单独或与地区/等级组合" style="width:92px">
        <label>页数</label><input id="maxPages" type="number" value="50" min="1" max="500" style="width:64px">
        <label>间隔s</label><input id="interval" type="number" value="2" min="1" max="60" style="width:52px">
        <span class="hint" title="间隔过小会对服务器造成压力，可能被拉入 IP 黑名单">⚠ 建议 ≥2s（最小 1s）</span>
        <span class="hint">套餐信息并发查询 · 右键复制号码</span>
      </div>
      <div class="bar glass" style="padding-top:8px;padding-bottom:8px">
        <button class="pill" id="btnProv">加载省份</button>
        <button class="pill" id="btnStart">▶ 开始查询</button>
        <button class="pill ghost" id="btnStop" disabled>■ 停止</button>
        <button class="pill ghost" id="btnClear">清空</button>
        <button class="pill ghost" id="btnExport">⬇ 导出</button>
        <select id="expFmt" title="导出格式：DuckDB 入库（推荐，可到「数据库管理」查看）/ Excel / CSV">
          <option value="duckdb">→ DuckDB 入库</option>
          <option value="xlsx">Excel (xlsx)</option>
          <option value="csv">CSV</option>
        </select>
        <label class="hint" style="cursor:pointer"><input type="checkbox" id="expMore"> 附带更多套餐(套餐2/3)</label>
        <span class="hint" id="counter"></span>
      </div>
      <div class="tbl glass">
        <table>
          <thead><tr><th style="width:44px">序号</th><th>手机号</th><th>省份</th><th>城市</th><th>等级</th>
            <th>预存(元)</th><th>月低消(元)</th><th>套餐</th><th style="width:70px">操作</th></tr></thead>
          <tbody id="lhBody"></tbody>
        </table>
        <div class="empty" id="lhEmpty">点击「开始查询」获取号码；页面支持右键复制。</div>
        <div id="pagerBar" style="display:none">
          <button class="pill ghost" id="pageFirst" style="padding:4px 14px">« 首页</button>
          <button class="pill ghost" id="pagePrev" style="padding:4px 14px">‹ 上一页</button>
          <span id="pageNums"></span>
          <button class="pill ghost" id="pageNext" style="padding:4px 14px">下一页 ›</button>
          <button class="pill ghost" id="pageLast" style="padding:4px 14px">末页 »</button>
          <span class="hint" style="margin-left:0" id="pageInfo">共 0 条（每页 20）</span>
          <span class="hint" style="margin-left:auto">导出包含全部数据</span>
        </div>
      </div>
    </section>

    <!-- ============ 流量专区 ============ -->
    <section class="panel" id="p-flow">
      <div class="bar glass">
        <button class="pill" id="btnCats">加载分类</button>
        <label>分类</label><select id="category"><option value="">— 加载分类 —</option></select>
        <button class="pill ghost" id="btnProds">加载产品</button>
        <button class="pill ghost" id="btnKd">🚚 物流方式</button>
        <span class="hint">双击产品行查看详情</span>
      </div>
      <div class="tbl glass">
        <table>
          <thead><tr><th>产品ID</th><th>名称</th><th>价格(分)</th><th>计费单位</th><th>分类</th><th>库存</th><th>说明</th></tr></thead>
          <tbody id="flowBody"></tbody>
        </table>
        <div class="empty" id="flowEmpty">先加载分类，再选择分类加载产品。</div>
      </div>
      <div class="detail glass" id="flowDetail"></div>
    </section>

    <!-- ============ 订单查询 ============ -->
    <section class="panel" id="p-order">
      <div class="bar glass">
        <label>证件号</label><input id="cardno" placeholder="下单时填写的证件号" style="width:220px">
        <label>手机号</label><input id="ophone" placeholder="可选" style="width:150px">
        <button class="pill" id="btnOrder">查询订单</button>
        <span class="hint">证件号必须非空（接口要求）</span>
      </div>
      <div class="tbl glass">
        <table>
          <thead><tr><th>订单号</th><th>手机号</th><th>状态</th><th>产品</th><th>金额(分)</th><th>时间</th><th>收货地址</th></tr></thead>
          <tbody id="orderBody"></tbody>
        </table>
        <div class="empty" id="orderEmpty">填写证件号后点击「查询订单」。</div>
      </div>
    </section>
  </main>

  <div class="glass" id="log"></div>
  <div class="disclaimer">⚠ 软件仅供学习测试使用，请勿商用！请合理控制查询频率，尊重目标服务。<br>
    作者：<a href="https://github.com/xiazhimiao" target="_blank">xiazhimiao</a> ｜
    项目源码：<a href="https://github.com/xiazhimiao/ms170-reverse-lab" target="_blank">ms170-reverse-lab</a></div>
</div>

<!-- 类型选择弹窗（分页列表 20/页，替代下拉框） -->
<div class="modal-mask" id="rankMask" style="display:none">
  <div class="modal glass" style="max-width:560px">
    <div class="modal-head"><span>🎯 选择号码类型</span><button class="modal-close" id="rankClose">✕</button></div>
    <div id="rankGrid" class="rank-grid"></div>
    <div class="rank-page">
      <button class="pill ghost" id="rankPrev" style="padding:5px 14px">← 上一页</button>
      <span class="hint" id="rankPageNo" style="margin-left:0"></span>
      <button class="pill ghost" id="rankNext" style="padding:5px 14px">下一页 →</button>
    </div>
    <button class="pill ghost" id="rankNone" style="margin-top:12px">不限（全部类型）</button>
  </div>
</div>

<!-- 套餐选择弹窗（复刻小程序：号码 → 预存 → 默认套餐 → 立即办理/更多套餐 → 下一步） -->
<div class="modal-mask" id="pkgMask" style="display:none">
  <div class="modal glass" style="max-width:560px">
    <div class="modal-head"><span>📶 套餐选择</span><button id="pkgClose">✕</button></div>
    <div class="pkg-top">已选择号码：<b id="pkgPhone"></b><span id="pkgPre" class="pkg-pre"></span></div>
    <div id="pkgDefault" class="pkg-card">
      <div class="pkg-name" id="pkgName"></div>
      <div class="pkg-fee" id="pkgFee"></div>
      <div class="pkg-quota" id="pkgQuota"></div>
      <div class="pkg-desc" id="pkgDesc"></div>
      <button class="pill" id="pkgGoNow" style="margin-top:10px">立即办理</button>
    </div>
    <button class="pill ghost" id="pkgMore" style="margin-top:12px">＋ 更多套餐选择</button>
    <div id="pkgList" class="pkg-list" style="display:none"></div>
    <button class="pill" id="pkgNext" style="display:none;margin-top:12px">下一步</button>
    <p class="qr-hint">"更多套餐选择" 加载该号码可办套餐列表；选中后点「下一步」进入订单确认</p>
  </div>
</div>

<!-- 订单确认弹窗 -->
<div class="modal-mask" id="cfmMask" style="display:none">
  <div class="modal glass" style="max-width:520px">
    <div class="modal-head"><span>🧾 订单确认</span><button id="cfmClose">✕</button></div>
    <div class="cfm-rows" id="cfmRows"></div>
    <div class="pkg-desc" id="cfmDesc"></div>
    <button class="pill" id="cfmGo" style="margin-top:14px">确认订阅</button>
    <p class="qr-hint">下单与支付需在「民生靓号」微信小程序内完成（本工具为查号演示，此按钮仅展示确认流程）</p>
  </div>
</div>

<!-- 导出进度弹窗（勾选「附带更多套餐」时异步导出，拉取套餐耗时） -->
<div class="modal-mask" id="expMask" style="display:none">
  <div class="modal glass" style="max-width:420px">
    <div class="modal-head"><span>📤 导出中</span></div>
    <div class="exp-bar"><div id="expBar"></div></div>
    <p class="qr-hint" id="expText">正在准备导出...</p>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let lhRows = [];
let busy = false;

/* ---------- 透明度滑块（记忆上次设置） ---------- */
const opSlider = $('opSlider'), opVal = $('opVal');
function applyAlpha(v){
  document.documentElement.style.setProperty('--alpha-glass', v);
  opVal.textContent = Math.round(v * 100) + '%';
  try { localStorage.setItem('ms_alpha', v); } catch(e) {}
}
opSlider.oninput = () => applyAlpha(parseFloat(opSlider.value));
try {
  const saved = parseFloat(localStorage.getItem('ms_alpha'));
  if (saved >= 0.1 && saved <= 0.9) { opSlider.value = saved; }
} catch(e) {}
applyAlpha(parseFloat(opSlider.value));

/* ---------- 背景 ---------- */
const bg = $('bg');
function setBg(src){
  bg.classList.add('fade');
  bg.onload = () => bg.classList.remove('fade');
  bg.onerror = () => { bg.src = '/api/bg'; bg.classList.remove('fade'); };
  bg.src = src;
}
$('btnBg').onclick = async () => {
  $('btnBg').disabled = true;
  try {
    await fetch('/api/bg/refresh');
    setBg('/api/bg?' + Date.now());
  } catch(e) {}
  setTimeout(() => $('btnBg').disabled = false, 20000);
};
// 自动替换：API 随机图拉取完成（ts 变化）后，用 API 图替换默认程序化背景
let bgTs = 0;
async function pollBg(){
  try {
    const r = await (await fetch('/api/bg/ts')).json();
    if (r.ts && r.ts !== bgTs) { bgTs = r.ts; setBg('/api/bg?' + r.ts); }
  } catch(e) {}
}
setInterval(pollBg, 1500);

/* ---------- 日志 ---------- */
function log(level, msg, noTime) {
  const box = $('log');
  const d = document.createElement('div');
  d.className = level;
  if (!noTime) {
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = new Date().toLocaleTimeString('zh-CN', {hour12:false});
    d.appendChild(t);
  }
  d.appendChild(document.createTextNode(msg));
  box.appendChild(d);
  while (box.children.length > 500) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}
function setStatus(t){ $('status').textContent = t; }
function setRunning(run){
  $('btnStart').disabled = run;
  $('btnStop').disabled = !run;
  $('btnProv').disabled = run;
}

/* ---------- SSE ---------- */
function connectSSE(){
  const es = new EventSource('/api/events');
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'log') log(ev.level, ev.msg);
    else if (ev.type === 'page') { addLhRows(ev.rows); setStatus('查询中 · 累计 ' + ev.total + ' 条'); }
    else if (ev.type === 'done') { setRunning(false); setStatus(ev.reason + ' · 共 ' + ev.total + ' 条'); }
  };
  es.onerror = () => {}; // EventSource 自动重连
}

/* ---------- 靓号查询（结果分页：每页 20 条，翻页浏览全部数据） ---------- */
const PAGE_SIZE = 20;
let lhPage = 1;        // 当前显示页
let lhFollow = true;   // 跟随最新页（手动翻页离开末页后停止，回到末页恢复）

function appendLhRow(r){
  const tb = $('lhBody');
  const tr = document.createElement('tr');
  tr.dataset.phone = r.phone_number;
  tr.dataset.row = JSON.stringify(r);
  tr.onclick = () => openPackageChoose(r);
  const yuan = v => (v || 0) / 100;
  [r.index, r.phone_number, r.province, r.city, r.rank,
   yuan(r.bossPrestore), yuan(r.minConsume), r.productName || '—'].forEach(v => {
    const td = document.createElement('td'); td.textContent = v; tr.appendChild(td);
  });
  const td = document.createElement('td');
  const btn = document.createElement('button');
  btn.className = 'mini';
  btn.textContent = '办理';
  btn.onclick = e => { e.stopPropagation(); openPackageChoose(r); };
  td.appendChild(btn); tr.appendChild(td);
  tb.appendChild(tr);
}

function totalLhPages(){ return Math.max(1, Math.ceil(lhRows.length / PAGE_SIZE)); }

// 页码窗口：1 … (当前-2) (当前-1) 当前 (当前+1) (当前+2) … 末页；页数少时全部显示
function pageNums(cur, total){
  if (total <= 7) return Array.from({length: total}, (_, i) => ({n: i + 1}));
  const set = new Set([1, total]);
  for (let i = cur - 2; i <= cur + 2; i++) { if (i >= 1 && i <= total) set.add(i); }
  const sorted = [...set].sort((a, b) => a - b);
  const out = [];
  let prev = 0;
  for (const n of sorted) {
    if (n - prev > 1) out.push({ell: true});   // 窗口与边界之间有缺口 → 省略号
    out.push({n});
    prev = n;
  }
  return out;
}

function renderLhPage(){
  if (lhPage > totalLhPages()) lhPage = totalLhPages();
  const rows = lhRows.slice((lhPage - 1) * PAGE_SIZE, lhPage * PAGE_SIZE);
  $('lhBody').innerHTML = '';
  $('lhEmpty').style.display = rows.length ? 'none' : '';
  rows.forEach(appendLhRow);
  const total = totalLhPages();
  $('pageInfo').textContent = '共 ' + lhRows.length + ' 条（每页 ' + PAGE_SIZE + '）';
  $('pageFirst').disabled = lhPage <= 1;
  $('pagePrev').disabled = lhPage <= 1;
  $('pageNext').disabled = lhPage >= total;
  $('pageLast').disabled = lhPage >= total;
  $('pageNums').innerHTML = pageNums(lhPage, total).map(x => x.ell
    ? '<span class="page-gap">…</span>'
    : `<button class="page-num${x.n === lhPage ? ' on' : ''}" data-n="${x.n}">${x.n}</button>`).join('');
  Array.from($('pageNums').children).forEach(b => b.onclick = () => {
    lhPage = parseInt(b.dataset.n, 10);
    lhFollow = lhPage >= totalLhPages();
    renderLhPage();
  });
  $('pagerBar').style.display = lhRows.length ? 'flex' : 'none';
}
$('pageFirst').onclick = () => { lhPage = 1; lhFollow = false; renderLhPage(); };
$('pagePrev').onclick = () => { lhPage--; lhFollow = lhPage >= totalLhPages(); renderLhPage(); };
$('pageNext').onclick = () => { lhPage++; lhFollow = lhPage >= totalLhPages(); renderLhPage(); };
$('pageLast').onclick = () => { lhPage = totalLhPages(); lhFollow = true; renderLhPage(); };

function addLhRows(rows){
  lhRows = lhRows.concat(rows);
  $('counter').textContent = '已获取 ' + lhRows.length + ' 条';
  // 跟随最新：未手动翻页（或已在末页）时，新数据到达自动翻到最新页
  if (lhFollow) lhPage = totalLhPages();
  renderLhPage();
}
// 右键复制号码
$('lhBody').addEventListener('contextmenu', e => {
  const tr = e.target.closest('tr'); if (!tr) return;
  e.preventDefault();
  navigator.clipboard.writeText(tr.dataset.phone || '');
  log('ok', '已复制 ' + tr.dataset.phone);
});

async function loadProvinces(){
  $('btnProv').disabled = true;
  try {
    const r = await (await fetch('/api/provinces')).json();
    if (r.code) throw new Error(r.msg);
    const sel = $('province');
    sel.innerHTML = '<option value="">全部</option>' +
      r.data.map(p => `<option value="${p.code}">${p.name}</option>`).join('');
    log('ok', '省份加载完成，共 ' + r.data.length + ' 个');
    setStatus('就绪');
  } catch(err) { log('error', '省份加载失败: ' + err.message); }
  $('btnProv').disabled = false;
}
$('btnProv').onclick = loadProvinces;
$('province').onchange = async () => {
  const code = $('province').value;
  $('city').innerHTML = '<option value="">全部</option>';
  if (!code) return;
  try {
    const r = await (await fetch('/api/cities/' + code)).json();
    if (r.code) throw new Error(r.msg);
    $('city').innerHTML = '<option value="">全部</option>' +
      r.data.map(c => `<option value="${c.code}">${c.name}</option>`).join('');
  } catch(err) { log('error', '城市加载失败: ' + err.message); }
};

async function startQuery(){
  const params = {
    series: $('series').value,
    province: $('province').value,
    city: $('city').value,
    rank: rankSel,
    msisdn: $('msisdn').value.trim(),
    max_pages: parseInt($('maxPages').value || '0', 10),
    interval: parseFloat($('interval').value || '2')
  };
  const r = await (await fetch('/api/query/start', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(params)
  })).json();
  if (r.code) { log('error', r.msg); return; }
  setRunning(true); setStatus('查询启动...');
  const conds = [params.series, rankSel ? '类型 ' + rankSel : '',
                 params.msisdn ? '号码含 ' + params.msisdn : ''].filter(Boolean).join(' · ');
  log('info', '开始查询: ' + conds);
}
$('btnStart').onclick = startQuery;
$('btnStop').onclick = async () => { await fetch('/api/query/stop', {method:'POST'}); };
$('btnClear').onclick = () => {
  lhRows = [];
  lhPage = 1;
  lhFollow = true;
  renderLhPage();
  $('counter').textContent = '';
  log('info', '已清空');
};

function buildExportRows(){
  return lhRows.map(r => [r.index, r.phone_number, r.province, r.city, r.rank,
    yuan(r.bossPrestore), yuan(r.minConsume), r.productName || '',
    yuan(r.productFee), r.liuTotal || '', r.callTotal || '', r.package || '']);
}

// 保存导出文件（复用下载响应 → 解析后端文件名 → 触发下载）
async function saveBlob(r, fmt){
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  // 优先用后端文件名（含省_市_时间戳），解析 Content-Disposition filename*
  let fname = '靓号查询结果.' + (fmt === 'csv' ? 'csv' : 'xlsx');
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)/);
  if (m) { try { fname = decodeURIComponent(m[1]); } catch(e) {} }
  a.download = fname;
  a.click();
  URL.revokeObjectURL(a.href);
  return fname;
}

async function exportRows(){
  if (!lhRows.length) { log('error', '没有可导出的数据'); return; }
  const fmt = $('expFmt').value;   // duckdb / xlsx / csv
  const more = $('expMore').checked;
  const provSel = $('province').selectedOptions[0];
  const citySel = $('city').selectedOptions[0];
  const fnameInfo = (provSel ? provSel.textContent : '全国') + '_' + (citySel ? citySel.textContent : '全部');
  const body = {rows: buildExportRows(), fmt, more, fname_info: fnameInfo};
  const dbHint = t => '已写入 duckdb：表 ' + t + '（可在 数据库管理 查看）';
  if (more) {
    // 附带更多套餐：每个号码都要并发拉套餐，耗时较长 → 后台任务 + 进度条轮询
    log('info', '正在拉取更多套餐（' + lhRows.length + ' 个号码）...');
    const r = await fetch('/api/export', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (!r.ok) { log('error', '导出失败'); return; }
    const j = await r.json();
    if (j.code || !j.task) { log('error', j.msg || '导出失败'); return; }
    $('expMask').style.display = 'flex';
    const task = j.task;
    try {
      while (true) {
        await new Promise(s => setTimeout(s, 500));
        const s = await (await fetch('/api/export/status?task=' + task)).json();
        if (s.code) { log('error', s.msg); break; }
        const pct = s.total ? Math.round(s.done / s.total * 100) : 0;
        $('expBar').style.width = pct + '%';
        $('expText').textContent = '正在拉取更多套餐 ' + s.done + '/' + s.total + '（' + pct + '%）';
        if (s.status === 'done') {
          if (fmt === 'duckdb') {
            log('ok', dbHint(s.table));
          } else {
            $('expText').textContent = '文件生成中...';
            const dl = await fetch('/api/export/download?task=' + task);
            if (!dl.ok) { log('error', '导出文件下载失败'); break; }
            const fname = await saveBlob(dl, fmt);
            log('ok', '已导出 ' + lhRows.length + ' 条 → ' + fname);
          }
          break;
        }
        if (s.status === 'error') { log('error', '导出失败: ' + (s.error || '未知错误')); break; }
      }
    } finally { $('expMask').style.display = 'none'; }
    return;
  }
  // 未勾选更多套餐：列表已加载，快
  const r = await fetch('/api/export', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
  if (!r.ok) { log('error', '导出失败'); return; }
  if (fmt === 'duckdb') {
    const j = await r.json();
    if (j.code) { log('error', j.msg || '导出失败'); return; }
    log('ok', dbHint(j.table));
    return;
  }
  const fname = await saveBlob(r, fmt);
  log('ok', '已导出 ' + lhRows.length + ' 条 → ' + fname);
}
$('btnExport').onclick = () => exportRows();

/* ---------- 套餐选择 + 订单确认（复刻小程序 ActivityOptimization 流程） ---------- */
let pkgRow = null;      // 当前号码行（含默认套餐信息）
let pkgSelIdx = -1;     // 更多套餐列表选中项
let pkgSelList = [];    // 更多套餐列表

function yuan(v){ return (v || 0) / 100; }

function closeMasks(){ $('pkgMask').style.display = 'none'; $('cfmMask').style.display = 'none'; }

function openPackageChoose(r){
  pkgRow = r; pkgSelIdx = -1; pkgSelList = [];
  $('pkgPhone').textContent = r.phone_number;
  $('pkgPre').textContent = '预存 ' + yuan(r.bossPrestore) + ' 元';
  // 默认套餐卡：先用行内字段，接口返回后刷新为第一个（接口自带的默认/最划算套餐）
  $('pkgName').textContent = r.productName || '加载中...';
  $('pkgFee').textContent = yuan(r.productFee) + ' 元/月';
  $('pkgQuota').textContent = '流量 ' + (r.liuTotal || 0) + ' G · 通话 ' + (r.callTotal || 0) + ' 分钟';
  $('pkgDesc').textContent = r.package || '';
  $('pkgList').style.display = 'none';
  $('pkgList').innerHTML = '';
  $('pkgNext').style.display = 'none';
  $('pkgMask').style.display = 'flex';
  loadPackageList();  // 点击号码直接列出所有套餐
  log('ok', '打开号码 ' + r.phone_number + ' 套餐选择');
}
$('pkgClose').onclick = closeMasks;
$('pkgMask').onclick = e => { if (e.target === $('pkgMask')) closeMasks(); };
$('cfmClose').onclick = closeMasks;
$('cfmMask').onclick = e => { if (e.target === $('cfmMask')) closeMasks(); };

// 加载 qryProductList 列表：第一个=接口默认（最划算），列表只显示第2个及以后（不重复）
async function loadPackageList(){
  try {
    const r = await (await fetch('/api/packages?msisdn=' + encodeURIComponent(pkgRow.phone_number))).json();
    if (r.code) throw new Error(r.msg);
    pkgSelList = r.data || [];
    if (!pkgSelList.length) { $('pkgList').style.display = 'none'; return; }
    // 默认套餐卡刷新为接口第一个（源码 PackageChooseCPMPage: packagesInfos[0] + sPackageIndex:0）
    const first = pkgSelList[0];
    $('pkgName').textContent = first.productName;
    $('pkgFee').textContent = yuan(first.productFee) + ' 元/月';
    $('pkgDesc').textContent = first.serviceDesc || '';
    // 列表：第2个及以后
    const rest = pkgSelList.slice(1);
    const list = $('pkgList');
    list.style.display = rest.length ? 'flex' : 'none';
    list.innerHTML = rest.map((p, i) => `
      <div class="pkg-item" id="pkgItem${i}">
        <div class="pkg-name">${p.productName}</div>
        <div class="pkg-fee">${yuan(p.productFee)} 元/月</div>
        <div class="pkg-desc">${(p.serviceDesc || '').slice(0, 120)}…</div>
      </div>`).join('');
    rest.forEach((_, i) => $('pkgItem' + i).onclick = () => {
      pkgSelIdx = i;
      document.querySelectorAll('.pkg-item').forEach(el => el.classList.remove('sel'));
      $('pkgItem' + i).classList.add('sel');
      $('pkgNext').style.display = '';
    });
    log('ok', '该号码可办套餐 ' + pkgSelList.length + ' 个（默认=第一个）');
  } catch(err) { log('error', '套餐列表加载失败: ' + err.message); }
}
$('pkgMore').onclick = loadPackageList;  // 保留按钮：重新加载列表（幂等）

// 立即办理：默认套餐（接口第一个，加载前用行内字段兜底）→ 订单确认
$('pkgGoNow').onclick = () => {
  if (!pkgRow) return;
  const p = pkgSelList.length ? pkgSelList[0] : pkgRow;
  showConfirm(pkgRow.phone_number, p.productName || '默认套餐',
    yuan(p.productFee) + ' 元/月', yuan(pkgRow.bossPrestore) + ' 元',
    '流量 ' + (pkgRow.liuTotal || 0) + ' G · 通话 ' + (pkgRow.callTotal || 0) + ' 分钟',
    p.serviceDesc || pkgRow.package || '');
};

// 下一步：选中的套餐 → 订单确认
$('pkgNext').onclick = () => {
  if (pkgSelIdx < 0) { log('error', '请先选择套餐'); return; }
  const p = pkgSelList[pkgSelIdx];
  showConfirm(pkgRow.phone_number, p.productName, yuan(p.productFee) + ' 元/月',
    yuan(pkgRow.bossPrestore) + ' 元', '', p.serviceDesc || '');
};

// 订单确认弹窗
function showConfirm(phone, pkgName, fee, pre, quota, desc){
  $('cfmRows').innerHTML = `
    <div class="cfm-row"><span>办理号码</span><b>${phone}</b></div>
    <div class="cfm-row"><span>预存</span><b>${pre}</b></div>
    <div class="cfm-row"><span>套餐</span><b>${pkgName}</b></div>
    <div class="cfm-row"><span>月费</span><b>${fee}</b></div>
    ${quota ? `<div class="cfm-row"><span>套餐额度</span><b>${quota}</b></div>` : ''}`;
  $('cfmDesc').textContent = desc || '';
  $('cfmMask').style.display = 'flex';
}
$('cfmGo').onclick = () => {
  log('ok', '确认订阅（演示）→ 实际下单请在「民生靓号」小程序内完成');
  closeMasks();
};

/* ---------- 流量专区 ---------- */
let categories = [];
async function loadCategories(){
  $('btnCats').disabled = true;
  try {
    const r = await (await fetch('/api/categories')).json();
    if (r.code) throw new Error(r.msg);
    categories = r.data;
    $('category').innerHTML = r.data.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    log('ok', '分类 ' + r.data.length + ' 个: ' + r.data.map(c => c.name).join(' / '));
    loadProducts();
  } catch(err) { log('error', '分类加载失败: ' + err.message); }
  $('btnCats').disabled = false;
}
$('btnCats').onclick = loadCategories;
async function loadProducts(){
  const cat = $('category').value;
  if (!cat) return;
  $('btnProds').disabled = true;
  try {
    const r = await (await fetch('/api/products?cat=' + encodeURIComponent(cat))).json();
    if (r.code) throw new Error(r.msg);
    const tb = $('flowBody');
    tb.innerHTML = '';
    $('flowEmpty').style.display = r.data.length ? 'none' : '';
    r.data.forEach(p => {
      const tr = document.createElement('tr');
      tr.dataset.id = p.id;
      [p.id, p.name, p.price, p.priceUnit, p.categoryName, p.num, p.limitDesc || p.desc || ''].forEach(v => {
        const td = document.createElement('td'); td.textContent = v || ''; tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    log('ok', '产品 ' + r.data.length + ' 个（双击查看详情）');
  } catch(err) { log('error', '产品加载失败: ' + err.message); }
  $('btnProds').disabled = false;
}
$('btnProds').onclick = loadProducts;
$('flowBody').addEventListener('dblclick', async e => {
  const tr = e.target.closest('tr'); if (!tr) return;
  const r = await (await fetch('/api/product_info?id=' + encodeURIComponent(tr.dataset.id))).json();
  const d = $('flowDetail');
  if (r.code || !r.data) { d.textContent = '详情为空'; d.classList.add('on'); return; }
  const i = r.data;
  d.textContent = `名称: ${i.name}\n价格: ${i.price} 分 / ${i.priceUnit}  分类: ${i.categoryName}\n编码: ${i.code}  流量: ${i.dataNum}  语音: ${i.voiceNum}  通用: ${i.gendataNum}\n简介: ${i.desc}\n服务: ${i.serviceDesc}`;
  d.classList.add('on');
});
$('btnKd').onclick = async () => {
  const r = await (await fetch('/api/kd')).json();
  if (r.code) log('error', r.msg);
  else log('ok', '物流方式: ' + (r.data.length ? r.data.map(k => k.name + '(' + k.fee + '分)').join('、') : '无'));
};

/* ---------- 订单查询 ---------- */
$('btnOrder').onclick = async () => {
  const cardno = $('cardno').value.trim(), phone = $('ophone').value.trim();
  if (!cardno && !phone) { log('error', '请至少填写证件号或手机号'); return; }
  const r = await (await fetch('/api/orders?cardno=' + encodeURIComponent(cardno) +
    '&phone=' + encodeURIComponent(phone))).json();
  if (r.code) { log('error', r.msg); return; }
  const tb = $('orderBody');
  tb.innerHTML = '';
  $('orderEmpty').style.display = r.data.length ? 'none' : '';
  r.data.forEach(o => {
    const tr = document.createElement('tr');
    [o.orderCode || o.sId, o.msisdn, o.orderStatus || o.status, o.productName || o.name,
     o.orderAmount || o.amount, o.createTime || o.orderTime, o.receiverAddr || o.addr].forEach(v => {
      const td = document.createElement('td'); td.textContent = v || ''; tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  log('ok', '订单 ' + r.data.length + ' 条');
};

/* ---------- 页签 ---------- */
document.querySelectorAll('.tab').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('on'));
    tab.classList.add('on');
    $('p-' + tab.dataset.tab).classList.add('on');
  };
});

/* ---------- 类型选择（分页列表 20/页，替代下拉框） ---------- */
let rankList = [];   // 全部类型（rankList 接口动态 40 种）
let rankPage = 1;    // 当前页
let rankSel = '';    // 已选类型（'' = 不限）
const RANK_PAGE = 20;

function renderRankGrid(){
  const start = (rankPage - 1) * RANK_PAGE;
  const items = rankList.slice(start, start + RANK_PAGE);
  $('rankGrid').innerHTML = items.map(r =>
    `<button class="${r === rankSel ? 'sel' : ''}" data-v="${r}">${r}</button>`).join('');
  Array.from($('rankGrid').children).forEach(b => b.onclick = () => {
    rankSel = b.dataset.v;
    $('btnRank').textContent = rankSel;
    $('rankMask').style.display = 'none';
    log('info', '已选类型: ' + rankSel);
  });
  const total = Math.ceil(rankList.length / RANK_PAGE) || 1;
  $('rankPageNo').textContent = '第 ' + rankPage + '/' + total + ' 页 · 共 ' + rankList.length + ' 种';
  $('rankPrev').disabled = rankPage <= 1;
  $('rankNext').disabled = rankPage >= total;
}
$('btnRank').onclick = () => { rankPage = 1; renderRankGrid(); $('rankMask').style.display = 'flex'; };
$('rankClose').onclick = () => $('rankMask').style.display = 'none';
$('rankMask').onclick = e => { if (e.target === $('rankMask')) $('rankMask').style.display = 'none'; };
$('rankPrev').onclick = () => { if (rankPage > 1) { rankPage--; renderRankGrid(); } };
$('rankNext').onclick = () => { if (rankPage * RANK_PAGE < rankList.length) { rankPage++; renderRankGrid(); } };
$('rankNone').onclick = () => {
  rankSel = '';
  $('btnRank').textContent = '不限';
  $('rankMask').style.display = 'none';
};

/* ---------- 初始化 ---------- */
(async () => {
  // 类型列表（rankList 接口动态，失败回退类型.txt）
  try {
    const r = await (await fetch('/api/ranks')).json();
    if (r.code) throw new Error(r.msg);
    rankList = r.data || [];
    log('info', '类型 ' + rankList.length + ' 种: ' + rankList.join(' '));
  } catch(e) {
    log('error', '类型加载失败: ' + e.message);
  }
  log('info', '靓号查询 v1.0.6 服务已连接，开始使用吧 ~');
  connectSSE();
})();
</script>
</body>
</html>
"""


# ---------- 数据库管理页（独立页面文件 db_page.html，专业 GUI + 工作流面板） ----------

_DB_PAGE_CACHE = None


def _get_db_page():
    """读 db_page.html（与类型.txt 同模式，PyInstaller onefile 下 __file__→_MEIPASS）"""
    global _DB_PAGE_CACHE
    if _DB_PAGE_CACHE is None:
        with open(os.path.join(BASE_DIR, "db_page.html"), encoding="utf-8") as f:
            _DB_PAGE_CACHE = f.read()
    return _DB_PAGE_CACHE


@app.route("/db")
def render_db_page():
    return Response(_get_db_page(), mimetype="text/html")


@app.route("/api/db/path")
def api_db_path():
    return jsonify({"code": 0, "path": duckdb_store.get_db_path()})


def render_index():
    return Response(PAGE, mimetype="text/html")


# ---------- 托盘 + 启动 ----------

_server = None


def _find_free_port(preferred):
    for port in [preferred] + list(range(preferred + 1, preferred + 200)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def _make_tray_icon():
    """PIL 画一个粉紫渐变圆图标"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(64):
        k = y / 63
        col = tuple(int(a + (b - a) * k) for a, b in zip((255, 143, 196), (142, 90, 216)))
        d.line([(0, y), (64, y)], fill=col)
    mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(mask).ellipse([2, 2, 62, 62], fill=255)
    img.putalpha(mask)
    ImageDraw.Draw(img).ellipse([2, 2, 62, 62], outline=(255, 255, 255, 160), width=2)
    return img


def main():
    import pystray

    global _server
    port = PORT
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass
    port = _find_free_port(port)
    url = f"http://127.0.0.1:{port}/"

    _server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=_server.serve_forever, daemon=True).start()

    # 预加载背景（不阻塞启动）
    threading.Thread(target=_bg_load, daemon=True).start()

    if "--no-browser" not in sys.argv:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    icon = pystray.Icon(
        "ms_lianghao", _make_tray_icon(), "民生靓号查询",
        menu=pystray.Menu(
            pystray.MenuItem("打开界面", lambda: webbrowser.open(url)),
            pystray.MenuItem("数据库管理", lambda: webbrowser.open(url + "db")),
            pystray.MenuItem("退出", lambda: icon.stop()),
        ),
    )
    icon.run()
    # 托盘退出后关闭服务
    if _server:
        _server.shutdown()


if __name__ == "__main__":
    main()
