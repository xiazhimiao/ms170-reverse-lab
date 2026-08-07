# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico：紫粉渐变圆角底 + 白色手机 + 金色"8"（与 GUI 毛玻璃配色一致）"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont

S = 512
yy, xx = np.mgrid[0:S, 0:S]
cx, cy = S * 0.30, S * 0.28
d = np.clip(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2), 0, S * 1.6)
top = np.array([255, 168, 178])
mid = np.array([168, 96, 180])
bot = np.array([48, 26, 88])

t1 = np.clip(d / (S * 0.55), 0, 1)
t2 = np.clip((d - S * 0.55) / (S * 0.9), 0, 1)
t1w = (t1 < 1).astype(float)
r = top[0] * t1w + mid[0] * (1 - t1w) + (mid[0] - top[0]) * np.minimum(t1, 1) * t1w + (bot[0] - mid[0]) * t2 * (1 - t1w)
g = top[1] * t1w + mid[1] * (1 - t1w) + (mid[1] - top[1]) * np.minimum(t1, 1) * t1w + (bot[1] - mid[1]) * t2 * (1 - t1w)
b = top[2] * t1w + mid[2] * (1 - t1w) + (mid[2] - top[2]) * np.minimum(t1, 1) * t1w + (bot[2] - mid[2]) * t2 * (1 - t1w)
img = Image.fromarray(np.stack([r, g, b], -1).astype(np.uint8), "RGB")

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=112, fill=255)
img.putalpha(mask)

dr = ImageDraw.Draw(img, "RGBA")
# 白色圆角手机机身 + 浅紫屏幕
dr.rounded_rectangle([S * 0.30, S * 0.16, S * 0.70, S * 0.88], radius=46, fill=(255, 255, 255, 245))
dr.rounded_rectangle([S * 0.345, S * 0.22, S * 0.655, S * 0.78], radius=20, fill=(245, 235, 250, 255))
dr.rounded_rectangle([S * 0.44, S * 0.185, S * 0.56, S * 0.20], radius=6, fill=(220, 205, 230, 255))
# 屏幕内金色 "8"（双层描出渐变感）
font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 250)
txt = "8"
bbox = dr.textbbox((0, 0), txt, font=font)
tx, ty = S * 0.5 - (bbox[2] - bbox[0]) / 2 - bbox[0], S * 0.47 - (bbox[3] - bbox[1]) / 2 - bbox[1]
dr.text((tx - 3, ty - 3), txt, font=font, fill=(255, 190, 90, 255))
dr.text((tx, ty), txt, font=font, fill=(255, 118, 38, 255))
# home 键
dr.rounded_rectangle([S * 0.47, S * 0.825, S * 0.53, S * 0.845], radius=8, fill=(220, 205, 230, 255))

img.convert("RGB").save("icon.ico", format="ICO",
                        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("icon.ico saved")
