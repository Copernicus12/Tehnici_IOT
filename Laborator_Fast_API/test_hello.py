import pytest
from fastapi.testclient import TestClient

from hello import DB, app


@pytest.fixture
def client():
    DB.clear()
    with TestClient(app) as test_client:
        yield test_client
    DB.clear()


@pytest.fixture
def reading_data():
    return {
        "dev": "senzor-1",
        "seq": 1,
        "ts": 1762096000,
        "temp": 21.5,
        "hum": 40,
    }


@pytest.fixture
def client_with_readings(client, reading_data):
    assert client.post("/readings", json=reading_data).status_code == 201
    second = {**reading_data, "seq": 2}
    second.pop("hum")
    assert client.post("/readings", json=second).status_code == 201
    return client


def test_01_post_reading(client, reading_data):
    response = client.post("/readings", json=reading_data)
    assert response.status_code == 201
    assert response.json() == reading_data
    saved = client.get("/devices/senzor-1/readings")
    assert saved.json() == [reading_data]


def test_02_duplicate_seq(client, reading_data):
    client.post("/readings", json=reading_data)
    response = client.post("/readings", json={**reading_data, "temp": 30})
    assert response.status_code == 409
    assert "seq=1" in response.json()["detail"]
    assert client.get("/devices/senzor-1/readings").json() == [reading_data]


def test_03_invalid_temperature(client, reading_data):
    response = client.post("/readings", json={**reading_data, "seq": 2, "temp": 100})
    assert response.status_code == 422
    assert client.get("/devices/senzor-1/readings").status_code == 404


def test_04_invalid_device(client, reading_data):
    response = client.post("/readings", json={**reading_data, "dev": "Senzor 1"})
    assert response.status_code == 422


def test_05_optional_humidity(client, reading_data):
    payload = {**reading_data, "seq": 2}
    payload.pop("hum")
    response = client.post("/readings", json=payload)
    assert response.status_code == 201
    assert response.json() == {**payload, "hum": None}


def test_06_get_readings(client_with_readings, reading_data):
    response = client_with_readings.get("/devices/senzor-1/readings")
    assert response.status_code == 200
    assert response.json() == [reading_data, {**reading_data, "seq": 2, "hum": None}]


def test_07_filter_since(client_with_readings, reading_data):
    response = client_with_readings.get("/devices/senzor-1/readings?since=1")
    assert response.status_code == 200
    assert response.json() == [{**reading_data, "seq": 2, "hum": None}]


def test_08_limit(client_with_readings, reading_data):
    response = client_with_readings.get("/devices/senzor-1/readings?limit=1")
    assert response.status_code == 200
    assert response.json() == [reading_data]


def test_09_invalid_limit(client_with_readings):
    response = client_with_readings.get("/devices/senzor-1/readings?limit=0")
    assert response.status_code == 422


def test_10_unknown_device(client):
    response = client.get("/devices/necunoscut/readings")
    assert response.status_code == 404
    assert "necunoscut" in response.json()["detail"]


def test_11_invalid_device_path(client):
    response = client.get("/devices/ABC/readings")
    assert response.status_code == 422


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dev", ""),
        ("dev", "a" * 33),
        ("seq", -1),
        ("ts", 1_600_000_000),
        ("temp", -40.1),
        ("hum", -0.1),
        ("hum", 100.1),
    ],
)
def test_invalid_body_boundaries(client, reading_data, field, value):
    response = client.post("/readings", json={**reading_data, field: value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


@pytest.mark.parametrize(("temp", "hum"), [(-40, 0), (85, 100), (21.5, None)])
def test_valid_body_boundaries(client, reading_data, temp, hum):
    payload = {
        **reading_data,
        "dev": "a" * 32,
        "seq": 0,
        "ts": 1_600_000_001,
        "temp": temp,
        "hum": hum,
    }
    response = client.post("/readings", json=payload)
    assert response.status_code == 201
    assert response.json() == payload


@pytest.mark.parametrize(
    "query",
    ["since=-1", "since=abc", "limit=101", "limit=abc", "min_temp=abc"],
)
def test_invalid_query(client, query):
    response = client.get(f"/devices/senzor-1/readings?{query}")
    assert response.status_code == 422


def test_readings_sorted_by_seq(client, reading_data):
    for seq in [3, 1, 2]:
        response = client.post("/readings", json={**reading_data, "seq": seq})
        assert response.status_code == 201
    response = client.get("/devices/senzor-1/readings?limit=2")
    assert response.status_code == 200
    assert [reading["seq"] for reading in response.json()] == [1, 2]


def test_no_readings_after_since(client_with_readings):
    response = client_with_readings.get("/devices/senzor-1/readings?since=2")
    assert response.status_code == 200
    assert response.json() == []


def test_seq_zero_is_saved_but_excluded_by_default_since(client, reading_data):
    assert client.post("/readings", json={**reading_data, "seq": 0}).status_code == 201
    response = client.get("/devices/senzor-1/readings")
    assert response.status_code == 200
    assert response.json() == []
    assert client.get("/devices").json() == [{"dev": "senzor-1", "count": 1}]


def test_same_seq_for_different_devices(client, reading_data):
    for dev in ["senzor-1", "senzor-2"]:
        response = client.post("/readings", json={**reading_data, "dev": dev})
        assert response.status_code == 201


def test_bonus_min_temp_before_limit(client, reading_data):
    for seq, temp in [(4, 30), (1, 10), (3, 20), (2, 10)]:
        payload = {**reading_data, "seq": seq, "temp": temp}
        assert client.post("/readings", json=payload).status_code == 201
    response = client.get("/devices/senzor-1/readings?since=1&min_temp=20&limit=1")
    assert response.status_code == 200
    assert response.json() == [{**reading_data, "seq": 3, "temp": 20}]


def test_bonus_devices_empty(client):
    response = client.get("/devices")
    assert response.status_code == 200
    assert response.json() == []


def test_bonus_devices_counts(client_with_readings, reading_data):
    response = client_with_readings.post(
        "/readings", json={**reading_data, "dev": "senzor-2"}
    )
    assert response.status_code == 201
    response = client_with_readings.get("/devices")
    assert response.status_code == 200
    assert response.json() == [
        {"dev": "senzor-1", "count": 2},
        {"dev": "senzor-2", "count": 1},
    ]
