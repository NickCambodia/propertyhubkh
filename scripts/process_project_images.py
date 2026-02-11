#!/usr/bin/env python3
import argparse
from pathlib import Path
from typing import Iterable, Set, Tuple
import json
import shutil
import subprocess
import re
import hashlib

from PIL import Image, ImageEnhance


SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SKIP_NAME_TOKENS = ("_wm", "watermark", "logo")


def iter_images(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXT:
            continue
        yield path


def git_changed_images(root: Path) -> Set[Path]:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain", "-z", "-uall"], stderr=subprocess.DEVNULL
        )
    except Exception:
        return set()
    changed: Set[Path] = set()
    entries = out.decode("utf-8", "replace").split("\0")
    for entry in entries:
        if not entry:
            continue
        rel = entry[3:]
        p = Path(rel)
        if not str(p).startswith(str(root)):
            continue
        if p.is_dir():
            for child in iter_images(p):
                changed.add(child)
            continue
        if p.suffix.lower() not in SUPPORTED_EXT:
            continue
        if p.exists():
            changed.add(p)
    return changed


def is_already_watermarked(path: Path) -> bool:
    name = path.name.lower()
    return any(token in name for token in SKIP_NAME_TOKENS)


def is_project_card_variant(path: Path) -> bool:
    # Keep project card assets clean (no watermark), including responsive 640w variants.
    return bool(re.search(r"main(?:-\d+w)?\.(jpg|jpeg|png|webp)$", path.name.lower()))


def load_project_card_images(projects_json: Path) -> Set[Path]:
    if not projects_json.exists():
        return set()
    try:
        data = json.loads(projects_json.read_text(encoding="utf-8"))
    except Exception:
        return set()
    result: Set[Path] = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        image = item.get("image")
        if not isinstance(image, str) or not image.strip():
            continue
        rel = image.replace("./", "")
        try:
            rel = bytes(rel, "utf-8").decode("utf-8")
        except Exception:
            pass
        rel = rel.replace("%20", " ")
        result.add(Path(rel))
    return result


def prepare_watermark(watermark_path: Path, target_size: Tuple[int, int], ratio: float, opacity: float) -> Image.Image:
    base_w, base_h = target_size
    mark = Image.open(watermark_path).convert("RGBA")
    mark_target_w = max(120, int(base_w * ratio))
    scale = mark_target_w / mark.width
    mark_target_h = max(1, int(mark.height * scale))
    mark = mark.resize((mark_target_w, mark_target_h), Image.Resampling.LANCZOS)

    alpha = mark.split()[-1]
    alpha = ImageEnhance.Brightness(alpha).enhance(opacity)
    mark.putalpha(alpha)
    return mark


def apply_mark(img: Image.Image, mark: Image.Image, margin: int) -> Image.Image:
    canvas = img.convert("RGBA")
    x = (canvas.width - mark.width) // 2
    # Keep watermark centered so placement is consistent across all assets.
    y = (canvas.height - mark.height) // 2
    canvas.alpha_composite(mark, (x, y))
    return canvas


def save_optimized(img_rgba: Image.Image, dst: Path, quality: int) -> None:
    ext = dst.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        img = img_rgba.convert("RGB")
        img.save(dst, quality=quality, optimize=True, progressive=True)
    elif ext == ".png":
        img = img_rgba.convert("RGBA")
        img.save(dst, optimize=True, compress_level=9)
    elif ext == ".webp":
        img = img_rgba.convert("RGB")
        img.save(dst, format="WEBP", quality=quality, method=6)
    else:
        img_rgba.save(dst)


def process_file(
    path: Path,
    watermark_path: Path,
    ratio: float,
    opacity: float,
    margin: int,
    quality: int,
    dry_run: bool,
    card_images: Set[Path],
    root_dir: Path,
) -> str:
    rel_path = path.relative_to(root_dir.parent) if root_dir.parent in path.parents else path
    if rel_path in card_images:
        return "skip_project_card"
    if is_project_card_variant(path):
        return "skip_project_card"
    if is_already_watermarked(path):
        return "skip_watermarked_name"

    try:
        with Image.open(path) as src:
            src.load()
            if src.width < 300 or src.height < 300:
                return "skip_small"
            mark = prepare_watermark(watermark_path, (src.width, src.height), ratio, opacity)
            out = apply_mark(src.convert("RGBA"), mark, margin)
            if not dry_run:
                save_optimized(out, path, quality)
            return "processed"
    except Exception:
        return "error"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize and watermark project images.")
    parser.add_argument("--root", default="Property Listing", help="Root directory to scan.")
    parser.add_argument("--watermark", default="PHC Logo (1).png", help="Watermark image path.")
    parser.add_argument("--quality", type=int, default=82, help="Compression quality for JPEG/WEBP.")
    parser.add_argument("--ratio", type=float, default=0.22, help="Watermark width ratio relative to image width.")
    parser.add_argument("--opacity", type=float, default=0.18, help="Watermark opacity (0..1).")
    parser.add_argument("--margin", type=int, default=28, help="Bottom margin in pixels.")
    parser.add_argument("--dry-run", action="store_true", help="Report only. Do not modify files.")
    parser.add_argument("--projects-json", default="data/projects.json", help="Project catalog JSON for card-image exclusion.")
    parser.add_argument("--changed-only", action="store_true", help="Process only git changed/new files under --root.")
    parser.add_argument("--backup-dir", default="", help="Directory to write backups before processing.")
    parser.add_argument(
        "--manifest",
        default=".image-process-manifest.json",
        help="Track processed file hashes to prevent repeat watermarking on re-runs.",
    )
    parser.add_argument(
        "--record-only",
        action="store_true",
        help="Do not process files; only record current hashes into manifest for selected files.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    watermark_path = Path(args.watermark)
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")
    if not watermark_path.exists():
        raise SystemExit(f"Watermark not found: {watermark_path}")
    card_images = load_project_card_images(Path(args.projects_json))
    changed_set = git_changed_images(root) if args.changed_only else set()
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    if backup_dir:
        backup_dir.mkdir(parents=True, exist_ok=True)

    counts = {
        "processed": 0,
        "skipped_unchanged": 0,
        "skip_project_card": 0,
        "skip_watermarked_name": 0,
        "skip_small": 0,
        "skip_manifest": 0,
        "error": 0,
    }

    for img_path in iter_images(root):
        if changed_set and img_path not in changed_set:
            counts["skipped_unchanged"] = counts.get("skipped_unchanged", 0) + 1
            continue
        key = img_path.as_posix()
        current_hash = sha256_file(img_path)
        if manifest.get(key) == current_hash:
            counts["skip_manifest"] = counts.get("skip_manifest", 0) + 1
            continue
        if args.record_only:
            manifest[key] = current_hash
            counts["skip_manifest"] = counts.get("skip_manifest", 0) + 1
            continue
        if backup_dir and not args.dry_run:
            backup_path = backup_dir / img_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(img_path, backup_path)
        result = process_file(
            img_path,
            watermark_path,
            ratio=args.ratio,
            opacity=args.opacity,
            margin=args.margin,
            quality=args.quality,
            dry_run=args.dry_run,
            card_images=card_images,
            root_dir=root,
        )
        counts[result] = counts.get(result, 0) + 1
        if result == "processed" and not args.dry_run:
            manifest[key] = sha256_file(img_path)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"[{mode}] root={root}")
    for key in (
        "processed",
        "skipped_unchanged",
        "skip_manifest",
        "skip_project_card",
        "skip_watermarked_name",
        "skip_small",
        "error",
    ):
        print(f"{key}: {counts.get(key, 0)}")
    if not args.dry_run:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
