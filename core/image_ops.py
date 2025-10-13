# -*- coding: utf-8 -*-
"""
常用图像操作（预留）。
后续我们会逐步把色相/亮度/描边/九宫格拉伸等功能塞进来。
"""
from PIL import Image, ImageFilter, ImageChops

def outline(img: Image.Image, px=3, color=(0,0,0,255)):
    """简单描边：对 alpha 做膨胀并上色，再与原图合成。"""
    a = img.split()[-1]                 # 提取 alpha 通道
    o = a
    for _ in range(px):
        o = o.filter(ImageFilter.MaxFilter(3))  # 膨胀 alpha
    edge = ImageChops.subtract(o, a)    # 得到“边框区域”
    rgba = Image.new("RGBA", img.size, color)
    rgba.putalpha(edge)
    return Image.alpha_composite(rgba, img)
