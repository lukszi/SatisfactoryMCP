"""The request guard: every request must name this server, and only its own page may write."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.guard import refusal

BOUND = ("127.0.0.1", 8713)
PAGE = {"host": "127.0.0.1:8713", "origin": "http://127.0.0.1:8713"}


def test_reads_from_this_server_pass_whatever_origin_they_carry():
    for method in ("GET", "HEAD", "OPTIONS"):
        assert (
            refusal(method, {"host": PAGE["host"], "origin": "http://evil.example"}, BOUND) is None
        )
        assert refusal(method, {"host": "localhost:8713"}, BOUND) is None


def test_a_read_through_a_rebound_host_is_refused():
    for method in ("GET", "HEAD", "OPTIONS"):
        said = refusal(method, {"host": "evil.example:8713"}, BOUND)
        assert said and "Host" in said
    assert refusal("GET", {}, BOUND)


def test_the_page_itself_may_write():
    assert refusal("POST", PAGE, BOUND) is None
    assert refusal("DELETE", PAGE, BOUND) is None
    alias = {"host": "localhost:8713", "origin": "http://localhost:8713"}
    assert refusal("POST", alias, BOUND) is None


def test_a_rebound_host_is_refused():
    said = refusal(
        "POST", {"host": "evil.example:8713", "origin": "http://evil.example:8713"}, BOUND
    )
    assert said and "Host" in said


def test_another_port_is_another_server():
    headers = {"host": "127.0.0.1:8712", "origin": "http://127.0.0.1:8712"}
    assert refusal("POST", headers, BOUND)


def test_a_cross_origin_write_is_refused():
    said = refusal("POST", {"host": PAGE["host"], "origin": "http://evil.example"}, BOUND)
    assert said and "evil.example" in said
    assert refusal("POST", {"host": PAGE["host"], "origin": "null"}, BOUND)


def test_the_referer_stands_in_for_a_missing_origin_and_nothing_is_not_enough():
    page = {"host": PAGE["host"], "referer": "http://127.0.0.1:8713/#dash=factories"}
    assert refusal("POST", page, BOUND) is None
    other = {"host": PAGE["host"], "referer": "http://evil.example/x"}
    assert refusal("POST", other, BOUND)
    assert refusal("POST", {"host": PAGE["host"]}, BOUND)


def test_a_server_bound_off_loopback_takes_only_its_own_name():
    headers = {"host": "localhost:8713", "origin": "http://localhost:8713"}
    assert refusal("POST", headers, ("192.168.1.5", 8713))


def test_the_app_refuses_a_cross_origin_post_before_the_handler_runs(client):
    reply = client.post(
        "/api/labels",
        json={"name": "x", "proposal": 0, "as_of": "sav:000000000000"},
        headers={"origin": "http://evil.example"},
    )
    assert reply.status_code == 403
    assert "evil.example" in reply.json()["error"]
    assert (
        client.get("/api/factories", headers={"origin": "http://evil.example"}).status_code == 200
    )


def test_the_app_refuses_a_get_for_a_foreign_host_and_serves_its_own(client):
    refused = client.get("/api/factories", headers={"host": "evil.example"})
    assert refused.status_code == 403
    assert "evil.example" in refused.json()["error"]
    assert client.get("/api/factories", headers={"host": "testserver"}).status_code == 200
