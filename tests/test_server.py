"""The web UI's JSON API (gph.server) against an in-memory file and, when present, sample files."""

import base64
import io
import json
import os
import threading
import urllib.request
from http.client import HTTPConnection

import pytest
from PIL import Image

from gph import GphFile
from gph.container import Section, iter_gph_files
from gph.edit import set_color, set_image, set_text
from gph.inf import InfFile
from gph.server import make_server

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("GPH_SAMPLES", os.path.join(HERE, "..", "samples"))
FILES = sorted(iter_gph_files([SAMPLES])) if os.path.isdir(SAMPLES) else []


def _theme_bytes() -> bytes:
    """A tiny GEN_HAxx-like file: two colours, two textures, two images (one shares a texture)."""
    f = GphFile(name="GEN HA09")
    set_color(f, 0, "#102030")
    set_color(f, 1, "#ffcc00")
    red = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    set_image(f, 0, [red], "BMP8.4")
    set_image(f, 1, [red, Image.new("RGBA", (8, 8), (0, 255, 0, 255))], "DXT5")
    return f.to_bytes()


def _lang_bytes() -> bytes:
    f = GphFile(name="GEN DU  ")
    f.sections.append(Section("TEXT2006"))
    set_text(f, 0, "Hallo")
    set_text(f, 2, "Regel een\rregel twee")
    return f.to_bytes()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("srv")
    (d / "GEN_HA09.gph").write_bytes(_theme_bytes())
    (d / "GEN_DU.gph").write_bytes(_lang_bytes())
    srv = make_server([str(d)], port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, str(d)
    srv.shutdown()
    srv.server_close()


class Client:
    def __init__(self, srv):
        self.host, self.port = srv.server_address[:2]

    def call(self, method, path, body=None, raw=False):
        c = HTTPConnection(self.host, self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        r = c.getresponse()
        payload = r.read()
        c.close()
        if raw:
            return r.status, r.getheader("Content-Type"), payload
        return r.status, json.loads(payload)

    def ok(self, method, path, body=None):
        status, d = self.call(method, path, body)
        assert status == 200, d
        return d


@pytest.fixture
def client(server):
    return Client(server[0])


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _file(client, label):
    return next(f for f in client.ok("GET", "/api/files")["files"] if f["label"] == label)


def test_static_and_files(client, server):
    status, ctype, body = client.call("GET", "/", raw=True)
    assert status == 200 and ctype.startswith("text/html") and b"GPH editor" in body
    assert client.call("GET", "/app.js", raw=True)[0] == 200
    assert client.call("GET", "/../pyproject.toml", raw=True)[0] == 404
    d = client.ok("GET", "/api/files")
    labels = {f["label"] for f in d["files"]}
    assert {"GEN_HA09.gph", "GEN_DU.gph"} <= labels
    theme = _file(client, "GEN_HA09.gph")
    groups = {s["name"]: s["group"] for s in theme["sections"]}
    assert groups["TXCO2000"] == "colors" and groups["IMAG2000"] == "images" and groups["TXUR2008"] == "textures"
    assert theme["suggested_out"].endswith(os.path.join("out", os.path.basename(server[1]), "GEN_HA09.gph.bz2"))


def test_rows_and_pictures(client):
    fid = _file(client, "GEN_HA09.gph")["id"]
    rows = client.ok("GET", f"/api/files/{fid}/items?section=TXCO2000")["items"]
    assert rows[1]["hex"] == "ffcc00ff" and rows[1]["description"] == "Normal text"
    imgs = client.ok("GET", f"/api/files/{fid}/items?section=IMAG2000")["items"]
    assert imgs[1]["frames"][1]["texture"] == 2 and imgs[1]["width"] == 8
    tex = client.ok("GET", f"/api/files/{fid}/items?section=TXUR2008")["items"]
    assert tex[0]["format"] == "BMP8.4" and tex[1]["format"] == "DXT5" and tex[0]["users"] == [0]
    status, ctype, png = client.call("GET", f"/api/files/{fid}/texture/1.png", raw=True)
    assert status == 200 and ctype == "image/png"
    assert Image.open(io.BytesIO(png)).getpixel((0, 0)) == (255, 0, 0, 255)
    status, ctype, gif = client.call("GET", f"/api/files/{fid}/image/1.gif", raw=True)
    assert status == 200 and ctype == "image/gif" and Image.open(io.BytesIO(gif)).n_frames == 2
    detail = client.ok("GET", f"/api/files/{fid}/item/TXUR2008/2")
    assert detail["format"] == "DXT5" and detail["users"] == [1]
    assert client.call("GET", f"/api/files/{fid}/image/7.png", raw=True)[0] == 404


def test_edit_text_color_undo(client):
    lang = _file(client, "GEN_DU.gph")["id"]
    r = client.ok("POST", f"/api/files/{lang}/text/2", {"text": "Een\nTwee"})
    assert r["text"] == "Een\rTwee" and r["file"]["dirty"] and r["file"]["undo"] == 1
    r = client.ok("POST", f"/api/files/{lang}/text/5", {"text": "Nieuw"})  # grows the section
    rows = client.ok("GET", f"/api/files/{lang}/items?section=TEXT2006")["items"]
    assert [x["index"] for x in rows] == [0, 2, 5]
    r = client.ok("POST", f"/api/files/{lang}/undo", {})
    assert r["file"]["undo"] == 1
    rows = client.ok("GET", f"/api/files/{lang}/items?section=TEXT2006")["items"]
    assert [x["index"] for x in rows] == [0, 2] and rows[1]["text"] == "Een\rTwee"

    theme = _file(client, "GEN_HA09.gph")["id"]
    r = client.ok("POST", f"/api/files/{theme}/color/1", {"color": "#00ff0080"})
    assert r["hex"] == "00ff0080"
    status, d = client.call("POST", f"/api/files/{theme}/color/1", {"color": "nonsense"})
    assert status == 400 and "error" in d
    r = client.ok("PUT", f"/api/files/{theme}/item/IMAG2000/1", {"changes": {"time": 250, "flag": 3}})
    assert r["item"]["fields"]["time"] == 250 and r["item"]["fields"]["flag"] == 3


def test_replace_image_and_texture(client):
    theme = _file(client, "GEN_HA09.gph")["id"]
    blue = Image.new("RGBA", (6, 5), (0, 0, 255, 255))
    r = client.ok("POST", f"/api/files/{theme}/image/0", {"frames": [_png_b64(blue)], "format": "DXT1", "display": "image"})
    assert r["result"]["display"] == [6, 5] and r["result"]["slots"] == [0]
    assert r["item"]["fields"]["pictures"][0]["width_minus_1"] == 5
    tex = client.ok("GET", f"/api/files/{theme}/item/TXUR2008/0")
    assert tex["format"] == "DXT1" and tex["fields"]["width"] == 8  # padded
    status, ctype, png = client.call("GET", f"/api/files/{theme}/image/0.png?display=1", raw=True)
    assert Image.open(io.BytesIO(png)).size == (6, 5)
    # replace one texture directly; the image using it follows the new size
    r = client.ok("POST", f"/api/files/{theme}/texture/2", {"data_b64": _png_b64(Image.new("RGBA", (4, 4), (9, 9, 9, 255)))})
    assert r["item"]["fields"]["width"] == 4
    img = client.ok("GET", f"/api/files/{theme}/item/IMAG2000/1")
    assert img["fields"]["pictures"][1]["width_minus_1"] == 3
    status, d = client.call("POST", f"/api/files/{theme}/image/0", {"frames": ["bm90IGEgcG5n"]})
    assert status == 400 and "cannot read image" in d["error"]


def test_preview(client):
    img = Image.radial_gradient("L").convert("RGBA")
    r = client.ok("POST", "/api/preview", {"data_b64": _png_b64(img), "format": "DXT5"})
    assert r["format"] == "DXT5" and r["width"] == 256 and r["psnr"] > 30
    assert Image.open(io.BytesIO(base64.b64decode(r["png_b64"]))).size == (256, 256)
    r = client.ok("POST", "/api/preview", {"data_b64": _png_b64(Image.new("RGBA", (3, 3), (1, 2, 3, 4))), "format": "BMP8.4"})
    assert r["psnr"] >= 99


def test_save_revert_close(client, server, tmp_path):
    theme = _file(client, "GEN_HA09.gph")["id"]
    status, d = client.call("POST", f"/api/files/{theme}/save", {"out": os.path.join(server[1], "GEN_HA09.gph")})
    assert status == 400 and "overwrite" in d["error"]
    out = tmp_path / "HARM09"
    r = client.ok("POST", f"/api/files/{theme}/save", {"out": str(out) + os.sep, "version": "26.10.28.12.00"})
    path = r["saved"]["path"]
    assert path.endswith("GEN_HA09.gph.bz2") and r["saved"]["inf"].endswith(".inf")
    assert InfFile.verify_files(path) == (True, True) and r["saved"]["inf_fields"]["VER"] == "26.10.28.12.00"
    assert not r["file"]["dirty"]
    g = GphFile.load(path)
    assert g.compressed and g.section("IMAG2000").items[0] is not None
    # revert reloads the original from disk
    client.ok("POST", f"/api/files/{theme}/color/0", {"color": "#000000"})
    r = client.ok("POST", f"/api/files/{theme}/revert", {})
    assert not r["file"]["dirty"] and r["file"]["undo"] == 0
    rows = client.ok("GET", f"/api/files/{theme}/items?section=TXCO2000")["items"]
    assert rows[0]["hex"] == "102030ff"
    # open the saved copy, then close it again
    r = client.ok("POST", "/api/open", {"path": path})
    new_id = r["opened"][0]["id"]
    assert r["opened"][0]["compressed"]
    again = client.ok("POST", "/api/open", {"path": path})
    assert again["opened"] == [] and again["already_open"][0]["id"] == new_id
    client.ok("POST", f"/api/files/{new_id}/close", {})
    assert client.call("GET", f"/api/files/{new_id}/items?section=TXCO2000")[0] == 404


def test_upload_and_browse(client, server):
    r = client.ok("POST", "/api/upload", {"name": "GEN_DU.gph", "data_b64": base64.b64encode(_lang_bytes()).decode()})
    f = r["opened"][0]
    assert f["path"] is None and f["label"] == "GEN_DU.gph"
    status, d = client.call("POST", f"/api/files/{f['id']}/revert", {})
    assert status == 400
    client.ok("POST", f"/api/files/{f['id']}/close", {})
    b = client.ok("POST", "/api/browse", {"path": server[1]})
    assert {x["name"] for x in b["files"]} >= {"GEN_HA09.gph", "GEN_DU.gph"}
    assert client.call("POST", "/api/browse", {"path": os.path.join(server[1], "nope")})[0] == 404
    assert client.call("POST", "/api/open", {"path": os.path.join(server[1], "nope.gph")})[0] == 404


@pytest.mark.skipif(not FILES, reason="no sample GPH files")
def test_sample_files(tmp_path):
    srv = make_server([SAMPLES], port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        c = Client(srv)
        files = c.ok("GET", "/api/files")["files"]
        theme = next(f for f in files if f["name"] == "GEN HA00")
        rows = c.ok("GET", f"/api/files/{theme['id']}/items?section=IMAG2000")["items"]
        assert len(rows) > 500
        detail = c.ok("GET", f"/api/files/{theme['id']}/item/IMAG2000/{rows[0]['index']}")
        assert "used_by" in detail
        status, ctype, png = c.call("GET", f"/api/files/{theme['id']}/image/{rows[0]['index']}.png", raw=True)
        assert status == 200 and Image.open(io.BytesIO(png)).size[0] > 0
        common = next(f for f in files if f["name"] == "GEN COMM")
        menu = c.ok("GET", f"/api/files/{common['id']}/items?section=MENU2006")["items"]
        d = c.ok("GET", f"/api/files/{common['id']}/item/MENU2006/{menu[0]['index']}")
        assert d["described"]  # links resolved through the other open files
        fnt = next(f for f in files if f["name"] == "FNT COMM")
        assert c.call("GET", f"/api/files/{fnt['id']}/font/0.png", raw=True)[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_busy_port_falls_back_to_a_free_one(capsys):
    first = make_server([], port=0)
    try:
        second = make_server([], port=first.server_address[1])
        try:
            assert second.server_address[1] != first.server_address[1]
            assert "already in use" in capsys.readouterr().err
        finally:
            second.server_close()
    finally:
        first.server_close()
