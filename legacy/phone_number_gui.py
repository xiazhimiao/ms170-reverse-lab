# -*- coding: utf-8 -*-
"""民生靓号查询工具（v4 · 二次元毛玻璃风）

- 背景：随机二次元图接口（t.alcy.cc/ys 原神 等 5 源 fallback）+ 程序化晚霞樱花兜底，
  每次启动/点击"换背景"拉取不同图片
- 毛玻璃：背景裁剪 → 高斯模糊 → 深紫罩 → 圆角描边（anime_bg.make_glass_layer）
- 控件：深色融入玻璃卡，按钮为 PIL 自绘圆角渐变（hover 高亮）
- 功能：靓号查询 / 流量专区 / 订单查询 三页签，导出 Excel/CSV

依赖：requests / pillow / numpy（背景生成）
"""

import threading
import time
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageDraw, ImageTk

import anime_bg
from phone_number_fetcher import PhoneNumberFetcher

APP_TITLE = "民生靓号查询工具"
RANK_HINTS = ["", "AAAA", "AAA", "AA", "A", "ABCD", "连号", "豹子"]
SERIES_MAP = {"靓号专区 (h5)": "h5", "流量卡专区 (liu)": "liu"}

# ---- 深色主题常量（与玻璃罩 tint 视觉匹配；Tk 控件不支持真透明，用近似罩后色调保证融感）----
CARD_BG = "#2a2150"          # 控件容器底色（罩后紫调）
DARK = "#1c1638"             # 输入/表格底色
DARK2 = "#31265c"            # 表头/滚动条
FG = "#efe7ff"               # 主文字
FG_PINK = "#ffb7d9"          # 强调文字（粉色）
ACCENT = "#8e5ad8"           # 选中紫

BTN_C1, BTN_C2 = "#ff8fc4", "#8e5ad8"   # 按钮渐变（粉→紫）
BTN2_C1, BTN2_C2 = "#5b4a8a", "#332654"  # 次要按钮渐变（灰紫）
FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)


class GlassButton(tk.Canvas):
    """PIL 自绘圆角渐变按钮：两态（normal/hover）"""

    def __init__(self, master, text, command=None, size=(104, 32),
                 c1=BTN_C1, c2=BTN_C2, font=FONT, fg="#ffffff"):
        super().__init__(master, width=size[0], height=size[1],
                         highlightthickness=0, bd=0, bg=CARD_BG)
        self._text, self._command = text, command
        self._font, self._fg = font, fg
        c1 = self._to_rgb(c1)
        c2 = self._to_rgb(c2)
        self._imgs = [self._render(c1, c2, b) for b in (1.0, 1.22)]
        self._photos = [ImageTk.PhotoImage(im) for im in self._imgs]  # 防 GC：保留全部引用
        self._state = 0
        self._enabled = True
        self._draw()
        self.bind("<Enter>", lambda e: self._set(1) if self._enabled else None)
        self.bind("<Leave>", lambda e: self._set(0))
        self.bind("<Button-1>", self._click)

    @staticmethod
    def _to_rgb(color):
        """'#rrggbb' 字符串或 (r,g,b) 元组 → (r,g,b)"""
        if isinstance(color, str):
            color = color.lstrip("#")
            return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        return color

    def _render(self, c1, c2, bright):
        w, h = int(self["width"]), int(self["height"])
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 逐行渐变（顶部 c1 亮 → 底部 c2 暗）
        for y in range(h):
            k = y / max(h - 1, 1)
            col = tuple(int(a + (b - a) * k) for a, b in zip(c2, c1))
            col = tuple(min(255, int(c * bright)) for c in col)
            d.line([(0, y), (w, y)], fill=col + (255,))
        # 圆角遮罩
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=255)
        img.putalpha(mask)
        # 高光描边
        ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2,
                                              outline=(255, 255, 255, 95), width=1)
        return img

    def _draw(self):
        self.delete("all")  # 先清旧图，避免重复堆叠
        self.create_image(0, 0, image=self._photos[self._state], anchor="nw")
        self.create_text(int(self["width"]) // 2, int(self["height"]) // 2,
                         text=self._text, fill=self._fg, font=self._font)

    def _set(self, state):
        self._state = state
        self._draw()

    def _click(self, _e):
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._set(0)


class TabButton(GlassButton):
    """页签按钮：选中态=渐变亮，未选中=暗紫底"""

    def __init__(self, master, text, command=None, size=(118, 30)):
        super().__init__(master, text, command, size=size,
                         c1="#ff9ecb", c2="#9b6ae0", font=FONT_S)
        self._active = False
        self._render_states()

    def _render_states(self):
        w, h = int(self["width"]), int(self["height"])
        inactive = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(inactive)
        for y in range(h):
            d.line([(0, y), (w, y)], fill=(56, 44, 92, 255))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=9, fill=255)
        inactive.putalpha(mask)
        self._photo_inactive = ImageTk.PhotoImage(inactive)
        self._text_color = "#c9b8ef"

    def set_active(self, active):
        self._active = active
        self._draw()

    def _draw(self):
        # 注意：super().__init__ 里第一次 _draw 时 _active/_photo_inactive 尚未创建，兜底
        active = getattr(self, "_active", False)
        if active:
            photo, color = self._photos[1], "#ffffff"
        else:
            photo = getattr(self, "_photo_inactive", self._photos[0])
            color = getattr(self, "_text_color", "#c9b8ef")
        self.delete("all")
        self.create_image(0, 0, image=photo, anchor="nw")
        self.create_text(int(self["width"]) // 2, int(self["height"]) // 2,
                         text=self._text, fill=color, font=self._font)


class LiangHaoPage(tk.Frame):
    """页签1：靓号查询"""

    def __init__(self, master, app):
        super().__init__(master, bg=CARD_BG)
        self.app = app
        self.searching = False
        self.stop_flag = False
        self.result_count = 0
        self._build_ui()

    def _build_ui(self):
        # ---- 参数行 ----
        row1 = tk.Frame(self, bg=CARD_BG)
        row1.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(row1, text="系列:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.series_combo = ttk.Combobox(row1, width=13, state="readonly",
                                         values=list(SERIES_MAP.keys()))
        self.series_combo.set("靓号专区 (h5)")
        self.series_combo.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(row1, text="省份:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.province_combo = ttk.Combobox(row1, width=15, state="readonly")
        self.province_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.province_combo.bind("<<ComboboxSelected>>", self._on_province_selected)
        tk.Label(row1, text="城市:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.city_combo = ttk.Combobox(row1, width=15, state="readonly")
        self.city_combo.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(row1, text="等级:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.rank_combo = ttk.Combobox(row1, width=9, values=RANK_HINTS)
        self.rank_combo.set("")
        self.rank_combo.pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(row1, text="页数:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.max_pages_var = tk.StringVar(value="50")
        ttk.Spinbox(row1, from_=1, to=500, width=5, textvariable=self.max_pages_var).pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(row1, text="间隔(秒):", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.interval_var = tk.StringVar(value="2")
        ttk.Spinbox(row1, from_=0, to=60, width=4, textvariable=self.interval_var).pack(side=tk.LEFT, padx=(4, 0))

        # ---- 控制行 ----
        row2 = tk.Frame(self, bg=CARD_BG)
        row2.pack(fill=tk.X, padx=8, pady=(4, 6))
        self.load_prov_btn = GlassButton(row2, "加载省份", self.load_provinces, size=(96, 30), c1=BTN2_C1, c2=BTN2_C2)
        self.load_prov_btn.pack(side=tk.LEFT)
        self.start_btn = GlassButton(row2, "开始查询", self.start_search, size=(104, 30))
        self.start_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.stop_btn = GlassButton(row2, "停止", self.stop_search, size=(76, 30), c1="#8e6a3c", c2="#4a3520")
        self.stop_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.export_btn = GlassButton(row2, "导出 Excel", self.export_excel, size=(104, 30), c1="#5b8ed8", c2="#2c4a7a")
        self.export_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.clear_btn = GlassButton(row2, "清空", self.clear_table, size=(76, 30), c1=BTN2_C1, c2=BTN2_C2)
        self.clear_btn.pack(side=tk.LEFT, padx=(10, 0))

        # ---- 结果表格 ----
        table_frame = tk.Frame(self, bg=CARD_BG)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        columns = ("index", "phone", "province", "city", "package", "link")
        headers = {"index": "序号", "phone": "手机号", "province": "省份",
                   "city": "城市", "package": "套餐", "link": "链接"}
        widths = {"index": 46, "phone": 112, "province": 72, "city": 88,
                  "package": 400, "link": 230}
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=13)
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor=tk.W)
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # 右键菜单
        self.menu = tk.Menu(self, tearoff=0, bg=DARK2, fg=FG,
                            activebackground=ACCENT, activeforeground="#fff")
        self.menu.add_command(label="复制手机号", command=lambda: self._copy_selected("phone"))
        self.menu.add_command(label="复制链接", command=lambda: self._copy_selected("link"))
        self.menu.add_separator()
        self.menu.add_command(label="查看号码可办套餐", command=self._show_packages)
        self.tree.bind("<Button-3>", self._show_menu)

    # ---- 省份/城市 ----

    def load_provinces(self):
        self.load_prov_btn.set_enabled(False)
        self.app.set_status("正在加载省份...")

        def worker():
            try:
                provinces = self.app.fetcher.get_provinces()
                self.app.log("ok", f"省份加载完成，共 {len(provinces)} 个")
                values = [f"{p['name']} ({p['code']})" for p in provinces]
                self.app.post( lambda: self._fill_provinces(values))
            except Exception as e:
                self.app.log("error", f"省份加载失败: {e}")
                self.app.post( lambda: self.load_prov_btn.set_enabled(True))

        threading.Thread(target=worker, daemon=True).start()

    def _fill_provinces(self, values):
        self.province_combo["values"] = values
        self.load_prov_btn.set_enabled(True)
        self.app.set_status(f"就绪（{len(values)} 个省份）")

    def _on_province_selected(self, _event=None):
        province = self.province_combo.get()
        self.city_combo.set("")
        if not province:
            self.city_combo["values"] = []
            return
        code = province.split("(")[-1].rstrip(")")
        self.app.set_status(f"加载 {province.split('(')[0]} 城市...")

        def worker():
            try:
                cities = self.app.fetcher.get_cities(code)
                values = [f"{c['name']} ({c['code']})" for c in cities]
                self.app.post( lambda: self.city_combo.configure(values=values))
                self.app.post( lambda: self.app.set_status("就绪"))
            except Exception as e:
                self.app.log("error", f"城市加载失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ---- 查询 ----

    def start_search(self):
        if self.searching:
            return
        province = self.province_combo.get()
        if not province:
            messagebox.showwarning("提示", "请先选择省份（或点“加载省份”）")
            return
        province_code = province.split("(")[-1].rstrip(")")
        city_code = ""
        city = self.city_combo.get()
        if city:
            city_code = city.split("(")[-1].rstrip(")")

        rank = self.rank_combo.get().strip()
        series = SERIES_MAP.get(self.series_combo.get(), "h5")
        try:
            max_pages = int(self.max_pages_var.get())
            interval = float(self.interval_var.get())
        except ValueError:
            messagebox.showwarning("提示", "页数和间隔必须是数字")
            return

        self.searching = True
        self.stop_flag = False
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self.export_btn.set_enabled(False)
        self.result_count = 0

        def worker():
            self.app.log("info", "正在校验渠道二维码...")
            if not self.app.fetcher.get_qr_code():
                self.app.log("error", "二维码校验失败，请检查渠道码是否有效")
                self.app.post( self._search_done)
                return
            page = 1
            while self.searching and not self.stop_flag:
                if max_pages and page > max_pages:
                    self.app.log("info", f"达到最大页数 {max_pages}，停止")
                    break
                try:
                    numbers = self.app.fetcher.get_phone_numbers(
                        page, province_code, city_code, rank, series)
                except Exception as e:
                    self.app.log("error", f"第 {page} 页请求异常: {e}")
                    break
                if not numbers:
                    self.app.log("info", f"第 {page} 页无数据，查询结束")
                    break
                self.app.post(lambda: self._append_rows(numbers))
                self.app.log("ok", f"第 {page} 页完成，累计 {self.result_count} 条")
                page += 1
                if interval > 0 and self.searching and not self.stop_flag:
                    time.sleep(interval)
            self.app.post( self._search_done)

        threading.Thread(target=worker, daemon=True).start()

    def _append_rows(self, numbers):
        for n in numbers:
            self.tree.insert("", tk.END, values=(
                n["index"], n["phone_number"], n["province"], n["city"],
                n["package"], n["link"]
            ))
        self.result_count += len(numbers)
        self.app.set_status(f"已获取 {self.result_count} 条")

    def stop_search(self):
        self.stop_flag = True
        self.app.set_status("正在停止...")

    def _search_done(self):
        self.searching = False
        self.start_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        if self.result_count:
            self.export_btn.set_enabled(True)
        self.app.set_status(f"完成，共 {self.result_count} 条")

    # ---- 套餐查看（右键） ----

    def _show_packages(self):
        item = self.tree.selection()
        if not item:
            return
        phone = self.tree.item(item[0], "values")[1]
        self.app.set_status(f"查询 {phone} 可办套餐...")

        def worker():
            try:
                packages = self.app.fetcher.get_products_for_number(phone)
                self.app.post( lambda: self._show_packages_result(phone, packages))
            except Exception as e:
                self.app.log("error", f"套餐查询失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_packages_result(self, phone, packages):
        if not packages:
            self.app.log("info", f"{phone} 无可办套餐")
            self.app.set_status("就绪")
            return
        win = tk.Toplevel(self)
        win.title(f"{phone} 可办套餐")
        win.configure(bg=DARK)
        win.geometry("780x460")
        tree = ttk.Treeview(win, columns=("name", "fee", "code", "desc"), show="headings")
        tree.heading("name", text="套餐名称")
        tree.heading("fee", text="费用(分)")
        tree.heading("code", text="产品编码")
        tree.heading("desc", text="说明")
        tree.column("name", width=210)
        tree.column("fee", width=80, anchor=tk.CENTER)
        tree.column("code", width=110)
        tree.column("desc", width=360)
        tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        for p in packages:
            tree.insert("", tk.END, values=(
                p.get("productName", ""), p.get("productFee", ""),
                p.get("productCode", ""), p.get("serviceDesc", ""),
            ))
        self.app.log("ok", f"{phone} 可办套餐 {len(packages)} 个")
        self.app.set_status("就绪")

    # ---- 导出 / 其他 ----

    def export_excel(self):
        rows = []
        for item in self.tree.get_children():
            rows.append(self.tree.item(item, "values"))
        if not rows:
            messagebox.showwarning("提示", "没有可导出的数据")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx"), ("CSV 文件", "*.csv")])
        if not path:
            return
        try:
            if path.endswith(".csv"):
                import csv
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["序号", "手机号", "省份", "城市", "套餐", "链接"])
                    writer.writerows(rows)
            else:
                import pandas as pd
                df = pd.DataFrame(rows, columns=["序号", "手机号", "省份", "城市", "套餐", "链接"])
                df.to_excel(path, index=False)
            messagebox.showinfo("导出成功", f"已导出 {len(rows)} 条到:\n{path}")
        except ImportError:
            messagebox.showerror("错误", "缺少 pandas/openpyxl，请先安装:\npip install pandas openpyxl")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def clear_table(self):
        self.tree.delete(*self.tree.get_children())
        self.result_count = 0
        self.export_btn.set_enabled(False)
        self.app.set_status("已清空")

    def _show_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _copy_selected(self, column):
        item = self.tree.selection()
        if not item:
            return
        values = self.tree.item(item[0], "values")
        idx = {"phone": 1, "link": 5}[column]
        self.clipboard_clear()
        self.clipboard_append(values[idx])
        self.app.set_status("已复制到剪贴板")


class FlowPage(tk.Frame):
    """页签2：流量专区（third 系列）"""

    def __init__(self, master, app):
        super().__init__(master, bg=CARD_BG)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        top = tk.Frame(self, bg=CARD_BG)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.load_cat_btn = GlassButton(top, "加载分类", self.load_categories, size=(96, 30), c1=BTN2_C1, c2=BTN2_C2)
        self.load_cat_btn.pack(side=tk.LEFT)
        tk.Label(top, text="分类:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT, padx=(12, 4))
        self.category_combo = ttk.Combobox(top, width=18, state="readonly")
        self.category_combo.pack(side=tk.LEFT, padx=(0, 12))
        self.category_combo.bind("<<ComboboxSelected>>", lambda e: self.load_products())
        self.load_prod_btn = GlassButton(top, "加载产品", self.load_products, size=(96, 30))
        self.load_prod_btn.pack(side=tk.LEFT, padx=(0, 12))
        self.kd_btn = GlassButton(top, "物流方式", self.show_kd, size=(96, 30), c1="#5b8ed8", c2="#2c4a7a")
        self.kd_btn.pack(side=tk.LEFT)
        tk.Label(top, text="提示：双击产品行查看详情", bg=CARD_BG, fg="#8f7fb8", font=FONT_S).pack(side=tk.LEFT, padx=(14, 0))

        # 产品表格
        table_frame = tk.Frame(self, bg=CARD_BG)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 6))
        columns = ("id", "name", "price", "unit", "category", "num", "desc")
        headers = {"id": "产品ID", "name": "产品名称", "price": "价格(分)",
                   "unit": "计费单位", "category": "分类", "num": "库存", "desc": "说明"}
        widths = {"id": 62, "name": 230, "price": 76, "unit": 66,
                  "category": 96, "num": 62, "desc": 400}
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor=tk.W)
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _e: self.show_product_detail())

        # 详情区
        detail = tk.Frame(self, bg=CARD_BG)
        detail.pack(fill=tk.X, padx=8, pady=(0, 8))
        tk.Label(detail, text="产品详情", bg=CARD_BG, fg=FG_PINK, font=(FONT[0], 9, "bold")).pack(anchor="w")
        self.detail_text = tk.Text(detail, height=5, font=("Consolas", 9),
                                   bg=DARK, fg=FG, relief=tk.FLAT, padx=6, pady=4)
        self.detail_text.pack(fill=tk.X, pady=(2, 0))

    def load_categories(self):
        self.load_cat_btn.set_enabled(False)
        self.app.set_status("加载流量产品分类...")

        def worker():
            try:
                cats = self.app.fetcher.get_categories()
                self.app.post( lambda: self._fill_categories(cats))
            except Exception as e:
                self.app.log("error", f"分类加载失败: {e}")
                self.app.post( lambda: self.load_cat_btn.set_enabled(True))

        threading.Thread(target=worker, daemon=True).start()

    def _fill_categories(self, cats):
        self.load_cat_btn.set_enabled(True)
        if not cats:
            self.app.log("error", "未获取到分类数据")
            return
        self.categories = cats
        self.category_combo["values"] = [c.get("name", "") for c in cats]
        self.category_combo.set(cats[0].get("name", ""))
        self.app.log("ok", f"加载分类 {len(cats)} 个: " + " / ".join(c.get("name", "") for c in cats))
        self.app.set_status("就绪")
        self.load_products()

    def load_products(self):
        name = self.category_combo.get()
        if not name or not getattr(self, "categories", None):
            return
        cat = next((c for c in self.categories if c.get("name") == name), None)
        if not cat:
            return
        self.load_prod_btn.set_enabled(False)
        self.app.set_status(f"加载「{name}」产品...")

        def worker():
            try:
                products = self.app.fetcher.get_products(cat["id"])
                self.app.post( lambda: self._fill_products(products))
            except Exception as e:
                self.app.log("error", f"产品加载失败: {e}")
                self.app.post( lambda: self.load_prod_btn.set_enabled(True))

        threading.Thread(target=worker, daemon=True).start()

    def _fill_products(self, products):
        self.load_prod_btn.set_enabled(True)
        self.tree.delete(*self.tree.get_children())
        if not products:
            self.app.log("info", "该分类下暂无产品")
            return
        for p in products:
            self.tree.insert("", tk.END, values=(
                p.get("id", ""), p.get("name", ""), p.get("price", ""),
                p.get("priceUnit", ""), p.get("categoryName", ""),
                p.get("num", ""), p.get("limitDesc") or p.get("desc", ""),
            ))
        self.app.log("ok", f"产品 {len(products)} 个（双击行查看详情）")
        self.app.set_status("就绪")

    def show_product_detail(self):
        item = self.tree.selection()
        if not item:
            messagebox.showwarning("提示", "请先选择产品")
            return
        product_id = self.tree.item(item[0], "values")[0]
        self.app.set_status(f"加载产品详情 (id={product_id})...")

        def worker():
            try:
                info = self.app.fetcher.get_product_info(product_id)
                self.app.post( lambda: self._show_detail(info))
            except Exception as e:
                self.app.log("error", f"产品详情失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_detail(self, info):
        if not info:
            self.app.log("info", "产品详情为空")
            self.app.set_status("就绪")
            return
        lines = [
            f"名称    : {info.get('name', '')}",
            f"价格    : {info.get('price', '')} 分 / {info.get('priceUnit', '')}",
            f"编码    : {info.get('code', '')}（分类 {info.get('categoryName', '')}）",
            f"流量    : {info.get('dataNum', '')} / 语音 {info.get('voiceNum', '')} / 通用 {info.get('gendataNum', '')}",
            f"简介    : {info.get('desc', '')}",
            f"服务说明: {info.get('serviceDesc', '')}",
            f"图片    : {info.get('bannerImg', '')}",
        ]
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, "\n".join(lines))
        self.detail_text.config(state=tk.DISABLED)
        self.app.log("ok", f"产品详情加载成功: {info.get('name', '')}")
        self.app.set_status("就绪")

    def show_kd(self):
        def worker():
            try:
                kd = self.app.fetcher.get_kd()
                names = [f"{k.get('name', '')}({k.get('fee', '')}分)" for k in kd]
                self.app.log("ok", "物流方式: " + ("、".join(names) if names else "无"))
            except Exception as e:
                self.app.log("error", f"物流查询失败: {e}")

        threading.Thread(target=worker, daemon=True).start()


class OrderPage(tk.Frame):
    """页签3：订单查询"""

    def __init__(self, master, app):
        super().__init__(master, bg=CARD_BG)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        top = tk.Frame(self, bg=CARD_BG)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(top, text="证件号:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.cardno_entry = ttk.Entry(top, width=24)
        self.cardno_entry.pack(side=tk.LEFT, padx=(4, 14))
        tk.Label(top, text="手机号:", bg=CARD_BG, fg=FG, font=FONT_S).pack(side=tk.LEFT)
        self.phone_entry = ttk.Entry(top, width=16)
        self.phone_entry.pack(side=tk.LEFT, padx=(4, 14))
        self.query_btn = GlassButton(top, "查询订单", self.query_orders, size=(104, 30))
        self.query_btn.pack(side=tk.LEFT)
        tk.Label(top, text="提示：证件号为下单时填写（新用户未绑证件可留空试查）",
                 bg=CARD_BG, fg="#8f7fb8", font=FONT_S).pack(side=tk.LEFT, padx=(14, 0))

        # 订单表格
        table_frame = tk.Frame(self, bg=CARD_BG)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))
        columns = ("no", "msisdn", "status", "name", "price", "time", "addr")
        headers = {"no": "订单号", "msisdn": "手机号", "status": "状态",
                   "name": "产品", "price": "金额(分)", "time": "时间", "addr": "收货地址"}
        widths = {"no": 140, "msisdn": 110, "status": 66, "name": 170,
                  "price": 76, "time": 130, "addr": 260}
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=13)
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor=tk.W)
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

    def query_orders(self):
        cardno = self.cardno_entry.get().strip()
        phone = self.phone_entry.get().strip()
        if not cardno and not phone:
            messagebox.showwarning("提示", "请至少填写证件号或手机号")
            return
        self.query_btn.set_enabled(False)
        self.app.set_status("查询订单...")

        def worker():
            try:
                orders = self.app.fetcher.get_orders(cardno, phone)
                self.app.post( lambda: self._fill_orders(orders))
            except Exception as e:
                self.app.log("error", f"订单查询失败: {e}")
                self.app.post( lambda: self.query_btn.set_enabled(True))

        threading.Thread(target=worker, daemon=True).start()

    def _fill_orders(self, orders):
        self.query_btn.set_enabled(True)
        self.tree.delete(*self.tree.get_children())
        if not orders:
            self.app.log("info", "没有查询到订单")
            self.app.set_status("就绪")
            return
        for o in orders:
            self.tree.insert("", tk.END, values=(
                o.get("orderCode", "") or o.get("sId", ""),
                o.get("msisdn", ""),
                o.get("orderStatus", "") or o.get("status", ""),
                o.get("productName", "") or o.get("name", ""),
                o.get("orderAmount", "") or o.get("amount", ""),
                o.get("createTime", "") or o.get("orderTime", ""),
                o.get("receiverAddr", "") or o.get("addr", ""),
            ))
        self.app.log("ok", f"查询到订单 {len(orders)} 条")
        self.app.set_status(f"完成，共 {len(orders)} 条订单")


class AnimeApp(tk.Tk):
    """主窗口：二次元背景 + 毛玻璃卡片布局"""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1150x760")
        self.minsize(960, 620)
        self.configure(bg=CARD_BG)

        self.fetcher = PhoneNumberFetcher()
        self.log_queue = queue.Queue()
        self._ui_calls = queue.Queue()   # 后台线程 → 主线程任务队列
        self._photos = []          # PhotoImage 引用池（防 GC）
        self._bg_orig = None       # 当前背景原图（网络/程序化）
        self._resize_job = None
        self._bg_thread = None
        self._current_tab = 0

        self._setup_style()
        self._build_ui()
        self._poll_log()
        self._poll_ui()

        # 背景：先用纯色占位，后台线程程序化生成 → 再拉网络随机图
        self.update_idletasks()
        self._win_w, self._win_h = self.winfo_width(), self.winfo_height()
        self._bg_orig = Image.new("RGB", (self._win_w, self._win_h), (24, 16, 44))
        self._place_all()
        self._redraw()
        self._start_bg_loader()
        self.bind("<Configure>", self._on_resize)

    # ---------------- 样式 ----------------

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=DARK, fieldbackground=DARK, foreground=FG,
                        borderwidth=0, rowheight=25, font=FONT_S)
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=DARK2, foreground=FG_PINK,
                        font=(FONT[0], 9, "bold"), borderwidth=0, relief=tk.FLAT)
        style.map("Treeview.Heading", background=[("active", "#2a2050")])
        style.configure("TCombobox", fieldbackground=DARK, background=DARK2, foreground=FG,
                        bordercolor=DARK2, lightcolor=DARK2, darkcolor=DARK2,
                        arrowcolor=FG_PINK, padding=3)
        style.map("TCombobox", fieldbackground=[("readonly", DARK)])
        style.configure("TEntry", fieldbackground=DARK, foreground=FG,
                        bordercolor=DARK2, lightcolor=DARK2, darkcolor=DARK2, padding=3)
        style.configure("TSpinbox", fieldbackground=DARK, background=DARK2, foreground=FG,
                        bordercolor=DARK2, lightcolor=DARK2, darkcolor=DARK2,
                        arrowcolor=FG_PINK, padding=2)
        style.configure("Vertical.TScrollbar", background=DARK2, troughcolor=DARK,
                        bordercolor=DARK, arrowcolor=FG_PINK, width=12)
        style.configure("Horizontal.TScrollbar", background=DARK2, troughcolor=DARK,
                        bordercolor=DARK, arrowcolor=FG_PINK, width=12)

    # ---------------- 布局（place 绝对坐标，随窗口重算） ----------------

    def _build_ui(self):
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg="#181230")
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # 标题文字（Canvas 上）
        self.title_var = tk.StringVar(value=f"🌸 {APP_TITLE}")
        self.status_var = tk.StringVar(value="就绪 · 背景加载中...")
        self.bg_label_var = tk.StringVar(value="背景: 原神随机图")

        # 标题卡片控件（place 在玻璃卡上）
        self.title_text = tk.Label(self, textvariable=self.title_var, bg=CARD_BG, fg="#ffffff",
                                   font=("Microsoft YaHei UI", 13, "bold"))
        self.status_text = tk.Label(self, textvariable=self.status_var, bg=CARD_BG, fg="#c9b8ef",
                                    font=FONT_S)
        self.bg_label = tk.Label(self, textvariable=self.bg_label_var, bg=CARD_BG, fg="#8f7fb8",
                                 font=(FONT_S[0], 8))
        self.change_bg_btn = GlassButton(self, "🔄 换背景", self.change_bg, size=(100, 30))

        # 页签按钮
        self.tab_btns = []
        for i, name in enumerate(["靓号查询", "流量专区", "订单查询"]):
            b = TabButton(self, name, lambda i=i: self.switch_tab(i), size=(112, 30))
            b.place_forget()
            self.tab_btns.append(b)
        self.tab_btns[0].set_active(True)

        # 内容页
        self.content = tk.Frame(self, bg=CARD_BG)
        self.pages = [
            LiangHaoPage(self.content, self),
            FlowPage(self.content, self),
            OrderPage(self.content, self),
        ]
        for p in self.pages:
            p.place(x=0, y=0, relwidth=1, relheight=1)
        self._show_page(0)

        # 日志卡片
        self.log_text = tk.Text(self, height=6, state=tk.DISABLED,
                                font=("Consolas", 9), bg=DARK, fg=FG,
                                relief=tk.FLAT, padx=6, pady=4)

    def _place_all(self):
        """按当前窗口尺寸摆放所有控件（与玻璃卡坐标一致）"""
        w, h = self._win_w, self._win_h
        # 标题卡
        self.title_text.place(x=30, y=18)
        self.status_text.place(x=30, y=42)
        self.bg_label.place(x=w - 300, y=14)
        self.change_bg_btn.place(x=w - 150, y=16)
        # 页签按钮
        for i, b in enumerate(self.tab_btns):
            b.place(x=28 + i * 124, y=74)
        # 内容卡
        self.content.place(x=16, y=112, width=w - 32, height=h - 238)
        # 日志卡
        self.log_text.place(x=16, y=h - 118, width=w - 32, height=104)

    def _show_page(self, idx):
        for i, p in enumerate(self.pages):
            p.place_forget() if i != idx else p.place(x=0, y=0, relwidth=1, relheight=1)
        for i, b in enumerate(self.tab_btns):
            b.set_active(i == idx)

    def switch_tab(self, idx):
        self._current_tab = idx
        self._show_page(idx)

    # ---------------- 背景 - 绘制 ----------------

    def _start_bg_loader(self):
        """后台线程：程序化兜底背景 → 网络随机图（每次启动不同）"""

        def worker():
            # 1) 程序化背景先显示
            gen = anime_bg.generate_anime_bg()
            self.post( lambda: self._apply_bg(gen, "程序化生成（兜底）"))
            # 2) 网络随机图替换
            img = anime_bg.fetch_random_bg(
                on_error=lambda u, e: self.post( lambda: self.log("info", f"背景源失败 {u}: {type(e).__name__}")))
            if img:
                self.post( lambda: self._apply_bg(img, "随机二次元图"))
            else:
                self.post( lambda: self.log("info", "所有背景接口不可用，使用程序化背景"))

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()

    def change_bg(self):
        """点击换背景：重新拉一张随机图（每次不同）"""
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self.set_status("正在更换背景...")

        def worker():
            img = anime_bg.fetch_random_bg(
                on_error=lambda u, e: self.post( lambda: self.log("info", f"背景源失败 {u}: {type(e).__name__}")))
            if img:
                self.post( lambda: self._apply_bg(img, "随机二次元图"))
                self.post( lambda: self.log("ok", "背景已更换"))
            else:
                self.post( lambda: self.log("error", "换背景失败：所有接口不可用"))

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()

    def _apply_bg(self, img, label):
        self._bg_orig = img
        self.bg_label_var.set(f"背景: {label}")
        self._redraw()

    def _on_resize(self, event):
        if event.widget is not self:
            return
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(220, self._redraw)

    def _redraw(self):
        self._resize_job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        self._win_w, self._win_h = w, h

        # 背景 cover 铺满
        bg = anime_bg.cover_resize(self._bg_orig, w, h)
        self._photos = [ImageTk.PhotoImage(bg)]
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photos[0], anchor="nw")

        # 玻璃卡片
        boxes = [
            (12, 10, w - 12, 64),          # 标题卡
            (12, 68, w - 12, 106),         # 页签卡
            (12, 110, w - 12, h - 124),    # 内容卡
            (12, h - 116, w - 12, h - 12), # 日志卡
        ]
        for box in boxes:
            glass = anime_bg.make_glass_layer(bg, box)
            if glass is None:
                continue
            photo = ImageTk.PhotoImage(glass)
            self._photos.append(photo)
            self.canvas.create_image(box[0], box[1], image=photo, anchor="nw")

        self._place_all()
        self.canvas.tag_lower("all")  # 背景在下，控件（独立 widget）自然在上

    # ---------------- 日志 / 状态 ----------------

    def post(self, fn):
        """主线程安全调度：后台线程把 fn 排队，由 UI 线程轮询执行（tkinter 禁止跨线程调 Tk API）"""
        self._ui_calls.put(fn)

    def _poll_ui(self):
        try:
            while True:
                self._ui_calls.get_nowait()()
        except queue.Empty:
            pass
        self.after(200, self._poll_ui)

    def log(self, level, msg):
        self.log_queue.put((level, msg))

    def set_status(self, text):
        self.status_var.set(text)

    def _poll_log(self):
        try:
            while True:
                level, msg = self.log_queue.get_nowait()
                color = {"info": "#c9b8ef", "ok": "#ffb7d9", "error": "#ff8fa3"}.get(level, "#c9b8ef")
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n", (level,))
                self.log_text.tag_config(level, foreground=color)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.after(200, self._poll_log)

    def on_close(self):
        self.pages[0].stop_flag = True
        self.destroy()


if __name__ == "__main__":
    app = AnimeApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
