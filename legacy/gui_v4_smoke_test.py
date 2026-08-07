# -*- coding: utf-8 -*-
"""GUI v4 冒烟测试：窗口/背景/玻璃卡/页签/日志/换背景/缩放（数值验证，无需肉眼看图）"""
import sys
import time
import traceback

from PIL import ImageGrab

import anime_bg
import phone_number_gui as g

errors = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        errors.append(name)


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.03)


def main():
    print("== 1. 创建窗口 ==")
    app = g.AnimeApp()
    pump(app, 1.0)
    check("窗口标题", app.title() == g.APP_TITLE, app.title())
    w, h = app.winfo_width(), app.winfo_height()
    check("窗口尺寸 >= 最小值", w >= 960 and h >= 620, f"{w}x{h}")

    print("== 2. 背景加载（占位色被替换） ==")
    gen = anime_bg.generate_anime_bg()
    check("程序化背景函数输出 1920x1080", gen.size == (1920, 1080), str(gen.size))
    t0 = time.time()
    while time.time() - t0 < 20:
        pump(app, 0.2)
        if app._bg_orig is not None and app._bg_orig.size != (w, h):
            break
    check("窗口背景已非占位纯色", app._bg_orig.size != (w, h) and app._bg_orig.size[0] >= 1000,
          str(app._bg_orig.size))

    print("== 3. canvas 玻璃卡布局 ==")
    pump(app, 0.3)
    items = app.canvas.find_all()
    check("canvas 有背景+4张玻璃卡(>=5 items)", len(items) >= 5, f"{len(items)} items")

    # 截图像素验证
    app.update()
    x0, y0 = app.winfo_rootx(), app.winfo_rooty()
    shot = ImageGrab.grab((x0, y0, x0 + w, y0 + h)).convert("RGB")

    # 页签卡右侧空白区（无控件干扰）：(w-60, 88)
    tab_px = shot.getpixel((w - 60, 88))
    # 同点背景原色（cover 后）
    bg_cover = anime_bg.cover_resize(app._bg_orig, w, h)
    bg_px = bg_cover.getpixel((w - 60, 88))
    check("玻璃罩生效（卡片区颜色 ≠ 背景原色）", abs(tab_px[0] - bg_px[0]) + abs(tab_px[1] - bg_px[1]) + abs(tab_px[2] - bg_px[2]) > 20,
          f"glass={tab_px} bg={bg_px}")
    # 玻璃区平滑度：相邻两像素差应小（高斯模糊后）
    d = sum(abs(a - b) for a, b in zip(tab_px, shot.getpixel((w - 90, 88))))
    check("玻璃区平滑（模糊生效）", d < 30, f"diff={d}")
    # 角落应该是背景原图（玻璃卡外）
    corner = shot.getpixel((4, 4))
    bg_corner = bg_cover.getpixel((4, 4))
    d2 = sum(abs(a - b) for a, b in zip(corner, bg_corner))
    check("卡片外区域保留背景原图", d2 < 12, f"diff={d2}")

    print("== 4. 页签切换 ==")
    app.switch_tab(1)
    pump(app, 0.2)
    check("切到流量专区", app.pages[1].winfo_ismapped() and not app.pages[0].winfo_ismapped())
    app.switch_tab(2)
    pump(app, 0.2)
    check("切到订单查询", app.pages[2].winfo_ismapped())
    app.switch_tab(0)
    pump(app, 0.2)
    check("切回靓号查询", app.pages[0].winfo_ismapped())

    print("== 5. 日志通道 ==")
    app.log("ok", "冒烟测试日志")
    pump(app, 0.5)
    txt = app.log_text.get("1.0", "end")
    check("日志面板收到消息", "冒烟测试日志" in txt)

    print("== 6. 换背景（网络随机图，20s 等待） ==")
    app.change_bg()
    t0 = time.time()
    while time.time() - t0 < 20:
        pump(app, 0.2)
        if "随机二次元图" in app.bg_label_var.get():
            break
    check("换背景成功（接口拉取）", "随机二次元图" in app.bg_label_var.get(),
          app.bg_label_var.get())
    bg_after = app._bg_orig.size
    check("新背景为高清图", bg_after[0] >= 1000, str(bg_after))

    print("== 7. 窗口缩放（防抖重绘） ==")
    app.geometry("1000x640")
    pump(app, 1.0)  # 220ms 防抖已过
    items2 = app.canvas.find_all()
    check("缩放后重绘完成", len(items2) >= 5, f"{len(items2)} items")
    check("内容区位置随窗口更新", app.content.winfo_y() == 112, f"y={app.content.winfo_y()}")

    print("== 8. 控件状态 ==")
    check("换背景按钮可点", app.change_bg_btn._enabled)
    check("页签0激活", app.tab_btns[0]._active)

    app.pages[0].stop_flag = True
    app.destroy()
    print()
    if errors:
        print(f"FAILED {len(errors)}: {errors}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
