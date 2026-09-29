"""Looking at a clue at full resolution.

Every view of a render in this tool is downscaled: the gallery thumbnail and
the review pane both come from /api/frame, which caps the long side at 900 px.
A clue is a licence plate or a street sign occupying a few dozen pixels of a
2048-wide source, so at 900 px it is unreadable no matter how good the render
is — which means nobody, including the clue-writing pass, has ever actually
seen one at full size.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts.chonky.web import server

client = TestClient(server.app)


def _render(image_id, size=(2048, 2560)):
    Image.new("RGB", size, (90, 140, 200)).save(server.LIBRARY / f"{image_id}.png")


def _cleanup(*ids):
    for i in ids:
        (server.LIBRARY / f"{i}.png").unlink(missing_ok=True)
        (server.LIBRARY / f"{i}.json").unlink(missing_ok=True)
        server._images.pop(i, None)


def test_a_crop_comes_back_at_its_own_pixels():
    """No downscaling: the point is to see the source pixels as they are."""
    _render("crop-a")
    try:
        r = client.get("/api/crop/crop-a?box=100,200,420,440")
        assert r.status_code == 200
        with Image.open(io.BytesIO(r.content)) as im:
            assert im.size == (320, 240)
    finally:
        _cleanup("crop-a")


def test_a_crop_can_be_magnified_for_a_tiny_clue():
    """A plate 60 px wide needs enlarging to be judged at all."""
    _render("crop-b")
    try:
        r = client.get("/api/crop/crop-b?box=100,100,160,140&scale=4")
        with Image.open(io.BytesIO(r.content)) as im:
            assert im.size == (240, 160)
    finally:
        _cleanup("crop-b")


def test_a_crop_is_lossless():
    """A JPEG of a clue would add exactly the artefacts being judged."""
    _render("crop-c")
    try:
        r = client.get("/api/crop/crop-c?box=0,0,64,64")
        assert r.headers["content-type"] == "image/png"
    finally:
        _cleanup("crop-c")


def test_a_box_outside_the_picture_is_refused():
    _render("crop-d", size=(512, 640))
    try:
        assert client.get("/api/crop/crop-d?box=400,500,900,900").status_code == 400
        assert client.get("/api/crop/crop-d?box=100,100,50,50").status_code == 400
    finally:
        _cleanup("crop-d")


def test_a_malformed_box_is_refused():
    _render("crop-e")
    try:
        assert client.get("/api/crop/crop-e?box=1,2,3").status_code == 400
        assert client.get("/api/crop/crop-e?box=a,b,c,d").status_code == 400
    finally:
        _cleanup("crop-e")


def test_an_unknown_image_is_a_404():
    assert client.get("/api/crop/nope?box=0,0,10,10").status_code == 404
