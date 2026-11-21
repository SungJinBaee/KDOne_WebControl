import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile

import package_webstation


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id":
                self.values.add(value)


def test_javascript_literal_dom_references_exist():
    root = Path(__file__).resolve().parents[1]
    parser = IDs()
    parser.feed((root / "templates/index.html").read_text(encoding="utf-8"))
    references = set(re.findall(r'\$\("([a-z][a-z0-9-]+)"\)', (root / "static/app.js").read_text(encoding="utf-8")))
    assert references <= parser.values, references - parser.values


def test_webstation_bundle_has_matched_assets_and_no_secrets():
    package_webstation.package()
    root = Path(__file__).resolve().parents[1]
    with ZipFile(root / "artifacts/kdone-webstation.zip") as archive:
        assert set(archive.namelist()) == {"index.html", "static/app.js", "static/app.css", "api.php"}
        html = archive.read("index.html").decode("utf-8")
        assert 'content="api.php?path="' in html
        for name in ("static/app.js", "static/app.css"):
            version = hashlib.sha256(archive.read(name)).hexdigest()[:12]
            assert f'{name}?v={version}' in html
        assert 'id="settings-auth-updated"' in html
