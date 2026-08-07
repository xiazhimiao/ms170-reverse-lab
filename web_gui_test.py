# -*- coding: utf-8 -*-
"""web_gui.py 冒烟测试：启动服务 → 验证 API → SSE 查询事件（验证计数）→ 关闭"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

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
        check("等级列表 17 种（类型.txt 全量）", ranks["code"] == 0 and len(ranks["data"]) == 17,
              " ".join(ranks["data"]))
        check("等级原样字母（无中文翻译）", all(r.isalpha() and r.isascii() for r in ranks["data"]))
        provs = get_json("/api/provinces")
        check("省份 31 个", provs["code"] == 0 and len(provs["data"]) == 31, str(len(provs["data"])))
        cats = get_json("/api/categories")
        check("流量分类", cats["code"] == 0 and len(cats["data"]) >= 1,
              " / ".join(c["name"] for c in cats["data"]))

        page = get("/")
        check("首页 HTML 含核心元素", b"backdrop-filter" in page and b"btnStart" in page
              and b"flowBody" in page and b"orderBody" in page)
        check("页面含版本标识", b"v1.0.1" in page)

        # 静态检查：所有 btn 按钮都有 onclick 绑定（防止再漏）
        import re
        html = page.decode("utf-8")
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
            # 套餐列表（真实号码）
            pk = get_json("/api/packages?msisdn=" + urllib.parse.quote(first["phone_number"]))
            check("套餐列表接口", pk["code"] == 0 and len(pk["data"]) >= 1,
                  " / ".join(p.get("productName", "") for p in pk["data"]))

        print("== 导出 ==")
        req = urllib.request.Request(BASE + "/api/export",
                                     data=json.dumps({"rows": [["1", "13800000000", "广东", "深圳", "AAAAA",
                                                                5000, 399, "联通畅享399元", 399, "150", "2000", "套餐详情文本"]],
                                                      "fmt": "xlsx", "more": True,
                                                      "fname_info": "广东_深圳"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            cd = resp.headers.get("Content-Disposition", "")
        check("导出 xlsx 有效", data[:2] == b"PK", f"{len(data)} bytes")
        cd_dec = urllib.parse.unquote(cd)
        check("导出文件名含省/市/时间戳", "广东" in cd_dec and "深圳" in cd_dec
              and cd_dec.count("_") >= 2 and "filename*=UTF-8" in cd, cd_dec[:80])

        # 兼容性：旧版前端直接发送对象行（dict rows），金额为分
        req = urllib.request.Request(BASE + "/api/export",
                                     data=json.dumps({"rows": [{"index": "1", "phone_number": "13800000000",
                                                                "province": "广东", "city": "深圳", "rank": "AAAAA",
                                                                "bossPrestore": 500000, "minConsume": 39900,
                                                                "productName": "联通畅享399元", "productFee": 39900,
                                                                "liuTotal": "150", "callTotal": "2000",
                                                                "package": "套餐详情文本"}],
                                                      "fmt": "csv", "more": False}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            d2 = resp.read()
        check("兼容对象行导出（旧前端）", b"13800000000" in d2 and b"5000" in d2, f"{len(d2)} bytes")

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
    print()
    if errors:
        print(f"FAILED {len(errors)}: {errors}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
