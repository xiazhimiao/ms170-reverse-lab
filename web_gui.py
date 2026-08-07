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
                    page, province, city, rank, series, workers=5)
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


@app.route("/api/export", methods=["POST"])
def api_export():
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("rows") or []
    fmt = body.get("fmt", "xlsx")
    more = bool(body.get("more"))          # 附带更多套餐（套餐2/3 新字段）
    fname_info = body.get("fname_info") or "全国_全部"  # 文件名信息（省_市）
    if not rows:
        return jsonify({"code": 1, "msg": "没有可导出的数据", "data": []})

    headers = ["序号", "手机号", "省份", "城市", "等级", "预存(元)", "月低消(元)",
               "套餐", "月费(元/月)", "流量(G)", "通话(分钟)", "套餐详情"]
    if more:
        # 并发拉取每个号码的可办套餐：第2/3个独立列，第4个及以后合并进「其他套餐」字段（| 分隔）
        from concurrent.futures import ThreadPoolExecutor
        phones = [r[1] for r in rows]
        with ThreadPoolExecutor(max_workers=6) as pool:
            pkg_lists = list(pool.map(engine.fetcher.get_products_for_number, phones))
        extra = []
        for pkgs in pkg_lists:
            p2 = pkgs[1] if len(pkgs) > 1 else {}
            p3 = pkgs[2] if len(pkgs) > 2 else {}
            rest = " | ".join("{}（{}元/月）".format(
                p.get("productName", ""), (p.get("productFee") or 0) // 100)
                for p in pkgs[3:])
            extra.append([
                p2.get("productName", ""), (p2.get("productFee") or 0) // 100,
                p3.get("productName", ""), (p3.get("productFee") or 0) // 100,
                rest])
        headers += ["套餐2", "套餐2月费(元)", "套餐3", "套餐3月费(元)", "其他套餐"]
        rows = [list(r) + e for r, e in zip(rows, extra)]

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
    # 中文文件名需 RFC 5987 编码（latin-1 头会崩）
    import urllib.parse
    quoted = urllib.parse.quote(fname)
    return Response(bio.getvalue(), mimetype=mime,
                    headers={"Content-Disposition": f"attachment; filename=export.{fmt}; filename*=UTF-8''{quoted}"})


# ---------- 随机二次元背景 ----------

_bg = {"img": None, "ts": 0}
_bg_lock = threading.Lock()


def _bg_load():
    # 短超时：图片源慢/挂时最多等 8s 放弃，避免刷新接口被外部源拖死
    img = anime_bg.fetch_random_bg(timeout=6, total_budget=8)
    if img:
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
              padding:8px 0 2px}
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
        <label>等级</label><select id="rank"><option value="">不限</option></select>
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
        <button class="pill ghost" id="btnExport">⬇ 导出 Excel</button>
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
  <div class="disclaimer">⚠ 软件仅供学习测试使用，请勿商用！请合理控制查询频率，尊重目标服务。</div>
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

/* ---------- 靓号查询 ---------- */
function addLhRows(rows){
  const tb = $('lhBody'), empty = $('lhEmpty');
  empty.style.display = 'none';
  rows.forEach(r => {
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
  });
  lhRows = lhRows.concat(rows);
  $('counter').textContent = '已获取 ' + lhRows.length + ' 条';
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
    rank: $('rank').value,
    max_pages: parseInt($('maxPages').value || '0', 10),
    interval: parseFloat($('interval').value || '2')
  };
  const r = await (await fetch('/api/query/start', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(params)
  })).json();
  if (r.code) { log('error', r.msg); return; }
  setRunning(true); setStatus('查询启动...');
  log('info', '开始查询: ' + params.series + (params.rank ? ' · 等级 ' + params.rank : ''));
}
$('btnStart').onclick = startQuery;
$('btnStop').onclick = async () => { await fetch('/api/query/stop', {method:'POST'}); };
$('btnClear').onclick = () => {
  $('lhBody').innerHTML = '';
  lhRows = [];
  $('lhEmpty').style.display = '';
  $('counter').textContent = '';
  log('info', '已清空');
};

async function exportRows(fmt){
  if (!lhRows.length) { log('error', '没有可导出的数据'); return; }
  const more = $('expMore').checked;
  if (more) log('info', '正在拉取更多套餐（' + lhRows.length + ' 个号码）...');
  const provSel = $('province').selectedOptions[0];
  const citySel = $('city').selectedOptions[0];
  const fnameInfo = (provSel ? provSel.textContent : '全国') + '_' + (citySel ? citySel.textContent : '全部');
  const r = await fetch('/api/export', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rows: lhRows.map(r => [r.index, r.phone_number, r.province, r.city, r.rank,
      yuan(r.bossPrestore), yuan(r.minConsume), r.productName || '',
      yuan(r.productFee), r.liuTotal || '', r.callTotal || '', r.package || '']),
      fmt, more, fname_info: fnameInfo})
  });
  if (!r.ok) { log('error', '导出失败'); return; }
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
  log('ok', '已导出 ' + lhRows.length + ' 条 → ' + fname);
}
$('btnExport').onclick = () => exportRows('xlsx');

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
  $('pkgName').textContent = r.productName || '默认套餐';
  $('pkgFee').textContent = yuan(r.productFee) + ' 元/月';
  $('pkgQuota').textContent = '流量 ' + (r.liuTotal || 0) + ' G · 通话 ' + (r.callTotal || 0) + ' 分钟';
  $('pkgDesc').textContent = r.package || '';
  $('pkgList').style.display = 'none';
  $('pkgList').innerHTML = '';
  $('pkgNext').style.display = 'none';
  $('pkgMask').style.display = 'flex';
  log('ok', '打开号码 ' + r.phone_number + ' 套餐选择');
}
$('pkgClose').onclick = closeMasks;
$('pkgMask').onclick = e => { if (e.target === $('pkgMask')) closeMasks(); };
$('cfmClose').onclick = closeMasks;
$('cfmMask').onclick = e => { if (e.target === $('cfmMask')) closeMasks(); };

// 立即办理：默认套餐 → 订单确认
$('pkgGoNow').onclick = () => {
  if (!pkgRow) return;
  showConfirm(pkgRow.phone_number, pkgRow.productName || '默认套餐',
    yuan(pkgRow.productFee) + ' 元/月', yuan(pkgRow.bossPrestore) + ' 元',
    '流量 ' + (pkgRow.liuTotal || 0) + ' G · 通话 ' + (pkgRow.callTotal || 0) + ' 分钟',
    pkgRow.package || '');
};

// 更多套餐选择：加载 qryProductList 列表
$('pkgMore').onclick = async () => {
  const btn = $('pkgMore'); btn.disabled = true;
  try {
    const r = await (await fetch('/api/packages?msisdn=' + encodeURIComponent(pkgRow.phone_number))).json();
    if (r.code) throw new Error(r.msg);
    pkgSelList = r.data;
    const list = $('pkgList');
    list.style.display = 'flex';
    list.innerHTML = pkgSelList.map((p, i) => `
      <div class="pkg-item" id="pkgItem${i}">
        <div class="pkg-name">${p.productName}</div>
        <div class="pkg-fee">${yuan(p.productFee)} 元/月</div>
        <div class="pkg-desc">${(p.serviceDesc || '').slice(0, 120)}…</div>
      </div>`).join('');
    pkgSelList.forEach((_, i) => $('pkgItem' + i).onclick = () => {
      pkgSelIdx = i;
      document.querySelectorAll('.pkg-item').forEach(el => el.classList.remove('sel'));
      $('pkgItem' + i).classList.add('sel');
      $('pkgNext').style.display = '';
    });
    log('ok', '该号码可办套餐 ' + pkgSelList.length + ' 个');
  } catch(err) { log('error', '套餐列表加载失败: ' + err.message); }
  btn.disabled = false;
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

/* ---------- 初始化 ---------- */
(async () => {
  // 等级列表（类型.txt 全量，原样展示）
  try {
    const r = await (await fetch('/api/ranks')).json();
    $('rank').innerHTML = '<option value="">不限</option>' +
      r.data.map(x => `<option value="${x}">${x}</option>`).join('');
    log('info', '等级 ' + r.data.length + ' 种: ' + r.data.join(' '));
  } catch(e) {}
  log('info', '服务已连接，开始使用吧 ~');
  connectSSE();
})();
</script>
</body>
</html>
"""


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
            pystray.MenuItem("退出", lambda: icon.stop()),
        ),
    )
    icon.run()
    # 托盘退出后关闭服务
    if _server:
        _server.shutdown()


if __name__ == "__main__":
    main()
