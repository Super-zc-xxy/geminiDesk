import math
from PIL import Image, ImageDraw

def create_gradient_icon():
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. 绘制带有圆角的大背景（macOS / Win11 风格的深色科技圆角矩形）
    margin = 12
    corner_radius = 54
    rect_box = [margin, margin, size - margin, size - margin]

    # 创建背景渐变蒙版
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(margin, size - margin):
        ratio = (y - margin) / (size - 2 * margin)
        # 顶部深蓝 #1e1b4b -> 底部天青蓝 #0f172a 混合电光紫 #312e81
        r = int(15 + ratio * (30 - 15))
        g = int(23 + ratio * (58 - 23))
        b = int(42 + ratio * (138 - 42))
        for x in range(margin, size - margin):
            bg.putpixel((x, y), (r, g, b, 255))

    # 圆角蒙版
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(rect_box, radius=corner_radius, fill=255)

    img.paste(bg, (0, 0), mask)

    # 绘制外边框微光 (Border glow)
    draw.rounded_rectangle(rect_box, radius=corner_radius, outline=(96, 165, 250, 90), width=3)

    # 2. 绘制中心主体：极简发光的“API 能量双向互联 / 闪电核心”
    # 绘制能量光环 (Outer glow ring)
    center = (128, 128)
    glow_r = 62
    for gr in range(glow_r + 8, glow_r - 2, -2):
        alpha = int(45 * (1.0 - (gr - glow_r + 2) / 10.0))
        draw.ellipse(
            [center[0] - gr, center[1] - gr, center[0] + gr, center[1] + gr],
            outline=(56, 189, 248, max(5, alpha)),
            width=2
        )

    # 绘制核心闪电形状 (Lightning Bolt with clean sharp geometry)
    # 顶点坐标设计
    bolt_points = [
        (134, 52),   # 顶点
        (94, 132),   # 左中凹陷
        (124, 132),  # 中间内收
        (116, 204),  # 底部尖端
        (164, 118),  # 右中凸起
        (134, 118)   # 右中内收
    ]

    # 闪电外发光
    for offset in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
        shifted = [(x + offset[0] * 2, y + offset[1] * 2) for x, y in bolt_points]
        draw.polygon(shifted, fill=(59, 130, 246, 70))

    # 闪电渐变填充 (金黄与电光天青融合，象征能量与连接)
    draw.polygon(bolt_points, fill=(56, 189, 248, 255))

    # 闪电高光条
    inner_bolt = [
        (132, 64),
        (104, 126),
        (128, 126),
        (122, 186),
        (152, 124),
        (132, 124)
    ]
    draw.polygon(inner_bolt, fill=(240, 249, 255, 220))

    # 绘制代表 API 节点的两个环绕粒子
    p1 = (82, 92)
    p2 = (174, 164)
    draw.ellipse([p1[0]-6, p1[1]-6, p1[0]+6, p1[1]+6], fill=(245, 158, 11, 240))
    draw.ellipse([p2[0]-6, p2[1]-6, p2[0]+6, p2[1]+6], fill=(56, 189, 248, 240))

    # 导出多尺寸 ICO
    icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(r"E:\GeminiDeskTop\app.ico", format="ICO", sizes=icon_sizes)
    print("Icon generated successfully at E:\\GeminiDeskTop\\app.ico")

if __name__ == "__main__":
    create_gradient_icon()
