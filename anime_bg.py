# -*- coding: utf-8 -*-
"""二次元背景图模块（GUI v4 用）

- fetch_random_bg(): 随机二次元图接口多源拉取（uapis → alcy → dmoe → imgapi）
- generate_anime_bg(): 程序化二次元壁纸（晚霞渐变+光晕+星空+远山+樱花），接口全挂时兜底
- cover_resize(): 等比缩放居中裁剪
- make_glass_layer(): 毛玻璃卡片合成（背景裁剪→高斯模糊→暗色罩→圆角→描边）

2026-08-07 实测：uapis(0.4s)/alcy(0.5s)/dmoe(6.8s) 均为 1920x1080 高清且每次调用返回不同图。
"""

import io
import math
import random
import threading
import time
import requests
from PIL import Image, ImageDraw, ImageFilter

# 随机二次元图接口（按速度排序，多源 fallback）
BG_SOURCES = [
    "https://t.alcy.cc/ys",            # 岁次元·原神（用户指定，实测 0.3-0.6s，4096x2371）
    "https://t.alcy.cc/moez",          # 岁次元·综合萌系（0.5s，6144x4096）
    "https://uapis.cn/api/v1/random/image?category=acg",
    "https://www.dmoe.cc/random.php",
    "https://imgapi.cn/api.php?zd=mobile",
]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 接口为 http 图床，verify=False 时抑制 SSL 警告噪音
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_session_obj = None


def _get_session():
    global _session_obj
    if _session_obj is None:
        _session_obj = requests.Session()
        _session_obj.trust_env = False  # 免疫系统残留代理
    return _session_obj


def fetch_random_bg(timeout: int = 15, on_error=None, total_budget: float = None):
    """按序尝试各随机图接口，返回 Image(RGB)；全部失败返回 None

    on_error: 可选回调，每次源失败时调用 on_error(url, exc)，用于 GUI 日志
    total_budget: 可选整体时间预算（秒）。requests 的 read timeout 是"空闲超时"
                  （慢速持续传输不会触发），多源串行最坏可能 timeout×源数；
                  传 budget 后在守护线程里拉图、join(budget) 到点即放弃，
                  防止慢/挂的图源把调用方拖死。
    """
    if not total_budget:
        return _fetch_all_bg(timeout, on_error)

    result = [None]

    def runner():
        result[0] = _fetch_all_bg(timeout, on_error)

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(timeout=total_budget)
    return result[0]


def _fetch_all_bg(timeout, on_error):
    for url in BG_SOURCES:
        try:
            r = _get_session().get(url, timeout=timeout, verify=False, headers=UA)
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            return img
        except Exception as e:
            if on_error:
                on_error(url, e)
            continue
    return None


def cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    """等比缩放 + 居中裁剪，铺满 (w, h)"""
    tw, th = img.size
    scale = max(w / tw, h / th)
    nw, nh = int(tw * scale + 0.5), int(th * scale + 0.5)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return img.crop((x, y, x + w, y + h))


def _petal_image(size: float, color, rng: random.Random) -> Image.Image:
    """绘制一片樱花花瓣（两圆并排+底部收尖），旋转随机角度"""
    s = max(int(size * 3), 9)
    pet = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pet)
    r = s * 0.28
    # 左右两圆并排成花瓣主体 + 底部小圆收尖
    pd.ellipse([s * 0.18, s * 0.12, s * 0.18 + 2 * r, s * 0.12 + 2 * r], fill=color)
    pd.ellipse([s * 0.82 - 2 * r, s * 0.12, s * 0.82, s * 0.12 + 2 * r], fill=color)
    pd.ellipse([s * 0.36, s * 0.30, s * 0.64, s * 0.62], fill=color)
    pd.ellipse([s * 0.42, s * 0.52, s * 0.58, s * 0.92], fill=color)
    ang = rng.uniform(0, math.pi)
    pet = pet.rotate(math.degrees(ang), resample=Image.Resampling.BICUBIC, expand=True)
    return pet


def generate_anime_bg(w: int = 1920, h: int = 1080, seed: int = 20260807) -> Image.Image:
    """程序化二次元壁纸：晚霞渐变 + 太阳光晕 + 星空 + 远山剪影 + 樱花飘落

    接口全部失败时的兜底背景（也用作启动瞬间的占位背景）。
    """
    import numpy as np
    rng = random.Random(seed)

    # ---- 垂直渐变（numpy 向量化）：顶部金粉晚霞 → 中部紫粉 → 底部夜紫 ----
    top, mid, bot = np.array([255, 190, 160], float), np.array([150, 80, 140], float), np.array([26, 14, 42], float)
    midy = 0.42
    yy = np.linspace(0.0, 1.0, h)[:, None]
    row = np.where(yy <= midy, top + (mid - top) * (yy / midy),
                   mid + (bot - mid) * ((yy - midy) / (1 - midy)))
    row = row[:, None, :]              # (h, 3) -> (h, 1, 3)，避免通道被当作列
    col = np.repeat(row, w, axis=1)    # -> (h, w, 3)
    img = Image.fromarray(col.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img, "RGBA")

    # ---- 太阳光晕（多层径向叠加）----
    cx, cy = int(w * 0.72), int(h * 0.28)
    for r, a in [(300, 18), (210, 26), (130, 38), (72, 62)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 222, 190, a))

    # ---- 星空 ----
    for _ in range(150):
        x, y = rng.randrange(0, w), rng.randrange(0, int(h * 0.5))
        r = rng.uniform(0.5, 1.6)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rng.randint(80, 200)))

    # ---- 远山剪影（两层）----
    for base_y, amp, c in [(int(h * 0.70), 60, (44, 24, 70, 150)),
                           (int(h * 0.82), 50, (24, 13, 42, 175))]:
        pts = [(0, h)]
        for x in range(0, w + 90, 90):
            pts.append((x, base_y - rng.randint(-amp, amp)))
        pts.append((w, h))
        d.polygon(pts, fill=c)

    # ---- 樱花飘落 ----
    colors = [(255, 182, 193, 190), (255, 209, 220, 170), (250, 165, 190, 150)]
    for _ in range(75):
        x, y = rng.randrange(0, w), rng.randrange(0, h)
        pet = _petal_image(rng.uniform(3.5, 8.5), rng.choice(colors), rng)
        img.paste(pet, (int(x) - pet.size[0] // 2, int(y) - pet.size[1] // 2), pet)

    return img


# 毛玻璃卡片合成参数（深紫半透明罩；alpha 135 让背景明显透出，控件底色 #2a2150 与之匹配）
GLASS_TINT = (30, 22, 56, 135)


def make_glass_layer(bg: Image.Image, box, blur: int = 12, radius: int = 16,
                     tint: tuple = GLASS_TINT, border: tuple = (255, 255, 255, 70)) -> Image.Image:
    """把背景图 (bg) 的 (x0,y0,x1,y1) 区域合成为毛玻璃卡片图

    流程：裁剪 → 高斯模糊 → 深色半透明罩 → 圆角遮罩 → 高亮描边
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    crop = bg.crop((x0, y0, x1, y1))
    crop = crop.filter(ImageFilter.GaussianBlur(blur)).convert("RGBA")
    overlay = Image.new("RGBA", crop.size, tint)
    crop = Image.alpha_composite(crop, overlay)

    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, crop.size[0] - 1, crop.size[1] - 1],
                                           radius=radius, fill=255)
    crop.putalpha(mask)

    if border:
        edge = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        ImageDraw.Draw(edge).rounded_rectangle([0, 0, crop.size[0] - 1, crop.size[1] - 1],
                                               radius=radius, outline=border, width=2)
        crop = Image.alpha_composite(crop, edge)
    return crop


if __name__ == "__main__":
    import sys
    # 自测：生成预览图供人工/程序检查
    mode = sys.argv[1] if len(sys.argv) > 1 else "gen"
    if mode == "gen":
        bg = generate_anime_bg()
        bg.save("bg_preview_gen.png")
        print("generated bg_preview_gen.png", bg.size)
    else:
        img = fetch_random_bg()
        if img:
            cover = cover_resize(img, 1150, 760)
            box = (10, 66, 1140, 158)
            glass = make_glass_layer(cover, box)
            canvas = Image.new("RGB", (1150, 760), (0, 0, 0))
            canvas.paste(cover, (0, 0))
            canvas.paste(glass, (10, 66), glass)
            canvas.save("bg_preview_net.png")
            print("network bg_preview_net.png", img.size)
        else:
            print("network FAIL")
