"""Anchoring the render to a photograph of the actual place.

Until now the picture was the image model's impression of a city, drawn from
training data and constrained only by text. The place was verified; the
picture was never checked against it, and nothing in the pipeline could.

The model takes up to sixteen visual references and we spend one on Chonky.
Giving it a real photograph of the viewpoint is what turns "a plausible Cusco
street" into "that street".
"""

import json

import pytest
from PIL import Image

from scripts.chonky import reference_photo as RP

fetch_reference = RP.fetch_reference


def _png_bytes(size=(800, 600)):
    import io
    buf = io.BytesIO()
    Image.new("RGB", size, (100, 130, 160)).save(buf, format="PNG")
    return buf.getvalue()


def test_it_saves_the_photo_and_reports_where_it_came_from(tmp_path):
    out = fetch_reference("https://example.org/street.png", tmp_path / "ref.png",
                          opener=lambda url: ("image/png", _png_bytes()))
    assert out["path"].exists()
    assert out["source_url"] == "https://example.org/street.png"


def test_the_saved_file_is_a_real_image(tmp_path):
    out = fetch_reference("https://example.org/street.png", tmp_path / "ref.png",
                          opener=lambda url: ("image/png", _png_bytes()))
    with Image.open(out["path"]) as im:
        assert im.size == (800, 600)


def test_something_that_is_not_an_image_is_refused(tmp_path):
    with pytest.raises(RP.ReferenceError):
        fetch_reference("https://example.org/page.html", tmp_path / "ref.png",
                        opener=lambda url: ("text/html", b"<html>nope</html>"))


def test_a_thumbnail_is_refused(tmp_path):
    """A 90 px thumbnail anchors nothing; it would just add noise."""
    with pytest.raises(RP.ReferenceError):
        fetch_reference("https://example.org/tiny.png", tmp_path / "ref.png",
                        opener=lambda url: ("image/png", _png_bytes((90, 70))))


def test_a_download_failure_is_reported_not_swallowed(tmp_path):
    def boom(url):
        raise OSError("connection reset")

    with pytest.raises(RP.ReferenceError):
        fetch_reference("https://example.org/x.png", tmp_path / "ref.png",
                        opener=boom)


def test_no_url_is_not_an_error(tmp_path):
    """A viewpoint with no findable photo still renders, just unanchored."""
    assert fetch_reference("", tmp_path / "ref.png", opener=lambda u: None) is None
