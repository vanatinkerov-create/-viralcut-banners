#!/usr/bin/env python3
"""Build the six 1920x1080 slides for the ViralCut 30s GitHub promo.

Run from this directory (or the repo root): python3 build_slides.py
Slides land in ./slides/s1.png ... s6.png.
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SHOTS = os.path.join(ROOT, 'docs', 'screenshots')
FONTS = os.path.join(ROOT, 'assets', 'fonts')
ICON = os.path.join(ROOT, 'assets', 'icon_1024.png')
OUT = os.path.join(HERE, 'slides')

W, H = 1920, 1080

# Brand palette (matches the app icon and the existing covers).
BG_TOP = (34, 16, 50)
BG_BOTTOM = (12, 7, 22)
VIOLET = (139, 92, 246)
VIOLET_SOFT = (167, 139, 250)
VIOLET_DEEP = (91, 33, 182)
TEXT = (245, 243, 255)
MUTED = (183, 169, 201)
CARD_FILL = (20, 17, 28)

HEAD = os.path.join(FONTS, 'GolosText-Bold.ttf')
BODY = os.path.join(FONTS, 'Montserrat-SemiBold.ttf')
BOLD = os.path.join(FONTS, 'Montserrat-Bold.ttf')


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def background() -> Image.Image:
    """Vertical gradient with two soft violet glows."""
    rows = np.linspace(0.0, 1.0, H)[:, None]
    top = np.array(BG_TOP, dtype=float)[None, :]
    bottom = np.array(BG_BOTTOM, dtype=float)[None, :]
    grad = top * (1 - rows) + bottom * rows  # H x 3
    canvas = np.repeat(grad[:, None, :], W, axis=1)

    yy, xx = np.mgrid[0:H, 0:W]
    for cx, cy, radius, color, alpha in (
        (1520, 120, 760, VIOLET_DEEP, 0.55),
        (240, 980, 700, (76, 29, 149), 0.45),
        (960, 540, 900, (59, 24, 110), 0.30),
    ):
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
        mask = np.clip(1.0 - d, 0.0, 1.0) ** 2 * alpha
        canvas = canvas * (1 - mask[..., None]) + np.array(color, dtype=float) * mask[..., None]

    # Soft vertical vignette so the lower third stays calm under the captions.
    vignette = 1.0 - 0.18 * np.clip((yy - H * 0.55) / (H * 0.75), 0, 1)
    canvas = canvas * vignette[..., None]
    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')


def translucent(img: Image.Image, paint) -> None:
    """Paint with alpha correctly: blend a transparent layer into the canvas.

    Pillow's RGB conversion drops the alpha channel instead of compositing, so a
    draw with (r, g, b, a) on an RGBA canvas must be blended before saving.
    """
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(layer))
    img.alpha_composite(layer)


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def paste_card(base: Image.Image, path: str, box: tuple[int, int, int, int],
               radius: int = 26, shadow: bool = True, chrome: bool = True,
               crop: tuple[float, float, float, float] | None = None,
               brightness: float = 1.0, saturation: float = 1.0) -> None:
    """Place a screenshot as a window card inside `box` (a max area).

    The card hugs the scaled screenshot, so no empty bands appear inside the
    frame. `crop` trims the source by fractions (left, top, right, bottom) so a
    sparse screenshot fills the frame with its content.
    """
    x0, y0, x1, y1 = box
    maxw, maxh = x1 - x0, y1 - y0
    src = Image.open(path).convert('RGB')
    if crop is not None:
        l, t, r, b = crop
        src = src.crop((int(src.width * l), int(src.height * t),
                        int(src.width * (1 - r)), int(src.height * (1 - b))))
    if brightness != 1.0:
        src = ImageEnhance.Brightness(src).enhance(brightness)
    if saturation != 1.0:
        src = ImageEnhance.Color(src).enhance(saturation)

    bar = 40 if chrome else 0
    pad = 12
    scale = min((maxw - 2 * pad) / src.width, (maxh - bar - 2 * pad) / src.height)
    sw, sh = max(1, int(src.width * scale)), max(1, int(src.height * scale))
    shot = src.resize((sw, sh), Image.LANCZOS)
    cw, ch = sw + 2 * pad, sh + bar + 2 * pad
    x = x0 + (maxw - cw) // 2
    y = y0 + (maxh - ch) // 2

    if shadow:
        layer = Image.new('RGBA', (cw + 160, ch + 160), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            (80, 80, 80 + cw, 80 + ch), radius=radius + 6, fill=(6, 2, 14, 160))
        layer = layer.filter(ImageFilter.GaussianBlur(26))
        base.alpha_composite(layer, (x - 80, y - 80 + 18))

    card = Image.new('RGBA', (cw, ch), CARD_FILL + (255,))
    card.paste(shot, (pad, bar + pad))

    def frame(d: ImageDraw.ImageDraw) -> None:
        if not chrome:
            d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=radius,
                                outline=(255, 255, 255, 30), width=2)
            return
        d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=radius,
                            outline=(255, 255, 255, 36), width=2)
        d.line((0, bar, cw, bar), fill=(255, 255, 255, 28), width=2)
        for i, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            cx = 24 + i * 24
            d.ellipse((cx - 6, bar // 2 - 6, cx + 6, bar // 2 + 6), fill=color + (240,))
    translucent(card, frame)
    card.putalpha(rounded_mask((cw, ch), radius))
    base.paste(card, (x, y), card)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ''
    for word in words:
        probe = f'{line} {word}'.strip()
        if draw.textlength(probe, font=fnt) <= max_width or not line:
            line = probe
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def fit(draw: ImageDraw.ImageDraw, text: str, path: str, max_width: int, max_size: int,
        min_size: int = 24) -> ImageFont.FreeTypeFont:
    size = max_size
    while size > min_size:
        fnt = font(path, size)
        if draw.textlength(text, font=fnt) <= max_width:
            return fnt
        size -= 2
    return font(path, min_size)


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], radius: int,
         fill: tuple[int, ...], outline: tuple[int, ...] | None = None, width: int = 2) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def chips(img: Image.Image, draw: ImageDraw.ImageDraw, items: list[str],
          x: int, y: int, fnt_size: int = 28, gap: int = 14) -> int:
    fnt = font(BODY, fnt_size)
    boxes = []
    cx = x
    for item in items:
        tw = int(draw.textlength(item, font=fnt))
        boxes.append((item, (cx, y, cx + tw + 56, y + fnt_size + 30)))
        cx = boxes[-1][1][2] + gap

    def paint(d: ImageDraw.ImageDraw) -> None:
        for _, box in boxes:
            pill(d, box, radius=22, fill=(255, 255, 255, 16), outline=(139, 92, 246, 110))
    translucent(img, paint)
    for item, box in boxes:
        draw.text((box[0] + 28, box[1] + 15), item, font=fnt, fill=TEXT)
    return cx


def brand(img: Image.Image) -> None:
    """Small icon + wordmark in the lower-left corner of content slides."""
    icon = Image.open(ICON).convert('RGBA').resize((44, 44), Image.LANCZOS)
    icon.putalpha(rounded_mask((44, 44), 11))
    img.paste(icon, (120, 982), icon)
    draw = ImageDraw.Draw(img)
    draw.text((176, 991), 'ViralCut', font=font(BOLD, 26), fill=MUTED)


def lock_glyph(img: Image.Image, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    """Rounded lock body plus shackle, centred on (cx, cy)."""
    w = size
    body_h = int(size * 0.62)
    body = (cx - w // 2, cy - body_h // 2 + int(size * 0.16), cx + w // 2, cy + body_h // 2 + int(size * 0.16))
    shackle_w = int(size * 0.56)
    bbox = (cx - shackle_w // 2, body[1] - int(size * 0.46),
            cx + shackle_w // 2, body[1] + int(size * 0.22))

    def paint(d: ImageDraw.ImageDraw) -> None:
        d.arc(bbox, start=180, end=360, fill=color, width=max(8, size // 12))
        d.rounded_rectangle(body, radius=int(size * 0.16), fill=(255, 255, 255, 26),
                            outline=color, width=max(4, size // 26))
    translucent(img, paint)


def slide_hero() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    icon = Image.open(ICON).convert('RGBA').resize((150, 150), Image.LANCZOS)
    icon.putalpha(rounded_mask((150, 150), 34))
    img.paste(icon, (120, 108), icon)

    head = font(HEAD, 94)
    draw.text((120, 330), 'Длинное видео —', font=head, fill=TEXT)
    draw.text((120, 442), 'в вирусные клипы', font=head, fill=VIOLET_SOFT)

    sub = font(BODY, 32)
    lines = wrap(draw, 'Подкасты, интервью, вебинары и стримы — в готовые Shorts, Reels и TikTok', sub, 880)
    y = 620
    for line in lines:
        draw.text((120, y), line, font=sub, fill=MUTED)
        y += 46
    chips(img, draw, ['Умное кадрирование', 'Караоке-субтитры'], 120, 740, 26)

    paste_card(img, os.path.join(SHOTS, 'subtitles-karaoke.png'),
               (1298, 130, 1298 + 450, 130 + 800), radius=34, shadow=False)
    brand(img)
    return img


def slide_moments() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    head = fit(draw, 'Находит моменты сам', HEAD, 840, 78)
    draw.text((120, 112), 'Находит моменты сам', font=head, fill=TEXT)
    sub = font(BODY, 30)
    y = 214
    for line in wrap(draw, 'По смыслу и по лицу, с караоке-субтитрами', sub, 840):
        draw.text((120, y), line, font=sub, fill=MUTED)
        y += 44
    chips(img, draw, ['Whisper', 'OpenCV', 'Авто-хук'], 120, 320, 26)

    paste_card(img, os.path.join(SHOTS, 'ui-results-ios.png'), (1010, 190, 1800, 960), radius=28,
               chrome=True, crop=(0.0, 0.0, 0.0, 0.12), brightness=1.35, saturation=1.05)
    brand(img)
    return img


def slide_clips() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    head = fit(draw, '50 клипов за запуск', HEAD, 900, 84)
    draw.text((120, 112), '50 клипов за запуск', font=head, fill=TEXT)
    sub = font(BODY, 30)
    draw.text((120, 220), 'С вашим шрифтом, цветом и хуком', font=sub, fill=MUTED)
    chips(img, draw, ['24 шрифта', 'Свои TTF', 'Хук на старте'], 120, 292, 26)

    paste_card(img, os.path.join(SHOTS, 'clips.png'), (740, 400, 1800, 960), radius=28,
               brightness=1.12, saturation=1.05)
    brand(img)
    return img


def slide_live() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    head = fit(draw, 'Эфир — на лету', HEAD, 660, 84)
    draw.text((120, 112), 'Эфир — на лету', font=head, fill=TEXT)
    lw = int(draw.textlength('LIVE', font=font(BOLD, 26)))

    def paint_badge(d: ImageDraw.ImageDraw) -> None:
        pill(d, (120, 230, 120 + lw + 88, 230 + 52), radius=26, fill=(239, 68, 68, 240))
        d.ellipse((140, 246, 160, 266), fill=(255, 255, 255, 240))
    translucent(img, paint_badge)
    draw.text((174, 243), 'LIVE', font=font(BOLD, 26), fill=(255, 255, 255))

    sub = font(BODY, 30)
    y = 330
    for line in wrap(draw, 'Клипы появляются, пока идёт трансляция', sub, 580):
        draw.text((120, y), line, font=sub, fill=MUTED)
        y += 44

    paste_card(img, os.path.join(SHOTS, 'ui-live-ios.png'), (800, 210, 1800, 960), radius=28,
               chrome=True, crop=(0.0, 0.0, 0.0, 0.24), brightness=1.35, saturation=1.05)
    brand(img)
    return img


def slide_local() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    lock_glyph(img, 960, 300, 190, VIOLET_SOFT)

    head = fit(draw, '100% локально', HEAD, 1200, 96)
    draw.text((960, 452), '100% локально', font=head, fill=TEXT, anchor='mm')
    sub = font(BODY, 34)
    draw.text((960, 546), 'Без облака, аккаунтов и подписок', font=sub, fill=MUTED, anchor='mm')

    fnt = font(BODY, 28)
    items = ['Whisper внутри', 'ffmpeg внутри', 'Ваши файлы — у вас']
    widths = [int(draw.textlength(i, font=fnt)) + 56 for i in items]
    total = sum(widths) + 14 * (len(items) - 1)
    x0 = (W - total) // 2
    boxes = []
    x = x0
    for item, w in zip(items, widths):
        boxes.append((item, (x, 632, x + w, 632 + 58)))
        x += w + 14

    def paint(d: ImageDraw.ImageDraw) -> None:
        for _, box in boxes:
            pill(d, box, radius=22, fill=(255, 255, 255, 16), outline=(139, 92, 246, 110))
    translucent(img, paint)
    for item, box in boxes:
        draw.text((box[0] + 28, 647), item, font=fnt, fill=TEXT)
    brand(img)
    return img


def slide_release() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    icon = Image.open(ICON).convert('RGBA').resize((152, 152), Image.LANCZOS)
    icon.putalpha(rounded_mask((152, 152), 34))
    img.paste(icon, ((W - 152) // 2, 150), icon)

    head = fit(draw, 'Уже доступно', HEAD, 1500, 108)
    draw.text((960, 430), 'Уже доступно', font=head, fill=TEXT, anchor='mm')

    url = 'github.com/vanwild11/viralcut'
    fnt = fit(draw, url, BOLD, 1500, 62)
    tw = int(draw.textlength(url, font=fnt))
    box = (960 - tw // 2 - 44, 520, 960 + tw // 2 + 44, 520 + 100)
    translucent(img, lambda d: pill(d, box, radius=30, fill=(139, 92, 246, 40),
                                    outline=(167, 139, 250, 205)))
    draw.text((960, box[1] + 50), url, font=fnt, fill=TEXT, anchor='mm')

    sub = font(BODY, 32)
    draw.text((960, 690), 'macOS и Windows · бесплатно · MIT', font=sub, fill=MUTED, anchor='mm')
    small = font(BODY, 26)
    draw.text((960, 754), 'Релизы и установщики — в разделе Releases', font=small,
              fill=(150, 137, 168), anchor='mm')

    draw.text((960, 852), 'v1.2.1', font=font(BOLD, 28), fill=(167, 139, 250), anchor='mm')
    brand(img)
    return img


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    slides = {
        's1': slide_hero,
        's2': slide_moments,
        's3': slide_clips,
        's4': slide_live,
        's5': slide_local,
        's6': slide_release,
    }
    for name, build in slides.items():
        img = build()
        path = os.path.join(OUT, f'{name}.png')
        img.convert('RGB').save(path)
        print('wrote', path, img.size)


if __name__ == '__main__':
    main()
