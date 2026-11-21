"""Export one matched frontend bundle for Synology Web Station + native PHP."""
import hashlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def package():
    root = Path(__file__).resolve().parent
    html = (root / "templates/index.html").read_text(encoding="utf-8")
    html = html.replace('content="api/"', 'content="api.php?path="')
    files = {}
    for path in sorted((root / "static").rglob("*")):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            content = path.read_bytes()
            files[name] = content
            version = hashlib.sha256(content).hexdigest()[:12]
            html = html.replace(f'"{name}"', f'"{name}?v={version}"')
    files["index.html"] = html.encode("utf-8")
    files["api.php"] = (root / "webstation/api.php").read_bytes()
    target = root / "artifacts/kdone-webstation.zip"
    target.parent.mkdir(exist_ok=True)
    # Also export ordinary files for direct File Station copying.
    folder = root / "artifacts" / "webstation"
    for name, content in files.items():
        destination = folder / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"index.html", "api.php", "static/app.js", "static/app.css"}
    print(f"{target}\n{len(files)} frontend files; matched HTML/JS/CSS, no credentials or Python source")


if __name__ == "__main__":
    package()
