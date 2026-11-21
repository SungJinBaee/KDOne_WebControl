"""Create a source-only NAS archive from an explicit allowlist."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def package():
    root = Path(__file__).resolve().parent
    files = [root / name for name in (
        "compose.yaml", "Dockerfile", ".dockerignore", ".env.example",
        "requirements.txt", "requirements-web.txt", "home_service.py",
        "schedules.py", "webapp.py", "config_store.py", "NAS_SETUP.md", "WEB_STATION_SETUP.md", "EXTERNAL_API.md",
        "config/settings.example.json", "config/secrets.example.json")]
    for directory in ("templates", "static"):
        files.extend(path for path in (root / directory).rglob("*") if path.is_file())
    upstream = root / "upstream" / "KDOne"
    files.extend(upstream / name for name in ("setup.py", "requirements.txt", "README.md", "LICENSE"))
    files.extend((upstream / "KDOne").rglob("*.py"))
    target = root / "artifacts" / "kdone-home-nas.zip"
    target.parent.mkdir(exist_ok=True)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, path.relative_to(root).as_posix())
    with ZipFile(target) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
        assert {"compose.yaml", "Dockerfile", ".env.example", "webapp.py"} <= set(names)
        forbidden = {".venv", ".tools", ".git", "data", "__pycache__"}
        assert not any(forbidden.intersection(Path(name).parts) for name in names)
        assert ".env" not in names
        assert not {"config/settings.json", "config/secrets.json"}.intersection(names)
    print(f"{target}\n{len(names)} files, {target.stat().st_size:,} bytes; archive verified")


if __name__ == "__main__":
    package()
