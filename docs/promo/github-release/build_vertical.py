#!/usr/bin/env python3
"""Build the six 1080x1920 (9:16) slides for the ViralCut TikTok promo.

Vertical composition, TikTok-safe: hook at the top, a floating app window in the
middle over a blurred copy of itself, and the spoken line burned in above the
caption zone. Run: python3 build_vertical.py -> slides-v/s1..s6.png
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
OUT = os.path.join(HERE, 'slides-v')

W, H = 1080, 1920

BG_TOP = (38, 18, 58)
BG_BOTTOM = (10, 6, 18)
VIOLET = (139, 92, 246)
VIOLET_SOFT = (167, 139, 250)
VIOLET_DEEP = (91, 33, 182)
TEXT = (246, 244, 255)
MUTED = (186, 173, 205)

HEAD = os.path.join(FONTS, 'GolosText-Bold.ttf')
BODY = os.path.join(FONTS, 'Montserrat-SemiBold.ttf')
BOLD = os.path.join(FONTS, 'Montserrat-Bold.ttf')

# TikTok overlays: keep everything inside this box.
SAFE_X0, SAFE_X1 = 64, 1016
HEAD_Y = 196
CARD_BOX = (90, 466, 950, 1390)
CAPTION_Y = 1428


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def background() -> Image.Image:
    rows = np.linspace(0.0, 1.0, H)[:, None]
    top = np.array(BG_TOP, dtype=float)[None, :]
    bottom = np.array(BG_BOTTOM, dtype=float)[None, :]
    grad = top * (1 - rows) + bottom * rows
    canvas = np.repeat(grad[:, None, :], W, axis=1)

    yy, xx = np.mgrid[0:H, 0:W]
    for cx, cy, radius, color, alpha in (
        (540, 120, 780, VIOLET_DEEP, 0.6),
        (120, 1500, 760, (76, 29, 149), 0.5),
        (980, 900, 700, (60, 25, 120), 0.35),
    ):
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
        mask = np.clip(1.0 - d, 0.0, 1.0) ** 2 * alpha
        canvas = canvas * (1 - mask[..., None]) + np.array(color, dtype=float) * mask[..., None]

    vignette = 1.0 - 0.22 * np.clip((yy - H * 0.5) / (H * 0.6), 0, 1)
    canvas = canvas * vignette[..., None]
    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def translucent(img: Image.Image, paint) -> None:
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(layer))
    img.alpha_composite(layer)


def blurred_backdrop(img: Image.Image, path: str, strength: float = 0.45,
                     crop: tuple[float, float, float, float] | None = None) -> None:
    """Full-bleed blurred copy of the screenshot: fills the tall frame with texture.

    `crop` keeps the content-rich part of a sparse screenshot, and the dark
    vertical scrim keeps the headline readable over bright parts of the shot.
    """
    src = Image.open(path).convert('RGB')
    if crop is not None:
        l, t, r, b = crop
        src = src.crop((int(src.width * l), int(src.height * t),
                        int(src.width * (1 - r)), int(src.height * (1 - b))))
    scale = max(W / src.width, H / src.height)
    sw, sh = int(src.width * scale), int(src.height * scale)
    shot = src.resize((sw, sh), Image.LANCZOS).crop(((sw - W) // 2, (sh - H) // 2,
                                                     (sw - W) // 2 + W, (sh - H) // 2 + H))
    shot = shot.filter(ImageFilter.GaussianBlur(64))
    shot = ImageEnhance.Brightness(shot).enhance(0.5)
    layer = shot.convert('RGBA')
    layer.putalpha(int(255 * strength))
    img.alpha_composite(layer)

    def scrim(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, 0, W, 560), fill=(10, 6, 18, 150))
        d.rectangle((0, 560, W, 1180), fill=(10, 6, 18, 40))
        d.rectangle((0, 1180, W, H), fill=(10, 6, 18, 170))
    translucent(img, scrim)


def glow(img: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int], alpha: int) -> None:
    x0, y0, x1, y1 = box
    layer = Image.new('RGBA', (x1 - x0 + 200, y1 - y0 + 200), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((100, 100, x1 - x0 + 100, y1 - y0 + 100), fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(60))
    img.alpha_composite(layer, (x0 - 100, y0 - 100))


def window_card(img: Image.Image, path: str, box: tuple[int, int, int, int],
                radius: int = 28, chrome: bool = True, crop=None,
                brightness: float = 1.0) -> None:
    """A floating app window that hugs its screenshot, with a violet halo."""
    x0, y0, x1, y1 = box
    maxw, maxh = x1 - x0, y1 - y0
    src = Image.open(path).convert('RGB')
    if crop is not None:
        l, t, r, b = crop
        src = src.crop((int(src.width * l), int(src.height * t),
                        int(src.width * (1 - r)), int(src.height * (1 - b))))
    if brightness != 1.0:
        src = ImageEnhance.Brightness(src).enhance(brightness)

    bar = 46 if chrome else 0
    pad = 14
    scale = min((maxw - 2 * pad) / src.width, (maxh - bar - 2 * pad) / src.height)
    sw, sh = max(1, int(src.width * scale)), max(1, int(src.height * scale))
    shot = src.resize((sw, sh), Image.LANCZOS)
    cw, ch = sw + 2 * pad, sh + bar + 2 * pad
    x = x0 + (maxw - cw) // 2
    y = y0 + (maxh - ch) // 2

    glow(img, (x, y + 40, x + cw, y + ch + 40), VIOLET, 90)

    card = Image.new('RGBA', (cw, ch), (18, 15, 26, 255))
    card.paste(shot, (pad, bar + pad))

    def frame(d: ImageDraw.ImageDraw) -> None:
        if chrome:
            d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=radius,
                                outline=(255, 255, 255, 46), width=2)
            d.line((0, bar, cw, bar), fill=(255, 255, 255, 34), width=2)
            for i, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
                cx = 28 + i * 26
                d.ellipse((cx - 7, bar // 2 - 7, cx + 7, bar // 2 + 7), fill=color + (245,))
        else:
            d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=radius,
                                outline=(255, 255, 255, 36), width=2)
    translucent(card, frame)
    card.putalpha(rounded_mask((cw, ch), radius))
    img.paste(card, (x, y), card)


def fit(draw: ImageDraw.ImageDraw, text: str, path: str, max_width: int, max_size: int,
        min_size: int = 30) -> ImageFont.FreeTypeFont:
    size = max_size
    while size > min_size:
        fnt = font(path, size)
        if draw.textlength(text, font=fnt) <= max_width:
            return fnt
        size -= 2
    return font(path, min_size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    line = ''
    for word in text.split():
        probe = f'{line} {word}'.strip()
        if draw.textlength(probe, font=fnt) <= max_width or not line:
            line = probe
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def headline(img: Image.Image, draw: ImageDraw.ImageDraw, lines: list[str],
             accent_last: bool = True, size: int = 92) -> None:
    """Big hook: 1-3 lines, the last one in violet."""
    fnt = font(HEAD, size)
    widest = max(draw.textlength(line, font=fnt) for line in lines)
    if widest > SAFE_X1 - SAFE_X0:
        fnt = fit(draw, max(lines, key=lambda l: draw.textlength(l, font=fnt)),
                  HEAD, SAFE_X1 - SAFE_X0, size)
    y = HEAD_Y
    for index, line in enumerate(lines):
        color = VIOLET_SOFT if (accent_last and index == len(lines) - 1) else TEXT
        draw.text((SAFE_X0, y), line, font=fnt, fill=color)
        y += int(fnt.size * 1.08)


def caption(img: Image.Image, draw: ImageDraw.ImageDraw, text: str,
            accent: tuple[str, ...] = ()) -> None:
    """The spoken line, burned in above the TikTok caption zone."""
    right = 950  # keep clear of TikTok's right-hand action column
    fnt = font(BODY, 38)
    lines = wrap(draw, text, fnt, right - SAFE_X0 - 72)
    line_h = 50
    box_h = 40 + line_h * len(lines)
    y0 = CAPTION_Y

    def paint(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle((SAFE_X0, y0, right, y0 + box_h), radius=26,
                            fill=(12, 9, 20, 190), outline=(167, 139, 250, 70), width=2)
    translucent(img, paint)

    y = y0 + 18
    for line in lines:
        words = line.split()
        x = SAFE_X0 + 36
        for word in words:
            clean = word.strip('.,—:;!?»«"()')
            color = VIOLET_SOFT if clean in accent else (238, 234, 248)
            draw.text((x, y), word, font=fnt, fill=color)
            x += draw.textlength(word, font=fnt) + draw.textlength(' ', font=fnt)
        y += line_h


def brand(img: Image.Image) -> None:
    """Small brand row, centred so the back/search chrome never covers it."""
    draw = ImageDraw.Draw(img)
    fnt = font(BOLD, 34)
    text_w = draw.textlength('ViralCut', font=fnt)
    total = 64 + 16 + text_w
    x = int((W - total) // 2)
    icon = Image.open(ICON).convert('RGBA').resize((64, 64), Image.LANCZOS)
    icon.putalpha(rounded_mask((64, 64), 16))
    img.paste(icon, (x, 76), icon)
    draw.text((x + 80, 92), 'ViralCut', font=fnt, fill=TEXT)


def lock_glyph(img: Image.Image, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    body_h = int(size * 0.62)
    body = (cx - size // 2, cy - body_h // 2 + int(size * 0.16),
            cx + size // 2, cy + body_h // 2 + int(size * 0.16))
    shackle_w = int(size * 0.56)
    bbox = (cx - shackle_w // 2, body[1] - int(size * 0.46),
            cx + shackle_w // 2, body[1] + int(size * 0.22))

    def paint(d: ImageDraw.ImageDraw) -> None:
        d.arc(bbox, start=180, end=360, fill=color, width=max(10, size // 12))
        d.rounded_rectangle(body, radius=int(size * 0.16), fill=(255, 255, 255, 26),
                            outline=color, width=max(5, size // 26))
    translucent(img, paint)


def chips(img: Image.Image, draw: ImageDraw.ImageDraw, items: list[str],
          y: int, size: int = 30) -> None:
    fnt = font(BODY, size)
    widths = [int(draw.textlength(item, font=fnt)) + 56 for item in items]
    total = sum(widths) + 16 * (len(items) - 1)
    x = (W - total) // 2
    boxes = []
    for item, width in zip(items, widths):
        boxes.append((item, (x, y, x + width, y + size + 32)))
        x += width + 16

    def paint(d: ImageDraw.ImageDraw) -> None:
        for _, box in boxes:
            d.rounded_rectangle(box, radius=22, fill=(255, 255, 255, 18),
                                outline=(167, 139, 250, 110), width=2)
    translucent(img, paint)
    for item, box in boxes:
        draw.text((box[0] + 28, box[1] + 14), item, font=fnt, fill=TEXT)


# ---------------------------------------------------------------- scenes


def s1() -> Image.Image:
    img = background()
    blurred_backdrop(img, os.path.join(SHOTS, 'subtitles-karaoke.png'), 0.45)
    draw = ImageDraw.Draw(img)
    brand(img)
    headline(img, draw, ['Длинное видео —', 'в вирусные клипы'])
    window_card(img, os.path.join(SHOTS, 'subtitles-karaoke.png'),
                (250, 448, 830, 1392), radius=36, chrome=False, brightness=1.08)
    caption(img, draw, 'Shorts, Reels и TikTok — из одного запуска', ('TikTok',))
    return img


def s2() -> Image.Image:
    img = background()
    blurred_backdrop(img, os.path.join(SHOTS, 'ui-results-ios.png'), 0.5,
                     crop=(0.06, 0.0, 0.18, 0.10))
    draw = ImageDraw.Draw(img)
    brand(img)
    headline(img, draw, ['Моменты', 'находит сам'])
    window_card(img, os.path.join(SHOTS, 'ui-results-ios.png'), CARD_BOX,
                crop=(0.0, 0.0, 0.32, 0.12), brightness=1.4)
    caption(img, draw, 'Он сам находит сильные моменты, кадрирует по лицу и накладывает субтитры',
            ('субтитры',))
    return img


def s3() -> Image.Image:
    img = background()
    blurred_backdrop(img, os.path.join(SHOTS, 'clips.png'), 0.5,
                     crop=(0.0, 0.0, 0.34, 0.0))
    draw = ImageDraw.Draw(img)
    brand(img)
    headline(img, draw, ['50 клипов', 'за запуск'])
    window_card(img, os.path.join(SHOTS, 'clips.png'), CARD_BOX,
                crop=(0.0, 0.0, 0.30, 0.0), brightness=1.15)
    caption(img, draw, 'До пятидесяти клипов за один запуск — с вашим шрифтом и цветом',
            ('пятидесяти', 'клипов'))
    return img


def s4() -> Image.Image:
    img = background()
    blurred_backdrop(img, os.path.join(SHOTS, 'ui-live-ios.png'), 0.5,
                     crop=(0.04, 0.0, 0.28, 0.08))
    draw = ImageDraw.Draw(img)
    brand(img)
    headline(img, draw, ['Эфир —', 'на лету'])
    lw = int(draw.textlength('LIVE', font=font(BOLD, 30)))

    def badge(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle((SAFE_X0, 452, SAFE_X0 + lw + 100, 452 + 62), radius=31,
                            fill=(239, 68, 68, 245))
        d.ellipse((SAFE_X0 + 24, 470, SAFE_X0 + 44, 490), fill=(255, 255, 255, 245))
    translucent(img, badge)
    draw.text((SAFE_X0 + 58, 467), 'LIVE', font=font(BOLD, 30), fill=(255, 255, 255))

    window_card(img, os.path.join(SHOTS, 'ui-live-ios.png'),
                (90, 556, 950, 1390), crop=(0.0, 0.0, 0.40, 0.22), brightness=1.4)
    caption(img, draw, 'Прямой эфир режется во время трансляции: клипы появляются, пока идёт стрим',
            ('эфир', 'трансляции'))
    return img


def s5() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    brand(img)
    glow(img, (370, 470, 710, 810), VIOLET, 130)
    lock_glyph(img, W // 2, 650, 290, VIOLET_SOFT)
    fnt = fit(draw, '100% локально', HEAD, SAFE_X1 - SAFE_X0, 104)
    draw.text((W // 2, 880), '100% локально', font=fnt, fill=TEXT, anchor='mm')
    draw.text((W // 2, 985), 'без облака, аккаунтов и подписок',
              font=font(BODY, 38), fill=MUTED, anchor='mm')
    chips(img, draw, ['Whisper внутри', 'ffmpeg внутри', 'файлы у вас'], 1090)
    caption(img, draw, 'Всё локально: без облака, аккаунтов и подписок', ('локально',))
    return img


def s6() -> Image.Image:
    img = background()
    draw = ImageDraw.Draw(img)
    glow(img, (330, 270, 750, 700), VIOLET, 130)
    icon = Image.open(ICON).convert('RGBA').resize((180, 180), Image.LANCZOS)
    icon.putalpha(rounded_mask((180, 180), 42))
    img.paste(icon, ((W - 180) // 2, 320), icon)

    fnt = fit(draw, 'Уже доступно', HEAD, SAFE_X1 - SAFE_X0, 112)
    draw.text((W // 2, 610), 'Уже доступно', font=fnt, fill=TEXT, anchor='mm')

    url = 'github.com/vanwild11/viralcut'
    size = 52
    ufnt = font(BOLD, size)
    tw = int(draw.textlength(url, font=ufnt))
    while W // 2 + tw // 2 + 40 > 948 and size > 34:
        size -= 2
        ufnt = font(BOLD, size)
        tw = int(draw.textlength(url, font=ufnt))
    box = (W // 2 - tw // 2 - 40, 712, W // 2 + tw // 2 + 40, 712 + 104)

    def paint(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle(box, radius=30, fill=(139, 92, 246, 44),
                            outline=(167, 139, 250, 210), width=3)
    translucent(img, paint)
    draw.text((W // 2, box[1] + 52), url, font=ufnt, fill=TEXT, anchor='mm')

    draw.text((W // 2, 885), 'macOS и Windows · бесплатно · MIT',
              font=font(BODY, 36), fill=MUTED, anchor='mm')

    def hint(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle((SAFE_X0 + 40, 1105, SAFE_X1 - 40, 1235), radius=26,
                            fill=(255, 255, 255, 14), outline=(255, 255, 255, 40), width=2)
    translucent(img, hint)
    draw.text((W // 2, 1155), 'Ссылка — в описании и на экране',
              font=font(BODY, 34), fill=TEXT, anchor='mm')
    draw.text((W // 2, 1205), 'Релизы: раздел Releases на GitHub',
              font=font(BODY, 30), fill=MUTED, anchor='mm')

    caption(img, draw, 'ViralCut уже доступен. Скачать — на GitHub', ('GitHub',))
    return img


SCENES = {'s1': s1, 's2': s2, 's3': s3, 's4': s4, 's5': s5, 's6': s6}


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for name, build in SCENES.items():
        img = build()
        path = os.path.join(OUT, f'{name}.png')
        img.convert('RGB').save(path)
        print('wrote', path, img.size)


if __name__ == '__main__':
    main()
