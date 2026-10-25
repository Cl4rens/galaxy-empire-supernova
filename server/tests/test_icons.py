"""Original item pictures: fetched from the wiki by the operator, served to the client."""

import pytest
from fastapi.testclient import TestClient

from supernova import icons, items, officers
from supernova.app import app

from . import test_server  # noqa: F401 (sets a client key, without which the world answers 503)

PNG = icons.PNG + b"picture"


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


def test_import_takes_the_pictures_saved_from_the_wiki(icon_dir, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(icons, "WIKI_FILES", {"a.png": "A_lv2@2x.png", "b.png": "A_lv2@2x.png", "c.png": "C.png",
                                              "d.png": "D.png"})
    saved = tmp_path_factory.mktemp("saved")
    (saved / "A lv2@2x.png").write_bytes(PNG)  # a browser may save the name with spaces
    (saved / "C.png").write_bytes(b"<html>not a picture</html>")
    assert icons.missing() == ["A_lv2@2x.png", "C.png", "D.png"]
    imported, problems = icons.import_folder(saved)
    assert imported == ["a.png", "b.png"]
    assert (icon_dir / "a.png").read_bytes() == PNG and (icon_dir / "b.png").read_bytes() == PNG
    assert any(p.startswith("C.png: not a PNG") for p in problems)
    assert any(p.startswith("D.png: not in") for p in problems)
    assert icons.missing() == ["C.png", "D.png"]
