from collections.abc import Sequence

from app.config import settings
from app.keitaro import KeitaroClient, KeitaroError, campaign_path, safe_alias, stream_path


def selection(items: Sequence[dict], item_id: int, kind: str) -> dict:
    match = next((
        x for x in items
        if x.get("id") == item_id and x.get("state", "active") == "active"
    ), None)
    if not match:
        raise KeitaroError(422, f"Invalid {kind} ID: {item_id}")
    return match


def shares(offer_ids: Sequence[int]) -> list[dict]:
    if not offer_ids:
        raise KeitaroError(422, "An offer flow needs at least one offer")
    if len(offer_ids) > 100:
        raise KeitaroError(422, "A stream cannot have more than 100 offers with integer shares")
    base, extra = divmod(100, len(offer_ids))
    return [
        {"offer_id": offer_id, "share": base + (1 if index < extra else 0), "state": "active"}
        for index, offer_id in enumerate(offer_ids)
    ]


def create_campaign(
    client: KeitaroClient, *, name: str, country: str, offer_id: int,
    domain_id: int, group_id: int, traffic_source_id: int,
) -> dict:
    if not name.startswith(settings.campaign_prefix):
        raise KeitaroError(422, f"Campaign name must start with {settings.campaign_prefix}")
    offers = client.get("offers")
    domains = client.get("domains")
    groups = client.get("groups?type=campaigns")
    sources = client.get("traffic_sources")
    selection(offers, offer_id, "offer")
    selection(domains, domain_id, "domain")
    selection(groups, group_id, "group")
    selection(sources, traffic_source_id, "traffic source")

    campaign = client.post("campaigns", {
        "name": name, "alias": safe_alias(name), "type": "position",
        "domain_id": domain_id, "group_id": str(group_id),
        "traffic_source_id": traffic_source_id,
    })
    campaign_id = campaign["id"]
    try:
        # A regular flow with a country filter is checked first. The default flow
        # catches every other click, matching the position-based UI configuration.
        google = client.post("streams", {
            "campaign_id": campaign_id, "name": "Country → Google",
            "type": "regular", "position": 1, "schema": "redirect",
            "action_type": "http", "action_payload": settings.google_redirect_url,
            "filters": [{"name": "country", "mode": "accept", "payload": [country]}],
        })
        offer = client.post("streams", {
            "campaign_id": campaign_id, "name": "Offer",
            "type": "default", "position": 2, "schema": "landings",
            "action_type": "http", "offers": shares([offer_id]),
        })
    except Exception:
        # Keitaro does not offer a transaction across these API calls.
        # Archive the incomplete campaign so it cannot receive traffic.
        try:
            client.delete(campaign_path(campaign_id))
        except Exception:
            pass
        raise
    return {"campaign": client.get(campaign_path(campaign_id)), "streams": [google, offer]}


def editable_stream(client: KeitaroClient, campaign_id: int, stream_id: int) -> dict:
    streams = client.get(f"campaigns/{campaign_id}/streams")
    stream = next((x for x in streams if x.get("id") == stream_id), None)
    if not stream or stream.get("state") == "deleted":
        raise KeitaroError(404, "Stream not found in this campaign")
    if stream.get("schema") != "landings":
        raise KeitaroError(422, "This stream does not route to offers")
    return client.get(stream_path(stream_id))


def replace_offers(
    client: KeitaroClient, campaign_id: int, stream_id: int,
    offers: list[dict], expected_offers: list[dict],
) -> dict:
    stream = editable_stream(client, campaign_id, stream_id)
    current_offers = [
        {"offer_id": x["offer_id"], "share": x["share"]}
        for x in stream.get("offers", []) if x.get("state") != "disabled"
    ]
    if current_offers != expected_offers:
        raise KeitaroError(409, "Offers changed in Keitaro. Reload the campaign before applying your draft")
    offer_ids = [offer["offer_id"] for offer in offers]
    if len(set(offer_ids)) != len(offer_ids):
        raise KeitaroError(422, "Duplicate offer IDs are not allowed")
    if not offers or any(offer["share"] < 1 for offer in offers) or sum(offer["share"] for offer in offers) != 100:
        raise KeitaroError(422, "Active offer shares must sum to 100")
    available = client.get("offers")
    for offer_id in offer_ids:
        selection(available, offer_id, "offer")
    client.put(stream_path(stream_id), {"offers": [{**offer, "state": "active"} for offer in offers]})
    return client.get(stream_path(stream_id))
