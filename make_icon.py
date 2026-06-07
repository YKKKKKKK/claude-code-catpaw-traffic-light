"""
PawSignal 图标 - 标准红绿灯 macOS 风格
简洁：圆角方形背景 + 灯壳 + 三个实心彩色圆点
"""
from PIL import Image, ImageDraw, ImageFilter
import os

SIZE = 1024

def make_icon(size=SIZE):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 背景：macOS 风格渐变深色圆角方形
    bg_r = int(size * 0.225)
    # 底色
    draw.rounded_rectangle([0, 0, size, size], radius=bg_r,
                            fill=(30, 32, 36, 255))
    # 顶部稍微亮一点（模拟光感）
    top_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    td = ImageDraw.Draw(top_layer)
    td.rounded_rectangle([0, 0, size, size // 2], radius=bg_r,
                          fill=(255, 255, 255, 12))
    img = Image.alpha_composite(img, top_layer)
    draw = ImageDraw.Draw(img)

    # ── 灯壳外框（深灰色竖长圆角矩形，居中）
    shell_w = int(size * 0.36)
    shell_h = int(size * 0.74)
    shell_x0 = (size - shell_w) // 2
    shell_y0 = (size - shell_h) // 2
    shell_x1 = shell_x0 + shell_w
    shell_y1 = shell_y0 + shell_h
    shell_r  = int(size * 0.08)

    # 阴影
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([shell_x0 + 6, shell_y0 + 10,
                           shell_x1 + 6, shell_y1 + 10],
                          radius=shell_r, fill=(0, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    # 灯壳主体
    draw.rounded_rectangle([shell_x0, shell_y0, shell_x1, shell_y1],
                            radius=shell_r, fill=(52, 54, 60, 255))
    # 灯壳顶部高光边
    draw.rounded_rectangle([shell_x0, shell_y0, shell_x1, shell_y1],
                            radius=shell_r,
                            outline=(255, 255, 255, 28), width=2)

    # ── 三个灯槽（深色凹槽，让灯更立体）
    dot_r   = int(size * 0.099)
    cx      = size // 2
    pad_top = int(shell_h * 0.155)
    spacing = int(shell_h * 0.296)
    light_ys = [shell_y0 + pad_top + i * spacing for i in range(3)]

    for lcy in light_ys:
        slot_r = dot_r + int(size * 0.012)
        draw.ellipse([cx - slot_r, lcy - slot_r, cx + slot_r, lcy + slot_r],
                     fill=(28, 28, 32, 200))

    # ── 三盏灯（纯实心圆，无同心圆特效）
    colors = [
        (220,  50,  40),   # 红
        (230, 180,   0),   # 黄
        ( 40, 195,  80),   # 绿
    ]

    for lcy, color in zip(light_ys, colors):
        # 灯体
        draw.ellipse([cx - dot_r, lcy - dot_r, cx + dot_r, lcy + dot_r],
                     fill=(*color, 255))
        # 单个高光点（左上，简单椭圆，不模糊）
        hl_w = int(dot_r * 0.5)
        hl_h = int(dot_r * 0.28)
        hl_x = cx - int(dot_r * 0.2)
        hl_y = lcy - int(dot_r * 0.42)
        draw.ellipse([hl_x - hl_w // 2, hl_y - hl_h // 2,
                      hl_x + hl_w // 2, hl_y + hl_h // 2],
                     fill=(255, 255, 255, 130))

    # ── 最外边框（细微高光线）
    draw.rounded_rectangle([1, 1, size - 2, size - 2],
                            radius=bg_r - 1,
                            outline=(255, 255, 255, 22), width=1)

    return img


icon = make_icon()

os.makedirs("icon.iconset", exist_ok=True)
for sz in [16, 32, 64, 128, 256, 512, 1024]:
    icon.resize((sz, sz), Image.LANCZOS).save(f"icon.iconset/icon_{sz}x{sz}.png")
    if sz <= 512:
        icon.resize((sz * 2, sz * 2), Image.LANCZOS).save(
            f"icon.iconset/icon_{sz}x{sz}@2x.png")

icon.save("icon_preview.png")
print("图标生成完成！")
