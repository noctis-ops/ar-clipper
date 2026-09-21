#!/usr/bin/env python3
"""مولّد عيّنات فيديو للتجربة — بلا إنترنت وبلا حقوق طرف ثالث.

لماذا؟ لأن تجربة الأداة تحتاج فيديو، وتنزيل واحد من الإنترنت قد يكون محجوباً
أو محكوماً بحقوق. هذه العيّنات مُولَّدة محلياً بالكامل.

الاستخدام:
    python scripts/make_sample.py                 # كل العيّنات
    python scripts/make_sample.py --kind speaker  # عيّنة واحدة
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "samples"


def _ffmpeg() -> str:
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def _face(size: int = 300):
    """وجه تخطيطي يكتشفه مصنّف Haar فعلاً — بلا صور أشخاص حقيقيين.

    ملاحظة مهمة: الرسم البسيط (دائرة + نقطتان) **لا يُكتشف إطلاقاً**؛ جرّبناه
    فأعطى 0%. مصنّف Haar يعتمد على فروق الإضاءة بين مناطق الوجه، فلا بد من
    جبهة فاتحة فوق محاجر داكنة وحواجب سميكة. هذه النسخة مُتحقَّق منها: تُكتشف
    في كل الأحجام من 200 إلى 360 بكسل.
    """
    import cv2
    import numpy as np

    img = np.full((size, size, 3), 45, np.uint8)
    cx = cy = size // 2
    # الرأس بيضاوي بلون بشرة
    cv2.ellipse(img, (cx, cy), (int(size * .30), int(size * .38)),
                0, 0, 360, (175, 190, 210), -1)
    # جبهة أفتح — Haar يبحث عن جبهة فاتحة فوق عيون داكنة
    cv2.ellipse(img, (cx, cy - int(size * .15)), (int(size * .27), int(size * .16)),
                0, 0, 360, (190, 205, 222), -1)
    eye_y = cy - int(size * .08)
    dx = int(size * .13)
    for sx in (-dx, dx):
        cv2.ellipse(img, (cx + sx, eye_y), (int(size * .075), int(size * .045)),
                    0, 0, 360, (120, 135, 155), -1)   # محجر داكن
        cv2.ellipse(img, (cx + sx, eye_y), (int(size * .06), int(size * .032)),
                    0, 0, 360, (250, 250, 252), -1)   # بياض العين
        cv2.circle(img, (cx + sx, eye_y), int(size * .026), (45, 40, 38), -1)
        cv2.ellipse(img, (cx + sx, eye_y - int(size * .075)),
                    (int(size * .075), int(size * .022)), 0, 180, 360, (55, 50, 55), -1)
    cv2.ellipse(img, (cx, cy + int(size * .06)), (int(size * .035), int(size * .085)),
                0, 0, 360, (158, 172, 192), -1)       # أنف
    cv2.ellipse(img, (cx, cy + int(size * .20)), (int(size * .10), int(size * .035)),
                0, 0, 360, (95, 85, 95), -1)          # فم
    return img


def _encode(frames_dir: Path, out: Path, fps: int, with_audio: bool = True) -> None:
    args = [_ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps),
            "-i", str(frames_dir / "%05d.png")]
    if with_audio:
        # صوت بفترات صمت حقيقية — ليختبر حذف الصمت
        args += ["-f", "lavfi", "-i",
                 "aevalsrc='0.25*sin(440*2*PI*t)*lt(mod(t\\,6)\\,4)':d=30"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if with_audio:
        args += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    args.append(str(out))
    subprocess.run(args, check=True)


def make_speaker(seconds: int = 30, fps: int = 25) -> Path:
    """متحدث واحد يتحرّك أفقياً — يُظهر قيمة تتبّع الوجه."""
    import cv2
    import numpy as np

    out = OUT_DIR / "speaker.mp4"
    tmp = OUT_DIR / "_frames_speaker"
    tmp.mkdir(parents=True, exist_ok=True)
    face = _face(300)
    W, H = 1280, 720
    total = seconds * fps
    for i in range(total):
        bg = np.full((H, W, 3), 32, np.uint8)
        cv2.rectangle(bg, (0, H - 90), (W, H), (26, 26, 30), -1)
        # يتحرّك من يسار الكادر إلى يمينه ثم يعود
        phase = (i / total) * 2
        pos = phase if phase <= 1 else 2 - phase
        cx = int((0.2 + 0.6 * pos) * W)
        y0 = 180
        bg[y0:y0 + 300, max(0, cx - 150):max(0, cx - 150) + 300] = face[
            :, : min(300, W - max(0, cx - 150))
        ]
        cv2.imwrite(str(tmp / f"{i + 1:05d}.png"), bg)
    _encode(tmp, out, fps)
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return out


def make_dialogue(seconds: int = 20, fps: int = 25) -> Path:
    """متحدثان على الجانبين — يُظهر قيمة الشاشة المنقسمة."""
    import cv2
    import numpy as np

    out = OUT_DIR / "dialogue.mp4"
    tmp = OUT_DIR / "_frames_dialogue"
    tmp.mkdir(parents=True, exist_ok=True)
    left, right = _face(280), cv2.flip(_face(280), 1)
    W, H = 1280, 720
    for i in range(seconds * fps):
        bg = np.full((H, W, 3), 30, np.uint8)
        bg[200:480, 180:460] = left     # مركز ≈ 0.25
        bg[200:480, 820:1100] = right   # مركز ≈ 0.75
        cv2.imwrite(str(tmp / f"{i + 1:05d}.png"), bg)
    _encode(tmp, out, fps)
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return out


def make_static(seconds: int = 15, fps: int = 25) -> Path:
    """مشهد ثابت بلا وجوه — يختبر التراجع الآمن للقص المركزي."""
    out = OUT_DIR / "static.mp4"
    subprocess.run(
        [_ffmpeg(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={fps}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out


KINDS = {"speaker": make_speaker, "dialogue": make_dialogue, "static": make_static}


def main() -> int:
    parser = argparse.ArgumentParser(description="توليد عيّنات فيديو محلية للتجربة")
    parser.add_argument("--kind", choices=sorted(KINDS) + ["all"], default="all")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chosen = sorted(KINDS) if args.kind == "all" else [args.kind]

    print("توليد عيّنات محلية (بلا إنترنت، بلا حقوق طرف ثالث)\n")
    for kind in chosen:
        print(f"  ⏳ {kind} ...", end=" ", flush=True)
        path = KINDS[kind]()
        size_kb = path.stat().st_size // 1024
        print(f"✅ {path.relative_to(ROOT)} ({size_kb} KB)")

    print("\nجرّبها:")
    print("  .venv/bin/python -m cli.main clip data/samples/speaker.mp4 \\")
    print("      -s 0 -e 20 --face-track --no-subtitles -L personal_test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
