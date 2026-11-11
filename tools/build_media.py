#!/usr/bin/env python3
"""
Turn full-resolution originals into web-ready media + a manifest.

Usage:
    python3 tools/build_media.py <originals_dir> <output_dir>

<originals_dir> mirrors the portfolio-media folder layout
(01-welcome/bts-me, 02-headshots/gallery, ...). Every image becomes:
    - a "full" WebP (max 2400px long edge) for galleries / lightbox
    - a "thumb" WebP (max 720px long edge) for the conveyor + grids
    - a tiny blurred placeholder embedded in the manifest
Every video becomes:
    - a web MP4 (H.264, <=1080p, fast-start) for the videography page
    - a short, silent, 540p loop for the conveyor belt
    - a poster frame (WebP)
Files are only re-processed when the original changes, so re-runs are fast.
Output: <output_dir>/media.json describing everything, in folder order.
"""
import base64, hashlib, io, json, os, subprocess, sys
from pathlib import Path
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None
IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".m4v"}
COPY_EXT = {".pdf", ".svg"}
FULL_PX, THUMB_PX = 2400, 720


def slug(name: str) -> str:
    stem = Path(name).stem
    out = "".join(c.lower() if c.isalnum() else "-" for c in stem)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "file"


def fingerprint(p: Path) -> str:
    st = p.stat()
    return hashlib.md5(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:10]


def save_webp(im, dest, px, quality):
    im = im.copy()
    im.thumbnail((px, px), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "WEBP", quality=quality, method=5)
    return im.size


def placeholder(im) -> str:
    small = im.copy()
    small.thumbnail((24, 24))
    buf = io.BytesIO()
    small.save(buf, "WEBP", quality=40)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def process_image(src, rel_dir, out_root, cache):
    key = slug(src.name)
    fp = fingerprint(src)
    full = out_root / rel_dir / f"{key}.webp"
    thumb = out_root / rel_dir / "thumbs" / f"{key}.webp"
    cached = cache.get(str(rel_dir / src.name))
    if cached and cached.get("fp") == fp and full.exists() and thumb.exists():
        return cached
    im = Image.open(src)
    im = ImageOps.exif_transpose(im)
    has_alpha = im.mode in ("RGBA", "LA", "P")
    im = im.convert("RGBA" if has_alpha else "RGB")
    w, h = save_webp(im, full, FULL_PX, 84)
    save_webp(im, thumb, THUMB_PX, 78)
    return {
        "type": "image", "name": src.name, "fp": fp,
        "src": str(full.relative_to(out_root.parent)),
        "thumb": str(thumb.relative_to(out_root.parent)),
        "w": w, "h": h, "lqip": placeholder(im),
    }


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def process_video(src, rel_dir, out_root, cache):
    key = slug(src.name)
    fp = fingerprint(src)
    d = out_root / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    web, loop, poster = d / f"{key}.mp4", d / f"{key}-loop.mp4", d / f"{key}-poster.webp"
    cached = cache.get(str(rel_dir / src.name))
    if cached and cached.get("fp") == fp and all(p.exists() for p in (web, loop, poster)):
        return cached
    ffmpeg("-i", str(src), "-vf", "scale='min(1920,iw)':-2", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "24", "-maxrate", "6M", "-bufsize", "12M",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", str(web))
    ffmpeg("-i", str(src), "-t", "12", "-an", "-vf", "scale=-2:540", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(loop))
    tmp = d / f"{key}-poster.png"
    ffmpeg("-ss", "1", "-i", str(src), "-frames:v", "1", str(tmp))
    im = Image.open(tmp).convert("RGB")
    w, h = save_webp(im, poster, 1920, 80)
    tmp.unlink()
    return {
        "type": "video", "name": src.name, "fp": fp,
        "src": str(web.relative_to(out_root.parent)),
        "loop": str(loop.relative_to(out_root.parent)),
        "poster": str(poster.relative_to(out_root.parent)),
        "w": w, "h": h, "lqip": placeholder(im),
    }


def main(src_root, out_root):
    src_root, out_root = Path(src_root), Path(out_root)
    manifest_path = out_root / "media.json"
    cache = {}
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        for folder, items in old.get("folders", {}).items():
            for it in items:
                cache[f"{folder}/{it['name']}"] = it
    folders = {}
    for path in sorted(src_root.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel_dir = path.parent.relative_to(src_root)
        ext = path.suffix.lower()
        try:
            if ext in IMG_EXT:
                item = process_image(path, rel_dir, out_root, cache)
            elif ext in VID_EXT:
                item = process_video(path, rel_dir, out_root, cache)
            elif ext in COPY_EXT:
                dest = out_root / rel_dir / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(path.read_bytes())
                item = {"type": ext[1:], "name": path.name,
                        "src": str(dest.relative_to(out_root.parent))}
            else:
                continue
        except Exception as e:
            print(f"  skipped {path}: {e}", file=sys.stderr)
            continue
        folders.setdefault(str(rel_dir), []).append(item)
        print(f"  ok  {rel_dir}/{path.name}")
    manifest_path.write_text(json.dumps({"folders": folders}, indent=1))
    # Remove web files whose originals were deleted
    keep = set()
    for items in folders.values():
        for it in items:
            for k in ("src", "thumb", "loop", "poster"):
                if k in it:
                    keep.add(str(out_root.parent / it[k]))
    for f in out_root.rglob("*"):
        if f.is_file() and f.name != "media.json" and str(f) not in keep:
            f.unlink()
            print(f"  removed {f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
