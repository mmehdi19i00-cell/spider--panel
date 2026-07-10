"""Proof that the VLESS link generator reflects the REAL Xray inbound config.

Run:  python -m pytest tests/test_vless_generator.py -q
(or):  python tests/test_vless_generator.py
"""
import asyncio
import sys
import os
from urllib.parse import urlparse, parse_qs, unquote
import json

# Ensure repo root is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import services.xray_service as xs
from core import state as st


def _set_inbounds(inbounds: dict):
    st.INBOUNDS.clear()
    st.INBOUNDS.update(inbounds)


def test_reality_xhttp():
    _set_inbounds({
        "real1": {
            "name": "R+xhttp",
            "protocol": "vless",
            "port": 1234,            # internal listen port (MUST NOT appear)
            "network": "xhttp",
            "security": "reality",
            "external_domain": "hayabusa.proxy.rlwy.net",
            "external_port": 22124,
            "fingerprint": "chrome",
            "reality_settings": {
                "private_key": "AAAABBBBprivate",
                "public_key":  "h1Qcuf3ZA9ea9XEPkVN6jV1CClO2c7qhEjQo6fmZJnM",
                "short_ids": "5a3ff5a13d",
                "sni": "is1-ssl.mzstatic.com",
                "server_names": ["is1-ssl.mzstatic.com"],
                "spiderx": "/",
                "dest": "is1-ssl.mzstatic.com:443",
            },
            "xhttp_settings": {
                "path": "/",
                "mode": "auto",
                "xPaddingBytes": "100-1000",
                "scMaxEachPostBytes": "1000000",
            },
        }
    })
    uuid = "9432d43f-3685-4765-821a-27926dfc1ccb"
    link = xs.generate_vless_link(uuid=uuid, remark="spider-test", inbound_id="real1")

    q = parse_qs(urlparse(link).query, keep_blank_values=True)
    assert link.startswith("vless://"), link
    assert "@hayabusa.proxy.rlwy.net:22124" in link, link
    assert "1234" not in link, "internal port leaked into link!"
    assert q["security"][0] == "reality"
    assert q["type"][0] == "xhttp"
    assert q["pbk"][0] == "h1Qcuf3ZA9ea9XEPkVN6jV1CClO2c7qhEjQo6fmZJnM"
    assert q["sid"][0] == "5a3ff5a13d"
    assert q["sni"][0] == "is1-ssl.mzstatic.com"
    assert q["fp"][0] == "chrome"
    assert q["spx"][0] == "/"          # parse_qs decodes %2F -> /
    assert "spx=%2F" in link, "spx must be encoded as %2F"
    assert q["mode"][0] == "auto"
    # extra must be URL-encoded JSON carrying xPaddingBytes + scMaxEachPostBytes
    extra = json.loads(unquote(q["extra"][0]))
    assert extra["xPaddingBytes"] == "100-1000"
    assert extra["scMaxEachPostBytes"] == "1000000"
    assert "host=" not in link, "ws host param must not appear for xhttp"
    print("Reality+xhttp OK:\n ", link)


def test_reality_ws():
    _set_inbounds({
        "realws": {
            "name": "R+ws",
            "protocol": "vless",
            "port": 1234,
            "network": "ws",
            "security": "reality",
            "external_domain": "hayabusa.proxy.rlwy.net",
            "external_port": 22124,
            "fingerprint": "chrome",
            "reality_settings": {
                "private_key": "pk",
                "public_key":  "PUBKEY123",
                "short_ids": "abcdef1234",
                "sni": "is1-ssl.mzstatic.com",
                "server_names": ["is1-ssl.mzstatic.com"],
                "spiderx": "/",
            },
            "ws_settings": {"path": "/myrealws", "host": "hayabusa.proxy.rlwy.net"},
        }
    })
    uuid = "11111111-2222-3333-4444-555555555555"
    link = xs.generate_vless_link(uuid=uuid, remark="spider-test", inbound_id="realws")
    q = parse_qs(urlparse(link).query, keep_blank_values=True)
    assert q["security"][0] == "reality"
    assert q["type"][0] == "ws"
    assert q["pbk"][0] == "PUBKEY123"
    assert q["sid"][0] == "abcdef1234"
    assert q["path"][0] == "/myrealws", link
    print("Reality+ws OK:\n ", link)


def test_tls_ws():
    _set_inbounds({
        "tlsws": {
            "name": "TLS+ws",
            "protocol": "vless",
            "port": 4433,
            "network": "ws",
            "security": "tls",
            "external_domain": "panel.example.com",
            "external_port": 443,
            "sni": "panel.example.com",
            "fingerprint": "chrome",
            "ws_settings": {"path": "/wspublic", "host": "panel.example.com"},
        }
    })
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    link = xs.generate_vless_link(uuid=uuid, remark="spider-test", inbound_id="tlsws")
    q = parse_qs(urlparse(link).query, keep_blank_values=True)
    assert q["security"][0] == "tls"
    assert q["type"][0] == "ws"
    assert "pbk" not in q, "pbk must NOT appear for non-reality"
    assert q["path"][0] == "/wspublic"
    print("TLS+ws OK:\n ", link)


def test_reality_incomplete_raises():
    _set_inbounds({
        "broken": {
            "name": "broken reality",
            "protocol": "vless",
            "port": 1234,
            "network": "tcp",
            "security": "reality",
            "external_domain": "hayabusa.proxy.rlwy.net",
            "external_port": 22124,
            "reality_settings": {
                # missing private_key/public_key AND sni
                "short_ids": "abcdef1234",
            },
        }
    })
    uuid = "00000000-0000-0000-0000-000000000000"
    caught = None
    try:
        xs.generate_vless_link(uuid=uuid, inbound_id="broken")
        assert False, "expected RealityIncompleteError"
    except xs.RealityIncompleteError as e:
        caught = e
        assert "pbk" in e.missing and "sni" in e.missing, e.missing
    print("RealityIncompleteError raised correctly:", caught.missing)


def main():
    test_reality_xhttp()
    test_reality_ws()
    test_tls_ws()
    test_reality_incomplete_raises()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
