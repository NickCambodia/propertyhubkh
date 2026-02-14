#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from urllib.parse import quote


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ASSET_EXT = IMAGE_EXT | {".pdf"}
EXT_PREFERENCE = {".webp": 0, ".jpg": 1, ".jpeg": 2, ".png": 3}

PROJECT_FOLDER_MAP = {
    "Time Square 7": "Time Square 7",
    "Time Square 8": "Time Square 8",
    "Time Square 9": "Time Square 9",
    "Time Square 10": "Time Square 10",
    "Kingston Royale": "Kingston Royale",
    "Le Conde": "Le Conde",
    "J Tower 3": "J-Tower 3",
    "Odom Tower": "ODOM Tower",
    "Odom Living": "Odom Living",
    "UC88 Wyndham Garden": "UC88 (Wyndham Garden)",
    "Diamond Bay Garden": "Diamond Bay Garden",
    "Angkor Grace": "Angkor Grace",
    "Rose Apple Square": "Rose Apple Square",
    "LZ Sea View Premium": "LZ Sea View Premium",
    "Picasso Sky Gemme": "Picasso Sky Gemme",
    "GATO Tower": "GATO Tower",
}


def urlize(path: Path) -> str:
    return "./" + quote(path.as_posix(), safe="/._-()")


def dedupe_prefer(paths: list[Path]) -> list[Path]:
    selected: dict[str, Path] = {}
    for p in paths:
        stem_key = (p.parent.as_posix().lower() + "/" + p.stem.lower().strip()).replace("\\", "/")
        current = selected.get(stem_key)
        if current is None:
            selected[stem_key] = p
            continue
        current_rank = EXT_PREFERENCE.get(current.suffix.lower(), 99)
        next_rank = EXT_PREFERENCE.get(p.suffix.lower(), 99)
        if next_rank < current_rank:
            selected[stem_key] = p
    return sorted(selected.values(), key=lambda x: x.as_posix().lower())


def list_images(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    out = []
    for p in sorted(path.rglob("*"), key=lambda x: x.as_posix().lower()):
        if p.is_file() and p.suffix.lower() in ASSET_EXT:
            out.append(p)
    return dedupe_prefer(out)


def list_top_level_images(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    out = []
    for p in sorted(path.iterdir(), key=lambda x: x.as_posix().lower()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXT:
            out.append(p)
    return dedupe_prefer(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync project asset arrays in data/projects.json from folder contents.")
    parser.add_argument("--projects-json", default="data/projects.json")
    parser.add_argument("--root", default="Property Listing")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    projects_path = Path(args.projects_json)
    root = Path(args.root)
    data = json.loads(projects_path.read_text(encoding="utf-8"))

    changes = 0
    for project in data:
        name = project.get("name")
        folder_name = PROJECT_FOLDER_MAP.get(name)
        if not folder_name:
            continue
        project_dir = root / folder_name
        if not project_dir.exists():
            continue

        main_image = str(project.get("image", ""))
        gallery = []
        for p in list_top_level_images(project_dir):
            rel = p.as_posix()
            entry = urlize(Path(rel))
            if entry == main_image:
                continue
            gallery.append(entry)

        floor_dir = project_dir / "Floor Plan"
        if not floor_dir.exists():
            floor_dir = project_dir / "Floorplans"
        facility_dir = project_dir / "Facility"
        unit_dir = project_dir / "Unit Layout"

        floor_plans = [urlize(Path(p.as_posix())) for p in list_images(floor_dir)]
        facilities = [urlize(Path(p.as_posix())) for p in list_images(facility_dir)]
        unit_layouts = [urlize(Path(p.as_posix())) for p in list_images(unit_dir)]

        before = (
            project.get("images", []),
            project.get("floorPlans", []),
            project.get("facilities", []),
            project.get("unitLayouts", []),
        )

        # Always refresh arrays for mapped projects so stale, deleted files do not remain referenced.
        project["images"] = gallery
        project["floorPlans"] = floor_plans
        project["facilities"] = facilities
        project["unitLayouts"] = unit_layouts

        after = (
            project.get("images", []),
            project.get("floorPlans", []),
            project.get("facilities", []),
            project.get("unitLayouts", []),
        )
        if before != after:
            changes += 1
            print(f"updated: {name}")

    if args.dry_run:
        print(f"dry-run changes: {changes}")
        return

    projects_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"applied changes: {changes}")


if __name__ == "__main__":
    main()
