import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, Operation, get_db
from app.keitaro import get_keitaro
from app.main import app
from test_service import FakeKeitaro


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fake = FakeKeitaro()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_keitaro] = lambda: fake
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            yield client, fake, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_create_normalizes_country_and_records_audit(api):
    client, fake, engine = api
    response = client.post("/api/campaigns", json={
        "name": settings.campaign_prefix + "api", "country": "au", "offer_id": 5,
        "domain_id": 1, "group_id": 1, "traffic_source_id": 1,
    })
    assert response.status_code == 201
    assert fake.calls[1][2]["filters"][0]["payload"] == ["AU"]
    with Session(engine) as session:
        operation = session.scalars(select(Operation)).one()
        assert (operation.action, operation.campaign_id) == ("create_campaign", 11)


def test_create_accepts_several_countries(api):
    client, fake, _ = api
    response = client.post("/api/campaigns", json={
        "name": settings.campaign_prefix + "geo", "country": "mx, au;ro AU", "offer_id": 5,
        "domain_id": 1, "group_id": 1, "traffic_source_id": 1,
    })
    assert response.status_code == 201
    assert fake.calls[1][2]["filters"][0]["payload"] == ["MX", "AU", "RO"]


@pytest.mark.parametrize("country", ["", "AUS", "A1", "MX,A", "MX-AU"])
def test_create_rejects_invalid_countries(api, country):
    client, fake, _ = api
    response = client.post("/api/campaigns", json={
        "name": settings.campaign_prefix + "geo", "country": country, "offer_id": 5,
        "domain_id": 1, "group_id": 1, "traffic_source_id": 1,
    })
    assert response.status_code == 422
    assert not fake.calls


def test_save_existing_zero_share_and_reject_stale_snapshot(api):
    client, fake, engine = api
    original = [{"offer_id": 5, "share": 100}, {"offer_id": 6, "share": 0}]
    fake.stream["offers"] = original
    body = {"expected_offers": original, "offers": [{"offer_id": 6, "share": 100}]}
    url = "/api/campaigns/11/streams/22/offers"
    saved = client.put(url, json=body)
    assert saved.status_code == 200
    assert saved.json()["offers"] == [{"offer_id": 6, "share": 100, "state": "active"}]
    assert client.put(url, json=body).status_code == 409
    assert len(fake.calls) == 1
    with Session(engine) as session:
        assert session.scalars(select(Operation)).one().details["from"] == original


@pytest.mark.parametrize("offers", [[], [{"offer_id": 5, "share": 0}], [{"offer_id": 5, "share": 99}]])
def test_invalid_allocation_never_writes_to_keitaro(api, offers):
    client, fake, _ = api
    response = client.put("/api/campaigns/11/streams/22/offers", json={
        "offers": offers, "expected_offers": [{"offer_id": 5, "share": 100}],
    })
    assert response.status_code == 422
    assert not fake.calls


def test_cannot_edit_stream_from_another_campaign(api):
    client, fake, _ = api
    response = client.put("/api/campaigns/11/streams/999/offers", json={
        "offers": [{"offer_id": 6, "share": 100}],
        "expected_offers": [{"offer_id": 5, "share": 100}],
    })
    assert response.status_code == 404
    assert not fake.calls


def test_page_and_script_are_served(api):
    client, _, _ = api
    assert 'id="create-form"' in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_migration_round_trip(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    url = "sqlite:///" + (tmp_path / "fresh.db").as_posix()
    monkeypatch.setattr(settings, "database_url", url)
    config = Config("alembic.ini")
    engine = create_engine(url)
    try:
        command.upgrade(config, "head")
        assert "operations" in inspect(engine).get_table_names()
        command.check(config)
        command.downgrade(config, "base")
        assert "operations" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "operations" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
