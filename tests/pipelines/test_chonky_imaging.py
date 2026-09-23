import io
import random

from PIL import Image

from scripts.chonky.geometry import IMG_W, IMG_H, VF_W, VF_H, VF_X0, VF_Y0
from scripts.chonky.imaging import encode_delivery, viewframe_crop
from scripts.chonky import imaging


def _photo_like():
    """A source that compresses the way a photograph does.

    Flat colour compresses to almost nothing and never exercises the size
    search; pure random noise is maximally incompressible and no quality
    setting fits it. A smooth gradient with mild grain behaves like a real
    render: large areas of correlated colour with fine detail on top.
    """
    rnd = random.Random(1)
    img = Image.new("RGB", (IMG_W, IMG_H))
    px = img.load()
    for y in range(IMG_H):
        base_g = (y * 255) // IMG_H
        for x in range(IMG_W):
            base_r = (x * 255) // IMG_W
            px[x, y] = (
                min(255, max(0, base_r + rnd.randrange(-18, 19))),
                min(255, max(0, base_g + rnd.randrange(-18, 19))),
                min(255, max(0, 180 + rnd.randrange(-18, 19))),
            )
    return img


def test_viewframe_crop_is_exactly_the_authored_window():
    img = Image.new("RGB", (IMG_W, IMG_H), (10, 20, 30))
    img.putpixel((VF_X0, VF_Y0), (255, 0, 0))
    crop = viewframe_crop(img)
    assert crop.size == (VF_W, VF_H)
    assert crop.getpixel((0, 0)) == (255, 0, 0)


def test_encode_delivery_returns_jpeg():
    data, _ = encode_delivery(Image.new("RGB", (IMG_W, IMG_H), (90, 110, 130)))
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_encode_delivery_respects_the_hard_ceiling():
    data, quality = encode_delivery(_photo_like())
    assert len(data) <= 1300 * 1024, f"{len(data)//1024} KB at q{quality}"


def test_encode_delivery_prefers_the_highest_quality_that_fits():
    data, quality = encode_delivery(_photo_like(), max_kb=1200)
    assert len(data) // 1024 <= 1200
    assert quality <= 92


def test_the_viewframe_crop_stays_inside_a_smaller_render():
    """PIL pads a crop that runs past the edge with black instead of failing.

    Against the reference constants a 1344x1680 render would be cropped 666 px
    below its own bottom edge, and the delivered image would carry a black bar
    that nothing in the pipeline would have flagged.
    """
    img = Image.new("RGB", (1344, 1680), (200, 180, 140))
    crop = imaging.viewframe_crop(img)

    assert crop.width <= img.width and crop.height <= img.height
    assert abs(crop.width / crop.height - 1200 / 2133) < 0.01, "not 9:16"

    # No padded black anywhere: every pixel came from the picture.
    assert crop.getextrema() != ((0, 0), (0, 0), (0, 0))
    darkest = min(min(ch) for ch in crop.getextrema())
    assert darkest > 0, "crop ran past the edge and was padded with black"
