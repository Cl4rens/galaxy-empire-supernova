"""Original item pictures: fetched from the wiki by the operator, served to the client."""

import pytest
from fastapi.testclient import TestClient

from supernova import icons, items, officers
from supernova.app import app

from . import test_server  # noqa: F401 (sets a client key, without which the world answers 503)

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"picture"  # served as it is


@pytest.fixture
def icon_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "icon_dir", lambda: tmp_path)
    return tmp_path


def test_items_keep_the_clients_picture_until_the_server_has_the_original(icon_dir):
    geologist = next(i for i in items.CATALOG if i.officer == (officers.GEOLOGIST, 1, 7))
    assert geologist.image() == f"icons/{officers.ICON}"
    (icon_dir / "officer_geologist_1.png").write_bytes(PNG)
    first = geologist.image()
    assert first.startswith("icons/officer_geologist_1-") and first.endswith(".png")
    # another picture under the same name gets another name, so clients load it again
    (icon_dir / "officer_geologist_1.png").write_bytes(PNG + b"new")
    import os
    os.utime(icon_dir / "officer_geologist_1.png", (1, 1))
    assert geologist.image() != first


def test_every_item_picture_comes_from_the_wiki_list():
    for i in items.CATALOG:
        assert not i.picture or i.picture in icons.WIKI_FILES, i.name


def test_server_serves_fetched_pictures_only(icon_dir):
    (icon_dir / "container_metal.png").write_bytes(PNG)
    client = TestClient(app)
    response = client.get("/world/1/" + icons.pick("container_metal.png", "x.png"))
    assert response.status_code == 200 and response.content == PNG
    assert client.get("/world/1/icons/container_metal.png").content == PNG
    assert client.get("/world/1/icons/container_gas.png").status_code == 404
    assert client.get("/world/1/icons/..%2Fsupernova.db").status_code == 404


def _image(size, color, fmt):
    import io

    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, fmt)
    return out.getvalue()


def test_import_takes_the_pictures_saved_from_the_wiki(icon_dir, tmp_path_factory, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(icons, "WIKI_FILES", {
        "a.png": "A_lv2@2x.png", "b.png": "A_lv2@2x.png", "c.png": "C.png", "d.png": "D.png",
        "e.png": icons.Source("E_store-1.png"),
        "f.png": icons.Source("F.png", crop=(10, 10, 60, 60), cover=(0, 0, 20, 20))})
    saved = tmp_path_factory.mktemp("saved")
    (saved / "A lv2@2x.png").write_bytes(_image((108, 108), "red", "PNG"))  # a browser may use spaces
    (saved / "C.png").write_bytes(b"<html>not a picture</html>")
    (saved / "E_store-1.webp").write_bytes(_image((160, 144), "blue", "WEBP"))  # browsers save WebP
    f = Image.new("RGBA", (100, 80), "green")
    f.paste((255, 255, 255, 255), (10, 10, 25, 25))  # a badge in the corner of the cut
    import io
    out = io.BytesIO()
    f.save(out, "PNG")
    (saved / "F.png").write_bytes(out.getvalue())
    assert icons.missing() == ["A_lv2@2x.png", "C.png", "D.png", "E_store-1.png", "F.png"]
    imported, problems = icons.import_folder(saved)
    assert imported == ["a.png", "b.png", "e.png", "f.png"]
    for name in imported:
        assert Image.open(icon_dir / name).size == (icons.SIZE, icons.SIZE)
    assert Image.open(icon_dir / "e.png").convert("RGB").getpixel((54, 54)) == (0, 0, 255)
    corner = Image.open(icon_dir / "f.png").convert("RGB").getpixel((10, 10))
    assert corner[1] > 100 and corner[0] < 200  # the badge is painted over with the green around
    assert any(p.startswith("C.png:") for p in problems)
    assert any(p.startswith("D.png: not in") for p in problems)
    assert icons.missing() == ["C.png", "D.png"]
