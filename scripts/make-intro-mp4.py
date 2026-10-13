#!/usr/bin/env python3
"""Render a 1080p intro from the real workbench screenshots plus a session-mode UI."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "website" / "assets"
OUT_MP4 = ASSETS / "edgesidekick-intro.mp4"

W, H = 1920, 1080
FPS = 30

# Durations in seconds.
SCENES = [
    ("brand", 2.5),
    ("workbench", 6.5),
    ("social", 6.5),
    ("both", 4.5),
    ("tools", 8.0),
    ("mcp", 6.5),
    ("arch", 8.0),
    ("cta", 3.5),
]

PAGE = (236, 241, 250)
WHITE = (255, 255, 255)
INK = (15, 23, 42)
MUTED = (100, 116, 139)
BLUE = (37, 99, 235)
BLUE_DEEP = (29, 78, 216)
LINE = (226, 232, 240)
SOFT = (241, 245, 249)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = (
        [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"]
        if bold
        else [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\arial.ttf"]
    )
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


F = {
    "hero": font(72, True),
    "h1": font(48, True),
    "h2": font(36, True),
    "h3": font(28, True),
    "title": font(24, True),
    "body": font(22),
    "small": font(18),
    "tiny": font(15),
    "badge": font(20, True),
}


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def text_size(draw: ImageDraw.ImageDraw, text: str, face) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=face)
    return box[2] - box[0], box[3] - box[1]


def center_text(draw, text, y, face, fill) -> None:
    tw, _ = text_size(draw, text, face)
    draw.text(((W - tw) // 2, y), text, font=face, fill=fill)


def load_shot(name: str) -> Image.Image:
    return Image.open(ASSETS / name).convert("RGB")


def cover(src: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    scale = max(bw / src.width, bh / src.height)
    nw, nh = int(src.width * scale), int(src.height * scale)
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - bw) // 2
    top = max(0, (nh - bh) // 2 - int(nh * 0.02))
    return resized.crop((left, top, left + bw, top + bh))


def contain_pad(src: Image.Image, bw: int, bh: int, bg) -> Image.Image:
    scale = min(bw / src.width, bh / src.height)
    nw, nh = int(src.width * scale), int(src.height * scale)
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (bw, bh), bg)
    canvas.paste(resized, ((bw - nw) // 2, (bh - nh) // 2))
    return canvas


def page() -> Image.Image:
    return Image.new("RGB", (W, H), PAGE)


def rounded(draw, xy, radius, fill, outline=None, width=1) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def caption_bar(img: Image.Image, title: str, subtitle: str) -> Image.Image:
    overlay = Image.new("RGBA", (W, 92), (15, 23, 42, 168))
    base = img.convert("RGBA")
    base.alpha_composite(overlay, (0, H - 92))
    out = base.convert("RGB")
    draw = ImageDraw.Draw(out)
    draw.text((36, H - 72), title, font=F["title"], fill=WHITE)
    tw, _ = text_size(draw, subtitle, F["small"])
    draw.text((W - 36 - tw, H - 66), subtitle, font=F["small"], fill=(226, 232, 240))
    return out


def render_brand(t: float) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    icon = Image.open(ASSETS / "brand-icon.png").convert("RGBA")
    size = 128
    icon = icon.resize((size, size), Image.Resampling.LANCZOS)
    img.paste(icon, ((W - size) // 2, 300), icon)
    draw = ImageDraw.Draw(img)
    a = ease(min(1.0, t / 0.6))
    ink = tuple(int(PAGE[i] + (INK[i] - PAGE[i]) * a) for i in range(3))
    center_text(draw, "EdgeSidekick", 460, F["hero"], ink if a > 0.2 else PAGE)
    center_text(draw, "本地多智能体 AI 工作台", 560, F["h3"], MUTED)
    center_text(draw, "工作台  ·  会话版  ·  工具  ·  MCP", 630, F["body"], BLUE)
    return img


def mode_card(draw, xy, title, desc, active: bool) -> None:
    x0, y0, x1, y1 = xy
    if active:
        rounded(draw, xy, 22, (239, 246, 255), BLUE, 3)
    else:
        rounded(draw, xy, 22, WHITE, LINE, 2)
    draw.text((x0 + 36, y0 + 36), title, font=F["h2"], fill=BLUE_DEEP if active else INK)
    draw.text((x0 + 36, y0 + 100), desc, font=F["body"], fill=MUTED)


def render_switch(t: float) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    center_text(draw, "同一套本地运行时，两种界面", 150, F["h1"], INK)
    center_text(draw, "设置 → 外观 → 界面模式", 220, F["body"], MUTED)
    active_social = t > 2.2
    gap = 36
    cw, ch = 760, 220
    x0 = (W - cw * 2 - gap) // 2
    y0 = 380
    mode_card(
        draw,
        (x0, y0, x0 + cw, y0 + ch),
        "工作台",
        "文件、搜索、浏览器、任务与多智能体协作",
        not active_social,
    )
    mode_card(
        draw,
        (x0 + cw + gap, y0, x0 + cw * 2 + gap, y0 + ch),
        "会话版",
        "会话列表、私聊与群协作，适合持续话题",
        active_social,
    )
    return img


def render_shot(t: float, shot: Image.Image, title: str, subtitle: str) -> Image.Image:
    z = 1.0 + 0.03 * ease(min(1.0, t / 6.0))
    bw, bh = int(W * z), int(H * z)
    frame = cover(shot, (0, 0, bw, bh))
    left = (bw - W) // 2
    top = (bh - H) // 2
    img = frame.crop((left, top, left + W, top + H))
    return caption_bar(img, title, subtitle)


def render_workbench(t: float, shot: Image.Image) -> Image.Image:
    return render_shot(t, shot, "工作台模式", "文件  ·  搜索  ·  浏览器  ·  任务  ·  Skills")


def avatar(draw, cx, cy, r, fill, letter) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    face = F["title"]
    tw, th = text_size(draw, letter, face)
    draw.text((cx - tw / 2, cy - th / 2 - 2), letter, font=face, fill=WHITE)


def bubble(draw, x, y, w, text, who, fill, name_color) -> int:
    face = F["body"]
    # wrap roughly by character count for CJK
    max_chars = max(12, int((w - 48) / 22))
    lines = []
    rest = text
    while rest:
        lines.append(rest[:max_chars])
        rest = rest[max_chars:]
    name_h = 28
    line_h = 32
    h = 24 + name_h + line_h * len(lines) + 16
    rounded(draw, (x, y, x + w, y + h), 18, fill, LINE, 1)
    draw.text((x + 22, y + 14), who, font=F["small"], fill=name_color)
    for i, line in enumerate(lines):
        draw.text((x + 22, y + 14 + name_h + i * line_h), line, font=face, fill=INK)
    return h


def render_social(t: float, with_caption: bool = True) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    # Top bar
    draw.rectangle((0, 0, W, 72), fill=WHITE)
    draw.line((0, 72, W, 72), fill=LINE, width=1)
    icon = Image.open(ASSETS / "brand-icon.png").convert("RGBA").resize((36, 36), Image.Resampling.LANCZOS)
    img.paste(icon, (28, 18), icon)
    draw = ImageDraw.Draw(img)
    draw.text((76, 22), "EdgeSidekick", font=F["title"], fill=INK)
    rounded(draw, (1680, 16, 1884, 56), 999, (239, 246, 255), BLUE, 2)
    draw.text((1704, 24), "会话版", font=F["badge"], fill=BLUE_DEEP)

    # Left conversation list
    rounded(draw, (24, 96, 560, 1040), 20, WHITE, LINE, 1)
    draw.text((48, 118), "会话", font=F["h3"], fill=INK)
    rounded(draw, (48, 172, 536, 220), 12, SOFT)
    draw.text((68, 184), "搜索会话、智能体…", font=F["small"], fill=MUTED)

    rows = [
        ((37, 99, 235), "产", "产品组", "协作 · 3 人", "把发布说明拆成清单"),
        ((14, 165, 233), "研", "研究员", "私聊", "本地数据默认不出境"),
        ((16, 185, 129), "工", "工程师", "私聊", "工作台负责改文件和命令"),
    ]
    y = 248
    for i, (color, letter, name, tag, preview) in enumerate(rows):
        if t < 0.2 + i * 0.25:
            continue
        bg = (239, 246, 255) if i == 0 else SOFT
        rounded(draw, (48, y, 536, y + 108), 16, bg)
        avatar(draw, 92, y + 54, 26, color, letter)
        draw.text((136, y + 22), name, font=F["title"], fill=INK)
        draw.text((136, y + 58), f"{tag}  ·  {preview}", font=F["small"], fill=MUTED)
        y += 124

    # Right thread
    rounded(draw, (584, 96, 1896, 1040), 20, WHITE, LINE, 1)
    draw.text((616, 118), "产品组", font=F["h3"], fill=INK)
    draw.text((760, 126), "大家讨论  ·  @成员派活", font=F["small"], fill=MUTED)
    draw.line((616, 176, 1864, 176), fill=LINE, width=1)

    messages = [
        (0.8, 616, 820, "你", "会话版和工作台怎么选？不要调用工具。", (239, 246, 255), BLUE_DEEP),
        (2.2, 616, 820, "研究员", "会话版适合日常问答和持续话题，私聊、群协作都在这里。", WHITE, (14, 165, 233)),
        (4.0, 616, 820, "工程师", "要改文件、跑命令、看浏览器沙箱，切到工作台。两边共用同一套模型和本地数据。", WHITE, (16, 185, 129)),
    ]
    my = 210
    for at, x, bw, who, text, fill, color in messages:
        if t < at:
            continue
        h = bubble(draw, x, my, bw, text, who, fill, color)
        my += h + 18

    # Composer
    rounded(draw, (616, 900, 1864, 1008), 16, SOFT, LINE, 1)
    draw.text((640, 924), "发消息，或 @成员 派活…", font=F["body"], fill=MUTED)
    rounded(draw, (1688, 936, 1840, 984), 12, BLUE)
    label = "发送"
    tw, th = text_size(draw, label, F["badge"])
    draw.text((1688 + (152 - tw) / 2, 936 + (48 - th) / 2 - 1), label, font=F["badge"], fill=WHITE)

    if not with_caption:
        return img
    return caption_bar(img, "会话版", "会话列表  ·  私聊  ·  群协作  ·  同一套模型")


def panel_label(draw, x, y, text) -> None:
    tw, th = text_size(draw, text, F["badge"])
    rounded(draw, (x, y, x + tw + 28, y + th + 16), 999, WHITE)
    draw.text((x + 14, y + 7), text, font=F["badge"], fill=BLUE_DEEP)


def render_both(t: float, shot: Image.Image, social: Image.Image) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    center_text(draw, "按场景切换，不用两套配置", 36, F["h3"], INK)
    gap = 28
    pw = (W - 72 - gap) // 2
    ph = 860
    y0 = 100
    left = contain_pad(shot, pw, ph, WHITE)
    right = contain_pad(social, pw, ph, WHITE)
    img.paste(left, (36, y0))
    img.paste(right, (36 + pw + gap, y0))
    draw = ImageDraw.Draw(img)
    rounded(draw, (36, y0, 36 + pw, y0 + ph), 8, None, LINE, 2)
    rounded(draw, (36 + pw + gap, y0, 36 + pw + gap + pw, y0 + ph), 8, None, LINE, 2)
    panel_label(draw, 52, y0 + 16, "工作台")
    panel_label(draw, 36 + pw + gap + 16, y0 + 16, "会话版")
    return img


def render_tools(t: float) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    center_text(draw, "工具调用，关键动作需要你确认", 64, F["h1"], INK)
    center_text(draw, "主智能体调度工具；写文件、Shell、改 Memory 走审批门", 130, F["body"], MUTED)
    items = [
        ("文件", "读取、写入、替换、搜索"),
        ("Shell", "跑命令，默认先审批"),
        ("浏览器", "browser_* · 沙箱里操作页面"),
        ("Git", "查看差异与仓库状态"),
        ("Memory", "追加或改写 MEMORY.md"),
        ("子智能体", "把子任务委派出去并行做"),
    ]
    cw, ch = 560, 150
    gap_x, gap_y = 28, 24
    x0 = (W - cw * 3 - gap_x * 2) // 2
    y0 = 230
    for i, (title, desc) in enumerate(items):
        if t < i * 0.35:
            continue
        col, row = i % 3, i // 3
        x = x0 + col * (cw + gap_x)
        y = y0 + row * (ch + gap_y)
        rounded(draw, (x, y, x + cw, y + ch), 18, WHITE, LINE, 2)
        draw.text((x + 28, y + 32), title, font=F["h2"], fill=BLUE_DEEP)
        draw.text((x + 28, y + 88), desc, font=F["body"], fill=MUTED)
    return img


def trim_margin(src: Image.Image) -> Image.Image:
    """Drop the empty page margin so a centered UI fills the video frame."""
    im = src.convert("RGB")
    px = im.load()
    w, h = im.size
    bg = px[2, 2]

    def differs(p) -> bool:
        return abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > 36

    def row_has(y: int) -> bool:
        return any(differs(px[x, y]) for x in range(0, w, 3))

    def col_has(x: int) -> bool:
        return any(differs(px[x, y]) for y in range(0, h, 3))

    top = next((y for y in range(h) if row_has(y)), 0)
    bottom = next((y for y in range(h - 1, -1, -1) if row_has(y)), h - 1)
    left = next((x for x in range(w) if col_has(x)), 0)
    right = next((x for x in range(w - 1, -1, -1) if col_has(x)), w - 1)
    pad = 12
    return im.crop((max(0, left - pad), max(0, top - pad), min(w, right + 1 + pad), min(h, bottom + 1 + pad)))


def render_mcp(t: float, shot: Image.Image) -> Image.Image:
    return render_shot(t, shot, "MCP 集成", "设置 → MCP · 本地进程 / 远程 HTTP / 远程 SSE")


def _node(draw, box, title: str, sub: str = "", *, fill=WHITE, title_fill=INK, sub_fill=MUTED, outline=LINE) -> None:
    x0, y0, x1, y1 = box
    rounded(draw, (x0 + 3, y0 + 5, x1 + 3, y1 + 5), 16, (214, 222, 234))
    rounded(draw, box, 16, fill, outline, 2)
    face = F["h3"]
    tw, th = text_size(draw, title, face)
    cx = (x0 + x1) / 2
    if sub:
        draw.text((cx - tw / 2, y0 + 18), title, font=face, fill=title_fill)
        sf = F["small"]
        sw, _ = text_size(draw, sub, sf)
        draw.text((cx - sw / 2, y0 + 58), sub, font=sf, fill=sub_fill)
    else:
        draw.text((cx - tw / 2, (y0 + y1 - th) / 2 - 1), title, font=face, fill=title_fill)


def _vline(draw, x: int, y0: int, y1: int) -> None:
    draw.line((x, y0, x, y1), fill=(148, 163, 184), width=3)


def _arrow(draw, x: int, y: int) -> None:
    draw.polygon([(x - 7, y - 12), (x + 7, y - 12), (x, y + 2)], fill=(100, 116, 139))


def render_arch(t: float) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    center_text(draw, "本地架构", 28, F["h1"], INK)
    center_text(draw, "界面、智能体和数据都在你的机器上", 88, F["body"], MUTED)

    web = (520, 150, 860, 246)
    desk = (1060, 150, 1400, 246)
    api = (700, 310, 1220, 398)
    agent = (640, 470, 1280, 578)
    sub = (250, 670, 590, 766)
    browser = (790, 670, 1130, 766)
    mcp = (1330, 670, 1670, 766)
    disk = (360, 850, 1560, 970)
    model = (700, 990, 1220, 1060)

    # Connectors sit behind the cards.
    _vline(draw, 690, 246, 330)
    _vline(draw, 1230, 246, 330)
    draw.line((690, 330, 1230, 330), fill=(148, 163, 184), width=3)
    _vline(draw, 960, 398, 470)
    _arrow(draw, 960, 470)
    _vline(draw, 960, 578, 640)
    draw.line((420, 640, 1500, 640), fill=(148, 163, 184), width=3)
    _vline(draw, 420, 640, 670)
    _vline(draw, 960, 640, 670)
    _vline(draw, 1500, 640, 670)
    _arrow(draw, 420, 670)
    _arrow(draw, 960, 670)
    _arrow(draw, 1500, 670)
    _vline(draw, 960, 766, 850)
    _arrow(draw, 960, 850)
    _vline(draw, 960, 970, 990)
    _arrow(draw, 960, 990)

    _node(draw, web, "Web 界面", "Vite + React")
    _node(draw, desk, "桌面端", "Electron")
    _node(draw, api, "本机 API", "127.0.0.1:8787")
    _node(
        draw, agent, "主智能体", "规划 · 工具 · 审批 · 压缩",
        fill=BLUE, title_fill=WHITE, sub_fill=(219, 234, 254), outline=BLUE_DEEP,
    )
    _node(draw, sub, "子智能体", "委派并行")
    _node(draw, browser, "浏览器沙盒", "browser_*")
    _node(draw, mcp, "MCP", "外部工具")
    rounded(draw, (disk[0] + 3, disk[1] + 5, disk[2] + 3, disk[3] + 5), 16, (214, 222, 234))
    rounded(draw, disk, 16, WHITE, LINE, 2)
    draw.text((disk[0] + 36, disk[1] + 18), "本机磁盘", font=F["title"], fill=BLUE_DEEP)
    chips = ["工作区", "Skills", "Memory", "本地配置"]
    chip_w = 240
    gap = 24
    total = chip_w * 4 + gap * 3
    x = disk[0] + (disk[2] - disk[0] - total) // 2
    for label in chips:
        rounded(draw, (x, 900, x + chip_w, 948), 999, SOFT)
        tw, th = text_size(draw, label, F["body"])
        draw.text((x + (chip_w - tw) / 2, 908), label, font=F["body"], fill=INK)
        x += chip_w + gap
    if t >= 0.4:
        _node(draw, model, "你的模型 API", "OpenAI 兼容")
    return img


def render_cta(t: float) -> Image.Image:
    img = page()
    draw = ImageDraw.Draw(img)
    icon = Image.open(ASSETS / "brand-icon.png").convert("RGBA").resize((84, 84), Image.Resampling.LANCZOS)
    img.paste(icon, ((W - 84) // 2, 280), icon)
    draw = ImageDraw.Draw(img)
    center_text(draw, "开始使用 EdgeSidekick", 400, F["h1"], INK)
    center_text(draw, "选工作区，配置模型，然后开始对话", 480, F["h3"], MUTED)
    label = "工作台  ·  会话版  ·  工具  ·  MCP"
    tw, th = text_size(draw, label, F["title"])
    bw, bh = tw + 64, th + 36
    x, y = (W - bw) // 2, 580
    rounded(draw, (x, y, x + bw, y + bh), 999, BLUE)
    draw.text((x + 32, y + 16), label, font=F["title"], fill=WHITE)
    return img


def which_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg not found in PATH")
    return path


def main() -> int:
    light = load_shot("gallery-workbench-light.png")
    social_path = ASSETS / "gallery-social.png"
    if not social_path.exists():
        raise SystemExit(f"missing real social screenshot: {social_path}")
    social = trim_margin(load_shot("gallery-social.png"))
    mcp = trim_margin(load_shot("gallery-mcp.png"))
    ffmpeg = which_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="esk-intro-") as tmp:
        tmp_path = Path(tmp)
        index = 0
        print("Rendering 1080p frames…", flush=True)
        for name, dur in SCENES:
            frames = int(round(dur * FPS))
            for i in range(frames):
                t = i / FPS
                if name == "brand":
                    frame = render_brand(t)
                elif name == "workbench":
                    frame = render_workbench(t, light)
                elif name == "social":
                    frame = render_shot(t, social, "会话版", "会话列表  ·  私聊  ·  群协作")
                elif name == "both":
                    frame = render_both(t, light, social)
                elif name == "tools":
                    frame = render_tools(t)
                elif name == "mcp":
                    frame = render_mcp(t, mcp)
                elif name == "arch":
                    frame = render_arch(t)
                else:
                    frame = render_cta(t)
                frame.save(tmp_path / f"frame_{index:05d}.png")
                index += 1
                if index % 60 == 0:
                    print(f"  {index} frames")
        print(f"Encoding {index} frames -> {OUT_MP4}")
        subprocess.run(
            [
                ffmpeg, "-y",
                "-framerate", str(FPS),
                "-i", str(tmp_path / "frame_%05d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "medium",
                "-crf", "16",
                "-tune", "stillimage",
                "-movflags", "+faststart",
                str(OUT_MP4),
            ],
            check=True,
        )
    seconds = sum(d for _, d in SCENES)
    print(f"Done: {OUT_MP4} ({OUT_MP4.stat().st_size / 1024 / 1024:.2f} MB, {seconds:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
