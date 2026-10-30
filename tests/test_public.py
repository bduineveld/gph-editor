"""Public (online) mode of gph.server: a session per visitor, no disk access, downloads."""

import base64
import http.cookiejar
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile

import pytest

from gph.server import PUBLIC_MAX_FILES, make_server
from test_screens import packed, sample_elf
from test_server import _theme_bytes


@pytest.fixture(scope="module")
def public_server():
    srv = make_server([], port=0, public=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


class Visitor:
    """An HTTP client with its own cookie jar."""

    def __init__(self, srv):
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, {"Content-Type": "application/json"} if data else {}, method=method)
        try:
            with self.opener.open(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def json(self, method, path, body=None):
        status, _h, raw = self.call(method, path, body)
        return status, json.loads(raw)

    def upload(self, name, data):
        return self.json("POST", "/api/upload", {"name": name, "data_b64": base64.b64encode(data).decode()})


def test_sessions_are_separate(public_server):
    a, b = Visitor(public_server), Visitor(public_server)
    st, d = a.json("GET", "/api/settings")
    assert st == 200 and d["public"] is True
    assert any(c.name == "gph_sid" for c in a.jar), "first contact sets the session cookie"
    st, d = a.upload("GEN_HA09.gph", _theme_bytes())
    assert st == 200 and len(d["opened"]) == 1
    assert a.json("GET", "/api/files")[1]["files"][0]["label"] == "GEN_HA09.gph"
    assert a.json("GET", "/api/files")[1]["cwd"] is None  # the server's folder is not revealed
    assert b.json("GET", "/api/files")[1]["files"] == []   # another visitor sees nothing of it
    # the same visitor keeps the session across requests
    assert len(a.json("GET", "/api/files")[1]["files"]) == 1


def test_disk_routes_are_blocked(public_server):
    v = Visitor(public_server)
    assert v.json("POST", "/api/open", {"path": "/"})[0] == 403
    assert v.json("POST", "/api/browse", {"path": "/"})[0] == 403
    assert v.json("POST", "/api/screens/open", {"path": "/x"})[0] == 403
    st, d = v.upload("GEN_HA09.gph", _theme_bytes())
    fid = d["opened"][0]["id"]
    assert v.json("POST", f"/api/files/{fid}/save", {"out": "/tmp/x"})[0] == 403
    assert v.json("POST", f"/api/files/{fid}/revert", {})[0] == 403
    assert v.json("POST", f"/api/files/{fid}/undo", {})[0] == 400  # allowed, just nothing to undo


def test_download_zip(public_server):
    v = Visitor(public_server)
    _st, d = v.upload("GEN_HA09.gph", _theme_bytes())
    fid = d["opened"][0]["id"]
    v.json("POST", f"/api/files/{fid}/color/0", {"color": "#11223344"})
    assert v.json("GET", "/api/files")[1]["files"][0]["dirty"] is True
    st, headers, raw = v.call("GET", f"/api/files/{fid}/download.zip?version=26.10.30.12.00")
    assert st == 200 and headers["Content-Type"] == "application/zip"
    assert 'filename="GEN_HA09.zip"' in headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = sorted(z.namelist())
        assert names == ["GEN_HA09.gph.bz2", "GEN_HA09.gph.bz2.inf"]
        assert b"VER:26.10.30.12.00" in z.read("GEN_HA09.gph.bz2.inf")
        assert z.read("GEN_HA09.gph.bz2")[:3] == b"BZh"
    assert v.json("GET", "/api/files")[1]["files"][0]["dirty"] is False


def test_upload_graphic_base_and_limits(public_server):
    v = Visitor(public_server)
    st, d = v.upload("GraphicBase.RNEG.gz", packed(sample_elf()))
    assert st == 200 and d["opened"] == [] and d["screens"]["count"] == 2
    assert v.json("GET", "/api/settings")[1]["screens"]["count"] == 2
    assert [s["id"] for s in v.json("GET", "/api/screens")[1]["screens"]] == [7, 8]
    # another visitor does not get this database
    assert Visitor(public_server).json("GET", "/api/settings")[1]["screens"] is None
    for _ in range(PUBLIC_MAX_FILES):
        assert v.upload("GEN_HA09.gph", _theme_bytes())[0] == 200
    st, d = v.upload("GEN_HA09.gph", _theme_bytes())
    assert st == 400 and "at most" in d["error"]


@pytest.fixture(scope="module")
def preloaded_server(tmp_path_factory):
    """A public server with files preloaded from a folder (GPH_PRELOAD on the online version)."""
    d = tmp_path_factory.mktemp("preload")
    (d / "HARM09").mkdir()
    (d / "HARM09" / "GEN_HA09.gph").write_bytes(_theme_bytes())
    (d / "Graphics").mkdir()
    (d / "Graphics" / "GraphicBase.RNEG.gz").write_bytes(packed(sample_elf()))
    srv = make_server([str(d)], port=0, public=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_preloaded_files_are_shared_until_edited(preloaded_server):
    a, b = Visitor(preloaded_server), Visitor(preloaded_server)
    fa = a.json("GET", "/api/files")[1]["files"]
    assert [f["label"] for f in fa] == ["GEN_HA09.gph"] and fa[0]["path"] is None and fa[0]["dirty"] is False  # file name kept, path hidden
    assert a.json("GET", "/api/settings")[1]["screens"]["count"] == 2
    fid = fa[0]["id"]
    before = a.json("GET", f"/api/files/{fid}/items?section=TXCO2000")[1]["items"][0]["hex"]
    # a edits: a sees the change, b and a fresh visitor still see the server's copy
    st, _ = a.json("POST", f"/api/files/{fid}/color/0", {"color": "#12345678"})
    assert st == 200
    assert a.json("GET", f"/api/files/{fid}/items?section=TXCO2000")[1]["items"][0]["hex"] == "12345678"
    assert b.json("GET", f"/api/files/{fid}/items?section=TXCO2000")[1]["items"][0]["hex"] == before
    assert Visitor(preloaded_server).json("GET", f"/api/files/{fid}/items?section=TXCO2000")[1]["items"][0]["hex"] == before
    # undo goes back to the shared content; the download works on the visitor's copy
    assert a.json("POST", f"/api/files/{fid}/undo", {})[0] == 200
    assert a.json("GET", f"/api/files/{fid}/items?section=TXCO2000")[1]["items"][0]["hex"] == before
    st, headers, raw = a.call("GET", f"/api/files/{fid}/download.zip")
    assert st == 200 and zipfile.ZipFile(io.BytesIO(raw)).namelist()[0] == "GEN_HA09.gph.bz2"
    # closing it only affects this visitor
    assert a.json("POST", f"/api/files/{fid}/close", {})[0] == 200
    assert a.json("GET", "/api/files")[1]["files"] == []
    assert len(b.json("GET", "/api/files")[1]["files"]) == 1
