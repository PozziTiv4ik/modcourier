"""Build a deterministic, dependency-free Python zip application."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output=None):
    target = Path(output) if output else ROOT / "dist" / "modcourier.pyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    files = {"__main__.py": b"from modcourier.cli import main\nraise SystemExit(main())\n"}
    for path in sorted((ROOT / "src" / "modcourier").rglob("*.py")):
        files[path.relative_to(ROOT / "src").as_posix()] = path.read_bytes()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (target.parent / "SHA256SUMS").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    return target


if __name__ == "__main__":
    print(build())
