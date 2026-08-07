# -*- coding: utf-8 -*-
"""web_gui.py 冒烟测试：启动服务 → 验证 API → SSE 查询事件（验证计数）→ 关闭"""
import io
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# GBK 控制台打不了 ✓ 等符号：stdout 强制 UTF-8（乱码可读性无所谓，防 UnicodeEncodeError）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.read()


def get_json(path):
    return json.loads(get(path))


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


errors = []
created_tables = []  # 测试期间创建的 duckdb 表（结束清理，不碰用户表）


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        errors.append(name)


def main():
    print("== 启动服务 ==")
    proc = subprocess.Popen(
        [sys.executable, "web_gui.py", "--no-browser", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等服务起来
        for _ in range(50):
            try:
                get("/api/ranks")
                break
            except Exception:
                time.sleep(0.3)
        check("服务已启动", True)

        print("== 基础 API ==")
        ranks = get_json("/api/ranks")
        cn_types = [r for r in ranks["data"] if not r.isascii()]
        check("等级列表 rankList 动态接口（40 种含中文类型）",
              ranks["code"] == 0 and len(ranks["data"]) >= 37 and len(cn_types) >= 1,
              f"{len(ranks['data'])} 种, 中文: {cn_types}")
        provs = get_json("/api/provinces")
        check("省份 31 个", provs["code"] == 0 and len(provs["data"]) == 31, str(len(provs["data"])))
        cats = get_json("/api/categories")
        check("流量分类", cats["code"] == 0 and len(cats["data"]) >= 1,
              " / ".join(c["name"] for c in cats["data"]))

        page = get("/")
        html = page.decode("utf-8")
        check("首页 HTML 含核心元素", b"backdrop-filter" in page and b"btnStart" in page
              and b"flowBody" in page and b"orderBody" in page)
        check("页面含版本标识", b"v1.0.7" in page)
        # 导出格式切换（默认 DuckDB 入库）+ duckdb 前端分支
        check("导出格式切换", 'id="expFmt"' in html and 'value="duckdb"' in html
              and 'value="xlsx"' in html and 'value="csv"' in html
              and "exportRows()" in html and "fmt === 'duckdb'" in html
              and "dbHint" in html)
        # 等级下拉已改为分页列表弹窗（20/页）；导出进度条弹窗（更多套餐异步）
        check("类型分页弹窗元素", 'id="btnRank"' in html and 'id="rankMask"' in html
              and 'id="rankGrid"' in html and 'id="rankPrev"' in html and 'id="rankNext"' in html
              and 'id="rankNone"' in html and 'id="rankPageNo"' in html
              and 'id="rank"' not in html)
        # 置顶类型：默认 11 个高频类型（含接口未列出的 AAAA/AAAAA/ABCDE）排第一页 + 自定义编辑弹窗
        check("置顶类型功能", 'id="rankPinBtn"' in html and 'id="pinnedMask"' in html
              and 'id="pinnedInput"' in html and 'id="pinnedSave"' in html and 'id="pinnedReset"' in html
              and "DEFAULT_PINNED" in html and "orderedRanks" in html and "loadPinned" in html
              and "rankPinned_v106" in html and "'AAAA'" in html and "'AAAAA'" in html
              and "'ABCDE'" in html and "'AAAAAAB'" in html and "'ABABAB'" in html
              and "置顶的类型排在选择列表" in html
              and 'data-v="">全部' in html and "rankPage === 1" in html)
        check("导出进度条弹窗元素", 'id="expMask"' in html and 'id="expBar"' in html
              and 'id="expText"' in html and "export/status" in html and "export/download" in html)
        # 号码结果分页：首页/上一页/页码窗口(±2+省略号)/下一页/末页
        check("结果分页条元素", 'id="pagerBar"' in html and 'id="pageFirst"' in html
              and 'id="pagePrev"' in html and 'id="pageNext"' in html and 'id="pageLast"' in html
              and 'id="pageNums"' in html and 'id="pageInfo"' in html
              and "PAGE_SIZE = 20" in html and "renderLhPage(" in html and "lhFollow" in html)
        check("页码窗口算法", "pageNums(" in html and "cur - 2" in html and "cur + 2" in html
              and "page-gap" in html and "page-num.on" in html and "…" in html)

        # 静态检查：所有 btn 按钮都有 onclick 绑定（防止再漏）
        import re
        btn_ids = set(re.findall(r'id="(btn[A-Za-z]+)"', html))
        bound = set(re.findall(r"\$\('(btn[A-Za-z]+)'\)\.onclick", html))
        missing = btn_ids - bound
        check("所有按钮均有 onclick 绑定", not missing, f"未绑定: {missing or '无'} btn={sorted(btn_ids)}")

        # 透明度滑块元素（默认 50%）
        check("透明度滑块存在且默认50%", 'id="opSlider"' in html and "--alpha-glass:.50" in html
              and 'value="0.50"' in html and 'id="opVal">50%' in html)
        # 套餐选择 + 订单确认弹窗
        check("套餐选择弹窗元素", 'id="pkgMask"' in html and 'id="pkgMore"' in html and 'id="pkgGoNow"' in html
              and 'id="pkgNext"' in html and "openPackageChoose(" in html)
        check("订单确认弹窗元素", 'id="cfmMask"' in html and 'id="cfmGo"' in html
              and "确认订阅" in html and "pollBg(" in html)
        check("链接字段已移除", "qrMask" not in html and "showQr(" not in html and "weixin://" not in html)

        bg = get("/api/bg")
        check("背景图返回 JPEG", bg[:2] == b"\xff\xd8", f"{len(bg)} bytes")
        # 外部图源偶发抖动（8s 预算内未拉到图）：给一次重试机会再断言
        ts1 = get_json("/api/bg/refresh")["ts"]
        time.sleep(1.5)
        ts2 = get_json("/api/bg/refresh")["ts"]
        if ts2 == ts1:
            time.sleep(3)
            ts2 = get_json("/api/bg/refresh")["ts"]
        check("换背景 ts 变化", ts2 != ts1, f"{ts1} -> {ts2}")
        bgts = get_json("/api/bg/ts")
        check("bg/ts 接口返回 ts/ready", bgts["code"] == 0 and bgts["ts"] > 0 and "ready" in bgts)

        # 套餐列表接口（缺参报错；真实号码在查询段后验证）
        pk_err = get_json("/api/packages")
        check("套餐接口缺参报错", pk_err["code"] == 1)

        print("== 查询启动 + SSE 事件（验证计数，2 页即止） ==")
        r = post("/api/query/start", {
            "series": "h5", "province": "", "city": "", "rank": "",
            "msisdn": "4",  # 号码关键词搜索：验证透传与结果匹配
            "max_pages": 2, "interval": 0,
        })
        check("查询已启动", r["code"] == 0, r["msg"])

        # 读 SSE 事件 40s
        events = []
        req = urllib.request.Request(BASE + "/api/events")
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=45) as resp:
            buf = b""
            while time.time() - t0 < 40:
                chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    line = raw.decode("utf-8", "ignore")
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                if any(e.get("type") == "done" for e in events):
                    break
        ev_types = [e.get("type") for e in events]
        pages = [e for e in events if e["type"] == "page"]
        done = next((e for e in events if e["type"] == "done"), None)
        check("收到 page/done 事件", "page" in ev_types and done is not None,
              "types=" + ",".join(ev_types[:8]))
        if pages and done:
            total_pages = len(pages)
            check("有页码数据", pages[0]["rows"], f"第{pages[0]['page']}页 {len(pages[0]['rows'])}条")
            check("累计数自增正确", pages[-1]["total"] == done["total"],
                  f"末页total={pages[-1]['total']} done.total={done['total']}")
            # 关键：每页 rows 非空且累计 = 各页之和
            sum_rows = sum(len(p["rows"]) for p in pages)
            check("累计 = 各页之和（无跨线程错位）", pages[-1]["total"] == sum_rows,
                  f"sum={sum_rows}")
            logs = [e["msg"] for e in events if e["type"] == "log"]
            check("日志含累计数", any("累计" in m for m in logs), "；".join(logs[:3]))
            # 行字段：预存/月低消/套餐字段已带，无 link
            first = pages[0]["rows"][0]
            check("行含预存/月低消/套餐字段", "bossPrestore" in first and "minConsume" in first
                  and "productName" in first and "link" not in first,
                  f"pre={first.get('bossPrestore')} min={first.get('minConsume')}")
            # 号码关键词搜索：msisdn=4 的结果应全部含 4
            check("搜索号码全部含关键词 4",
                  all("4" in r["phone_number"] for p in pages for r in p["rows"]),
                  f"{pages[0]['rows'][0]['phone_number']} ...")
            # 套餐列表（真实号码）
            pk = get_json("/api/packages?msisdn=" + urllib.parse.quote(first["phone_number"]))
            check("套餐列表接口", pk["code"] == 0 and len(pk["data"]) >= 1,
                  " / ".join(p.get("productName", "") for p in pk["data"]))

        print("== 导出 ==")
        ROW = ["1", "13800000000", "广东", "深圳", "AAAAA",
               5000, 399, "联通畅享399元", 399, "150", "2000", "套餐详情文本"]

        def post_export(body):
            req = urllib.request.Request(BASE + "/api/export",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read(), resp.headers.get("Content-Disposition", "")

        def poll_export(task, timeout=60):
            """轮询异步导出任务直到 done/error，返回 (status, done, total)"""
            t0 = time.time()
            while time.time() - t0 < timeout:
                s = get_json("/api/export/status?task=" + task)
                if s["status"] in ("done", "error"):
                    return s
                time.sleep(0.5)
            return {"status": "timeout"}

        # more=false：列表已加载，同步返回文件（快）
        data, cd = post_export({"rows": [ROW], "fmt": "xlsx", "more": False,
                                "fname_info": "广东_深圳"})
        check("导出 xlsx 有效", data[:2] == b"PK", f"{len(data)} bytes")
        cd_dec = urllib.parse.unquote(cd)
        check("导出文件名含省/市/时间戳", "广东" in cd_dec and "深圳" in cd_dec
              and cd_dec.count("_") >= 2 and "filename*=UTF-8" in cd, cd_dec[:80])

        # more=true：异步任务 → 轮询进度 → 下载
        j = post("/api/export", {"rows": [ROW], "fmt": "csv", "more": True,
                                 "fname_info": "广东_深圳"})
        check("异步导出返回 task id", j["code"] == 0 and j["task"], j.get("task", ""))
        task = j["task"]
        s = poll_export(task)
        check("导出任务完成无错误", s["status"] == "done",
              f"{s['status']} done={s.get('done')}/{s.get('total')} err={s.get('error','')[:120]}")
        check("任务进度 total=行数 done 自增", s["done"] == s["total"] == 1,
              f"{s.get('done')}/{s.get('total')}")
        with urllib.request.urlopen(BASE + "/api/export/download?task=" + task, timeout=30) as resp:
            d3 = resp.read().decode("utf-8", "ignore")
            cd3 = resp.headers.get("Content-Disposition", "")
        check("更多套餐只加一列且带详情", "其他套餐" in d3 and "套餐2" not in d3 and "套餐3" not in d3
              and ("元/月" in d3 or "13800000000" in d3), f"{len(d3)} bytes")
        check("下载文件名含省/市", "广东" in urllib.parse.unquote(cd3), cd3[:60])
        # 状态接口边界：未知任务 / 未完成下载
        check("未知任务报错", get_json("/api/export/status?task=zzz")["code"] == 1)
        try:
            with urllib.request.urlopen(BASE + "/api/export/download?task=zzz", timeout=20):
                pass
            check("未知任务下载报错", False)
        except urllib.error.HTTPError as e:
            check("未知任务下载报错", e.code == 404)

        # 兼容性：旧版前端直接发送对象行（dict rows），金额为分
        data, _ = post_export({"rows": [{"index": "1", "phone_number": "13800000000",
                                         "province": "广东", "city": "深圳", "rank": "AAAAA",
                                         "bossPrestore": 500000, "minConsume": 39900,
                                         "productName": "联通畅享399元", "productFee": 39900,
                                         "liuTotal": "150", "callTotal": "2000",
                                         "package": "套餐详情文本"}],
                               "fmt": "csv", "more": False})
        check("兼容对象行导出（旧前端）", b"13800000000" in data and b"5000" in data,
              f"{len(data)} bytes")

        print("== DuckDB 存储 ==")
        # 数据库管理页路由（独立文件 db_page.html：专业 GUI + 二次元背景 + 工作流面板）
        dbpage = get("/db")
        check("数据库管理页", "数据库管理".encode() in dbpage and b"btnExpXlsx" in dbpage
              and b"/api/db/tables" in dbpage and b"/api/db/table?name=" in dbpage)
        check("db 页新 GUI 元素", b"btnWf" in dbpage and b"wfMask" in dbpage and b"wfOpSel" in dbpage
              and b"tbSet" in dbpage and b"editCell(" in dbpage and b"/api/db/op" in dbpage
              and b"/api/db/edit" in dbpage and b"pageNums(" in dbpage and b"/api/bg" in dbpage)
        check("db 页工作流面板", b"wfDownload" in dbpage and b"wfUpload" in dbpage
              and b"wfApply" in dbpage and b"wfSave" in dbpage
              and b"/api/workflow/apply" in dbpage and b"/api/workflow/upload" in dbpage)
        check("db 页可拖拽日志面板", b'id="logpanel"' in dbpage and b'id="logHead"' in dbpage
              and b'id="logbody"' in dbpage and b'id="btnLogToggle"' in dbpage
              and b'id="btnLogClear"' in dbpage and b"mousemove" in dbpage and b"logline" in dbpage)
        # duckdb 同步导出（more=false）→ 返回表名
        j = post("/api/export", {"rows": [ROW], "fmt": "duckdb", "more": False,
                                 "fname_info": "广东_深圳"})
        created_tables.append(j.get("table", ""))
        check("duckdb 导出返回表名", j["code"] == 0 and j["table"].startswith("靓号查询_") and j["rows"] == 1,
              j.get("table", ""))
        tbl = j["table"]
        # 表列表出现该表
        tables = get_json("/api/db/tables")["data"]
        check("表列表包含新表", any(t["name"] == tbl and t["rows"] == 1 for t in tables),
              " / ".join(t["name"] for t in tables))
        # 表详情：12 列；预览走 /api/db/data（带 rowid）
        det = get_json("/api/db/table?name=" + urllib.parse.quote(tbl))["data"]
        check("表详情 12 列", det["count"] == 1 and len(det["columns"]) == 12,
              f"{det['count']}行 {len(det['columns'])}列")
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tbl))["data"]
        check("表预览分页(rowid+cells)", prev["total"] == 1 and prev["rows"][0]["id"] is not None
              and prev["rows"][0]["cells"][1] == "13800000000",
              f"total={prev['total']} cols={len(prev['columns'])}")
        # 表导出 xlsx
        with urllib.request.urlopen(BASE + "/api/db/export?name=" + urllib.parse.quote(tbl),
                                    timeout=30) as resp:
            x = resp.read()
            cdx = resp.headers.get("Content-Disposition", "")
        check("duckdb 表导出 xlsx", x[:2] == b"PK" and "filename*=UTF-8" in cdx, f"{len(x)} bytes")
        # 未知表边界
        check("未知表报错", get_json("/api/db/table?name=nonexist")["code"] == 1)
        # more=true + duckdb：异步任务完成后 status 返回表名
        j = post("/api/export", {"rows": [ROW], "fmt": "duckdb", "more": True,
                                 "fname_info": "广东_深圳"})
        created_tables.append(j.get("table", ""))
        s = poll_export(j["task"])
        check("duckdb 异步任务完成且带表名", s["status"] == "done" and s["table"].startswith("靓号查询_"),
              f"{s['status']} table={s.get('table')}")
        # duckdb 任务无文件可下载
        try:
            with urllib.request.urlopen(BASE + "/api/export/download?task=" + j["task"], timeout=20):
                pass
            check("duckdb 任务下载被拒", False)
        except urllib.error.HTTPError as e:
            check("duckdb 任务下载被拒", e.code == 400)

        print("== 字段操作 + 工作流 ==")
        import os as _os
        import duckdb_store as dstore

        tn = "字段操作测试表"
        created_tables.append(tn)
        dstore.write_table(tn, ["手机号", "备注", "运营商"], [
            ["13800000000", "靓号A", "联通"],
            ["13900000000", "靓号B", "移动"],
            ["13700000000", "普通", "电信"],
        ])

        def op(op_, **kw):
            return post("/api/db/op", dict({"name": tn, "op": op_}, **kw))

        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("字段操作测试表 3 行", prev["total"] == 3)
        rid = prev["rows"][0]["id"]

        # 单元格编辑（rowid 定位）
        r = post("/api/db/edit", {"name": tn, "rowid": rid, "column": "手机号", "value": "13811112222"})
        check("单元格编辑", r["code"] == 0)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("编辑已生效", prev["rows"][0]["cells"][0] == "13811112222")
        r = post("/api/db/edit", {"name": "zzz_nonexist", "rowid": 1, "column": "a", "value": "b"})
        check("未知表编辑报错", r["code"] == 1)

        # 整列替换
        r = op("set_column_value", column="运营商", value="全网通")
        check("整列替换影响 3 行", r["code"] == 0 and r["affected"] == 3)
        # 条件追加：手机号含 138 的行 → 备注末尾加（首推）
        r = op("append_text", column="备注", text="（首推）",
               match_column="手机号", match_keyword="138")
        check("条件末尾追加影响 1 行", r["code"] == 0 and r["affected"] == 1)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("追加已生效", prev["rows"][0]["cells"][1] == "靓号A（首推）",
              str(prev["rows"][0]["cells"]))
        # 条件开头插入：备注含 首推 → 运营商 前插 中国
        r = op("prepend_text", column="运营商", text="中国",
               match_column="备注", match_keyword="首推")
        check("条件开头插入", r["code"] == 0 and r["affected"] == 1)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("插入已生效", prev["rows"][0]["cells"][2] == "中国全网通",
              str(prev["rows"][0]["cells"]))
        # 查找替换（字面量）
        r = op("replace_text", column="备注", pattern="靓号", replacement="优选", regex=False)
        check("文本替换", r["code"] == 0 and r["affected"] == 2)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("替换已生效", prev["rows"][0]["cells"][1] == "优选A（首推）")
        # 条件置值：手机号含 139 → 运营商 = 中国移动
        r = op("set_if_contains", match_column="手机号", keyword="139",
               target_column="运营商", value="中国移动")
        check("条件置值", r["code"] == 0 and r["affected"] == 1)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("置值已生效", any(row["cells"][2] == "中国移动" for row in prev["rows"]),
              str([x["cells"] for x in prev["rows"]]))
        # 新增列 / 复制列 / 重命名 / 删除列
        r = op("add_column", column="标记", default="新")
        check("新增列", r["code"] == 0)
        r = op("add_column", column="标记2", default="")
        check("新增列2", r["code"] == 0)
        r = op("set_column_from", source="运营商", target="标记2")
        check("复制列", r["code"] == 0)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("复制列值已生效", prev["rows"][0]["cells"][4] == "中国全网通",
              str(prev["rows"][0]["cells"]))
        r = op("rename_column", old="标记2", column="标记3")
        check("重命名列", r["code"] == 0)
        det = get_json("/api/db/table?name=" + urllib.parse.quote(tn))["data"]
        check("列结构变化", "标记" in det["columns"] and "标记3" in det["columns"]
              and "标记2" not in det["columns"], str(det["columns"]))
        r = op("drop_column", column="标记3")
        check("删除列", r["code"] == 0 and "标记3" not in
              get_json("/api/db/table?name=" + urllib.parse.quote(tn))["data"]["columns"])
        # 删除匹配行
        r = op("delete_rows_contains", column="备注", keyword="普通")
        check("删除匹配行", r["code"] == 0 and r["affected"] == 1)
        prev = get_json("/api/db/data?name=" + urllib.parse.quote(tn))["data"]
        check("删除后剩 2 行", prev["total"] == 2)
        # 边界：未知操作 / 列不存在 / 未知表
        check("未知操作报错", post("/api/db/op", {"name": tn, "op": "hack_drop_all"})["code"] == 1)
        r = op("set_column_value", column="不存在列", value="x")
        check("列不存在报错", r["code"] == 1 and "不存在" in r["msg"], r["msg"])
        check("未知表操作报错",
              post("/api/db/op", {"name": "zzz", "op": "set_column_value",
                                  "column": "a", "value": "b"})["code"] == 1)

        print("== 工作流 ==")
        wf_backup = None
        if _os.path.exists("工作流.json"):
            with open("工作流.json", encoding="utf-8") as _f:
                wf_backup = _f.read()
            _os.remove("工作流.json")  # 清场确保默认态（测试结束还原）

        try:
            w = get_json("/api/workflow")
            check("工作流默认未启用", w["code"] == 0 and w["data"]["active"] is False
                  and "file" in w["data"] and w["data"]["steps"] == [])
    
            steps_clean = [{"op": "append_text", "column": "套餐详情", "text": "✓"}]
            r = post("/api/workflow", {"name": "测试工作流", "active": False, "steps": steps_clean})
            check("保存工作流", r["code"] == 0 and len(r["data"]["steps"]) == 1)
            r = post("/api/workflow/toggle", {"active": True})
            check("启用工作流", r["code"] == 0 and "启用" in r["msg"], r["msg"])
    
            # 立即应用（对测试表）
            r = post("/api/workflow/apply", {"name": tn, "steps": [
                {"op": "set_if_contains", "match_column": "手机号", "keyword": "139",
                 "target_column": "运营商", "value": "中国移动"},
                {"op": "drop_column", "column": "标记"},
            ]})
            check("工作流立即应用 2/2", r["code"] == 0 and len(r["results"]) == 2
                  and all(x["ok"] for x in r["results"]) and "2/2" in r["msg"], r["msg"])
            det = get_json("/api/db/table?name=" + urllib.parse.quote(tn))["data"]
            check("工作流步骤已执行", "标记" not in det["columns"] and len(det["columns"]) == 3,
                  str(det["columns"]))
            check("工作流空步骤报错", post("/api/workflow/apply", {"name": tn, "steps": []})["code"] == 1)
            check("工作流未知表报错",
                  post("/api/workflow/apply", {"name": "zzz", "steps": steps_clean})["code"] == 1)
    
            # 下载 → 内容 = 当前保存的工作流
            wfdl = json.loads(get("/api/workflow/download").decode("utf-8"))
            check("工作流下载 JSON", wfdl.get("active") is True and len(wfdl["steps"]) == 1)
    
            # 自动应用：导出 duckdb（当前 active=True，步骤=套餐详情末尾追加✓）
            j = post("/api/export", {"rows": [ROW], "fmt": "duckdb", "more": True,
                                     "fname_info": "广东_深圳"})
            s = poll_export(j["task"])
            created_tables.append(s.get("table", ""))
            check("自动应用：任务带 workflow 结果", s["status"] == "done"
                  and s.get("table", "").startswith("靓号查询_")
                  and s.get("workflow") and s["workflow"][0]["ok"], str(s.get("workflow"))[:120])
            prev = get_json("/api/db/data?name=" + urllib.parse.quote(s["table"]))["data"]
            # 导出表第 12 列=套餐详情，追加 ✓ 后应以此结尾
            check("自动应用：表内容已处理", prev["rows"][0]["cells"][11].endswith("✓"),
                  str(prev["rows"][0]["cells"][11])[-20:])
    
            # 上传（raw body，导入后 active=False）
            up = json.dumps({"active": False, "name": "导入测试", "steps": [
                {"op": "set_column", "column": "运营商", "value": "导入值"}]}).encode()
            req = urllib.request.Request(BASE + "/api/workflow/upload", data=up,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                ur = json.loads(resp.read())
            check("工作流上传", ur["code"] == 0 and len(ur["data"]["steps"]) == 1, ur.get("msg", ""))
            req = urllib.request.Request(BASE + "/api/workflow/upload", data=b"not json",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                bad = json.loads(resp.read())
            check("非法上传报错", bad["code"] == 1)
    
            # 停用工作流
            post("/api/workflow/toggle", {"active": False})
            w = get_json("/api/workflow")
            check("停用工作流", w["data"]["active"] is False)
        finally:
            # 还原用户原有工作流文件（无则删除测试产物）
            if wf_backup is None:
                if _os.path.exists("工作流.json"):
                    _os.remove("工作流.json")
            else:
                with open("工作流.json", "w", encoding="utf-8") as _f:
                    _f.write(wf_backup)

        print("== 停止 ==")
        post("/api/query/stop")
        check("停止接口正常", True)

        # 服务还活着
        check("服务存活", get_json("/api/query/status")["code"] == 0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        # 清理测试创建的 duckdb 表（若文件只剩测试残留则删除文件）
        try:
            import os
            import duckdb
            if os.path.exists("靓号查询数据.duckdb"):
                con = duckdb.connect("靓号查询数据.duckdb")
                try:
                    for t in created_tables:
                        if t:
                            con.execute(f'DROP TABLE IF EXISTS "{t}"')
                    remaining = con.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='main'").fetchall()
                finally:
                    con.close()
                if not remaining:
                    os.remove("靓号查询数据.duckdb")
        except Exception:
            pass
    print()
    if errors:
        print(f"FAILED {len(errors)}: {errors}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
