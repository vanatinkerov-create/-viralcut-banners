#!/usr/bin/env python3
"""Assemble the ViralCut 30s GitHub promo from slides, voice-over, and music.

Outputs (in ./out):
  ViralCut-promo-30s-github-1080p.mp4
  ViralCut-promo-30s-github-1080p.srt
  ViralCut-promo-30s-github-poster.jpg

Usage: python3 build_video.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = '/Users/ivan/Documents/deepseek-harness/default-workspace/viralcut-studio/dist/ViralCutStudio/_internal/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0'
FFPROBE_FALLBACK = 'ffprobe'

VERTICAL = 'vertical' in sys.argv[1:]
VW, VH = (1080, 1920) if VERTICAL else (1920, 1080)
SLIDE_DIR = 'slides-v' if VERTICAL else 'slides'
NAME = 'ViralCut-promo-30s-tiktok-1080x1920' if VERTICAL else 'ViralCut-promo-30s-github-1080p'
SLIDES = [os.path.join(HERE, SLIDE_DIR, f's{i}.png') for i in range(1, 7)]
VOICE = [os.path.join(HERE, 'audio', 'voice', f'l{i}.mp3') for i in range(1, 7)]
MUSIC = os.path.join(HERE, 'audio', 'music.mp3')
OUT = os.path.join(HERE, 'out-v' if VERTICAL else 'out')

VOICE_TEXT = [
    'Вайралкат превращает длинные видео и стримы в готовые вертикальные клипы для Shorts, Reels и TikTok.',
    'Он сам находит сильные моменты, кадрирует по лицу и накладывает субтитры.',
    'До пятидесяти клипов за один запуск — с вашим шрифтом и цветом.',
    'Прямой эфир режется прямо во время трансляции: клипы появляются, пока стрим ещё идёт.',
    'Всё локально: без облака, аккаунтов и подписок.',
    'Вайралкат уже доступен. Скачать — на ГитХаб.',
]

# Two-line cues: a player cannot wrap a single 110-character line for us.
SUBS = [
    ('ViralCut превращает длинные видео и стримы', 'в готовые вертикальные клипы для Shorts, Reels и TikTok.'),
    ('Он сам находит сильные моменты, кадрирует по лицу', 'и накладывает субтитры.'),
    ('До пятидесяти клипов за один запуск —', 'с вашим шрифтом и цветом.'),
    ('Прямой эфир режется прямо во время трансляции:', 'клипы появляются, пока стрим ещё идёт.'),
    ('Всё локально: без облака,', 'аккаунтов и подписок.'),
    ('ViralCut уже доступен.', 'Скачать — на GitHub.'),
]

LEAD = 0.35       # pause before the first line
GAP = 0.32        # pause between lines
PRE_CUT = 0.30    # the new slide lands slightly before its line
XFADE = 0.35 if 'vertical' in sys.argv[1:] else 0.45  # crossfade length
TAIL = 0.33       # hold after the last line


def duration(path: str) -> float:
    """Read a media duration without requiring ffprobe (parse ffmpeg's banner)."""
    out = subprocess.run([FFMPEG, '-hide_banner', '-i', path], capture_output=True, text=True).stderr
    for line in out.splitlines():
        if 'Duration:' in line:
            stamp = line.split('Duration:')[1].split(',')[0].strip()
            h, m, s = stamp.split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f'no duration for {path}')


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    lens = [duration(p) for p in VOICE]
    music_len = duration(MUSIC)

    starts = []
    t = LEAD
    for length in lens:
        starts.append(t)
        t += length + GAP
    total = round(starts[-1] + lens[-1] + TAIL, 2)

    # Slide presentation offsets: each new scene lands PRE_CUT before its line.
    cuts = [round(max(0.0, s - PRE_CUT), 2) for s in starts]
    cuts[0] = 0.0
    cuts.append(total)
    clip_len = [round(cuts[i + 1] - cuts[i] + (XFADE if i < 5 else 0.0), 3) for i in range(6)]

    print(json.dumps({'voice': [round(x, 2) for x in lens], 'starts': starts,
                      'clip_len': clip_len, 'total': total}, indent=2))

    inputs: list[str] = []
    for path, length in zip(SLIDES, clip_len):
        # -framerate pins the still's timestamps: the image2 demuxer defaults to
        # 25 fps, which would silently shorten every clip by 17%.
        inputs += ['-loop', '1', '-framerate', '30', '-t', f'{length}', '-i', path]
    for path in VOICE:
        inputs += ['-i', path]
    inputs += ['-i', MUSIC]

    chains = []
    for i in range(6):
        frames = max(2, int(round(clip_len[i] * 30)))
        step = 0.045 / frames
        chains.append(
            f'[{i}:v]scale={VW}:{VH},setsar=1,'
            f"zoompan=z='1+{step:.6f}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f':d=1:s={VW}x{VH}:fps=30,format=yuv420p[v{i}]'
        )

    offsets = []
    for i in range(1, 6):
        offsets.append(round(sum(clip_len[:i]) - i * XFADE, 2))

    first = 'v0'
    for i in range(1, 6):
        label = f'x{i}'
        chains.append(
            f'[{first}][v{i}]xfade=transition=fade:duration={XFADE}:offset={offsets[i - 1]:.2f}[{label}]'
        )
        first = label
    fade_in = '' if VERTICAL else 'fade=t=in:st=0:d=0.5,'
    chains.append(
        f'[{first}]{fade_in}fade=t=out:st={max(0.0, total - 0.55):.2f}:d=0.55,format=yuv420p[vout]'
    )

    voice_labels = []
    for i, start in enumerate(starts):
        ms = int(round(start * 1000))
        chains.append(
            f'[{6 + i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
            f'adelay={ms}|{ms}[a{i}]'
        )
        voice_labels.append(f'[a{i}]')
    chains.append(
        ''.join(voice_labels) + f'amix=inputs=6:duration=longest:dropout_transition=0:normalize=0,'
        f'loudnorm=I={-14 if VERTICAL else -16}:TP=-1.5:LRA=11[voiceraw]'
    )

    music_start = 2.0
    chains.append(
        f'[12:a]atrim=start={music_start}:end={music_start + total},asetpts=PTS-STARTPTS,'
        f'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
        f'volume=0.22,afade=t=in:st=0:d=0.8,'
        f'afade=t=out:st={max(0.0, total - 1.6):.2f}:d=1.6[music]'
    )
    chains.append('[voiceraw]asplit=2[voice][voicemix]')
    chains.append(
        '[music][voice]sidechaincompress=threshold=0.04:ratio=9:attack=6:release=320:makeup=1[ducked]'
    )
    chains.append(
        '[voicemix][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,'
        'alimiter=limit=0.95[aout]'
    )

    srt_path = os.path.join(OUT, f'{NAME}.srt')
    with open(srt_path, 'w', encoding='utf-8') as handle:
        for i, ((line1, line2), start, length) in enumerate(zip(SUBS, starts, lens), start=1):
            end = min(total, start + length + 0.28)
            handle.write(f'{i}\n{srt_time(start)} --> {srt_time(end)}\n{line1}\n{line2}\n\n')

    mp4 = os.path.join(OUT, f'{NAME}.mp4')
    cmd = [FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', *inputs,
           '-filter_complex', ';'.join(chains),
           '-map', '[vout]', '-map', '[aout]',
           '-c:v', 'libx264', '-preset', 'slow', '-crf', '19', '-pix_fmt', 'yuv420p',
           '-profile:v', 'high', '-level', '4.1', '-r', '30', '-g', '60',
           '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
           '-t', f'{total}', '-movflags', '+faststart', mp4]

    print('rendering', total, 's with', len(inputs), 'inputs')
    subprocess.run(cmd, check=True)

    # Mux the Russian subtitles as a soft mov_text track.
    muxed = os.path.join(OUT, f'{NAME}.sub.mp4')
    subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i', mp4,
                    '-i', srt_path, '-map', '0:v', '-map', '0:a', '-map', '1:s',
                    '-c:v', 'copy', '-c:a', 'copy', '-c:s', 'mov_text',
                    '-metadata:s:s:0', 'language=rus', '-movflags', '+faststart', muxed], check=True)
    os.replace(muxed, mp4)

    poster = os.path.join(OUT, f'{NAME}-poster.jpg')
    frame = os.path.join(OUT, '_poster.png')
    subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-ss', f'{total - 1.2:.2f}',
                    '-i', mp4, '-frames:v', '1', '-pix_fmt', 'rgb24', frame], check=True)
    from PIL import Image
    Image.open(frame).convert('RGB').save(poster, quality=88)
    os.remove(frame)

    size = os.path.getsize(mp4) / 1_048_576
    print(f'done: {mp4} ({size:.1f} MB, {total:.2f}s), {poster}, {srt_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
