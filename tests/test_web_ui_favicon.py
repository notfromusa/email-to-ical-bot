"""
Unit tests for Web UI tab icon (favicon) routing and asset availability.
"""

from pathlib import Path
from web_ui import app


def test_favicon_ico_route():
    """
    Verify that GET /favicon.ico returns 200 OK with the icon mime type and non-empty content.
    """
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert "image/vnd.microsoft.icon" in response.headers.get("Content-Type", "")
        assert len(response.data) > 0


def test_static_favicon_svg_accessible_without_auth():
    """
    Verify that /static/favicon.svg is publicly accessible and not intercepted by enforce_auth.
    """
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.get("/static/favicon.svg")
        assert response.status_code == 200
        assert "image/svg+xml" in response.headers.get("Content-Type", "")
        assert b"<svg" in response.data


def test_static_favicon_png_variants_exist():
    """
    Verify that standard favicon PNG sizes and webmanifest exist in the static directory.
    """
    static_dir = Path(app.root_path) / "static"
    required_files = [
        "favicon.ico",
        "favicon.svg",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "apple-touch-icon.png",
        "site.webmanifest",
    ]
    for filename in required_files:
        asset_file = static_dir / filename
        assert asset_file.exists(), f"Static asset {filename} missing"
        assert asset_file.stat().st_size > 0, f"Static asset {filename} is empty"


def test_templates_include_favicon_links():
    """
    Verify that index.html, login.html, and setup.html include favicon link tags.
    """
    templates_dir = Path(app.root_path) / "templates"
    for template_name in ["index.html", "login.html", "setup.html"]:
        content = (templates_dir / template_name).read_text(encoding="utf-8")
        assert 'rel="icon"' in content, f"Favicon link missing from {template_name}"
        assert "favicon.svg" in content, f"favicon.svg link missing from {template_name}"
