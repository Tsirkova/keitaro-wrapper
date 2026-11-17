import pytest

from app.config import settings
from app.keitaro import KeitaroError
from app.service import create_campaign, replace_offers, shares


class FakeKeitaro:
    def __init__(self):
        self.calls = []
        self.stream = {
            "id": 22, "campaign_id": 11, "schema": "landings", "type": "default",
            "offers": [{"offer_id": 5, "share": 100, "state": "active"}],
        }

    def get(self, path):
        if path == "offers":
            return [{"id": 5, "name": "A"}, {"id": 6, "name": "B"}]
        if path in ("domains", "groups?type=campaigns", "traffic_sources"):
            return [{"id": 1, "name": "FORTESTS"}]
        if path == "campaigns/11":
            return {"id": 11, "name": "test_surname_demo"}
        if path == "campaigns/11/streams":
            return [self.stream]
        if path == "streams/22":
            return self.stream
        raise AssertionError(path)

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path == "campaigns":
            return {"id": 11}
        return {"id": len(self.calls) + 20, **body}

    def put(self, path, body):
        self.calls.append(("PUT", path, body))
        self.stream["offers"] = body["offers"]

    def delete(self, path):
        self.calls.append(("DELETE", path, None))


def test_create_campaign_has_country_redirect_and_default_offer():
    fake = FakeKeitaro()
    result = create_campaign(
        fake, name=settings.campaign_prefix + "demo", country="AU", offer_id=5,
        domain_id=1, group_id=1, traffic_source_id=1,
    )
    assert result["campaign"]["id"] == 11
    campaign, geo, offer = [call[2] for call in fake.calls]
    assert campaign["type"] == "position"
    assert (campaign["domain_id"], campaign["group_id"], campaign["traffic_source_id"]) == (1, "1", 1)
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert geo["schema"] == "redirect"
    assert geo["action_payload"] == "https://www.google.com/"
    assert offer["type"] == "default"
    assert offer["schema"] == "landings"
    assert offer["offers"] == [{"offer_id": 5, "share": 100, "state": "active"}]


def test_offer_shares_sum_to_100():
    fake = FakeKeitaro()
    updated = replace_offers(fake, 11, 22,
        [{"offer_id": 5, "share": 50}, {"offer_id": 6, "share": 50}],
        [{"offer_id": 5, "share": 100}])
    assert [(x["offer_id"], x["share"]) for x in updated["offers"]] == [(5, 50), (6, 50)]
    updated = replace_offers(fake, 11, 22,
        [{"offer_id": 6, "share": 100}],
        [{"offer_id": 5, "share": 50}, {"offer_id": 6, "share": 50}])
    assert [(x["offer_id"], x["share"]) for x in updated["offers"]] == [(6, 100)]
    assert sum(x["share"] for x in shares(list(range(1, 8)))) == 100


def test_cannot_remove_last_offer():
    fake = FakeKeitaro()
    with pytest.raises(KeitaroError, match="sum to 100"):
        replace_offers(fake, 11, 22, [], [{"offer_id": 5, "share": 100}])
    assert not fake.calls


def test_duplicate_offer_is_rejected():
    fake = FakeKeitaro()
    with pytest.raises(KeitaroError, match="Duplicate"):
        replace_offers(fake, 11, 22,
            [{"offer_id": 5, "share": 50}, {"offer_id": 5, "share": 50}],
            [{"offer_id": 5, "share": 100}])


def test_failed_stream_creation_archives_incomplete_campaign():
    class FailingKeitaro(FakeKeitaro):
        def post(self, path, body):
            if path == "streams" and body["type"] == "default":
                raise KeitaroError(422, "invalid stream")
            return super().post(path, body)

    fake = FailingKeitaro()
    with pytest.raises(KeitaroError, match="invalid stream"):
        create_campaign(
            fake, name=settings.campaign_prefix + "demo", country="AU", offer_id=5,
            domain_id=1, group_id=1, traffic_source_id=1,
        )
    assert fake.calls[-1] == ("DELETE", "campaigns/11", None)


def test_save_draft_rebalances_and_rejects_stale_editor():
    fake = FakeKeitaro()
    offers = [{"offer_id": 5, "share": 50}, {"offer_id": 6, "share": 50}]
    saved = replace_offers(fake, 11, 22, offers, [{"offer_id": 5, "share": 100}])
    assert [x["share"] for x in saved["offers"]] == [50, 50]
    with pytest.raises(KeitaroError, match="Reload"):
        replace_offers(fake, 11, 22, [{"offer_id": 5, "share": 100}], [{"offer_id": 5, "share": 100}])
    assert len([call for call in fake.calls if call[0] == "PUT"]) == 1


def test_save_draft_preserves_pinned_share():
    fake = FakeKeitaro()
    fake.get = lambda path: (
        [{"id": 5}, {"id": 6}, {"id": 7}] if path == "offers" else
        [{"id": 22, "schema": "landings", "offers": [{"offer_id": 5}, {"offer_id": 6}]}]
        if path == "campaigns/11/streams" else
        fake.stream if path == "streams/22" else None
    )
    result = replace_offers(fake, 11, 22, [
        {"offer_id": 5, "share": 25},
        {"offer_id": 6, "share": 38},
        {"offer_id": 7, "share": 37},
    ], [{"offer_id": 5, "share": 100}])
    assert [offer["share"] for offer in result["offers"]] == [25, 38, 37]


def test_save_draft_rejects_incorrect_total():
    fake = FakeKeitaro()
    with pytest.raises(KeitaroError, match="sum to 100"):
        replace_offers(fake, 11, 22, [{"offer_id": 5, "share": 99}], [{"offer_id": 5, "share": 100}])
    assert not fake.calls


def test_save_draft_rejects_stale_shares_even_with_same_offer_ids():
    fake = FakeKeitaro()
    fake.stream["offers"] = [
        {"offer_id": 5, "share": 60}, {"offer_id": 6, "share": 40},
    ]
    with pytest.raises(KeitaroError, match="Reload"):
        replace_offers(fake, 11, 22,
            [{"offer_id": 5, "share": 100}],
            [{"offer_id": 5, "share": 50}, {"offer_id": 6, "share": 50}])
    assert not fake.calls
