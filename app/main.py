from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Operation, get_db
from app.keitaro import KeitaroClient, KeitaroError, get_keitaro
from app.service import create_campaign, replace_offers

app = FastAPI(title="Adrobot Keitaro Wrapper")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class CampaignCreate(BaseModel):
    name: str = Field(min_length=4, max_length=200)
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

    @field_validator("country")
    @classmethod
    def uppercase_country(cls, value: str) -> str:
        return value.upper()


class OfferAllocation(BaseModel):
    offer_id: int = Field(gt=0)
    share: int = Field(gt=0, le=100)


class OfferReplace(BaseModel):
    offers: list[OfferAllocation] = Field(min_length=1, max_length=100)
    expected_offers: list[OfferAllocation]


@app.exception_handler(KeitaroError)
async def keitaro_error_handler(_, exc: KeitaroError):
    from fastapi.responses import JSONResponse

    status = exc.status_code if 400 <= exc.status_code < 600 else 502
    return JSONResponse(status_code=status, content={"detail": exc.message})


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/bootstrap")
def bootstrap(client: KeitaroClient = Depends(get_keitaro)):
    campaigns = client.get("campaigns")
    return {
        "configured_prefix": settings.campaign_prefix,
        "domains": client.get("domains"),
        "groups": client.get("groups?type=campaigns"),
        "sources": client.get("traffic_sources"),
        "offers": client.get("offers"),
        "campaigns": [c for c in campaigns if c.get("state") != "deleted"],
    }


@app.post("/api/campaigns", status_code=201)
def create(
    body: CampaignCreate,
    client: KeitaroClient = Depends(get_keitaro),
    db: Session = Depends(get_db),
):
    result = create_campaign(client, **body.model_dump())
    db.add(Operation(
        action="create_campaign", campaign_id=result["campaign"]["id"],
        details={"name": body.name, "country": body.country, "offer_id": body.offer_id},
    ))
    db.commit()
    return result


@app.get("/api/campaigns/{campaign_id}")
def campaign_detail(campaign_id: int, client: KeitaroClient = Depends(get_keitaro)):
    campaign = client.get(f"campaigns/{campaign_id}")
    if not campaign or campaign.get("state") == "deleted":
        raise HTTPException(404, "Campaign not found")
    streams = client.get(f"campaigns/{campaign_id}/streams")
    return {"campaign": campaign, "streams": streams}


@app.put("/api/campaigns/{campaign_id}/streams/{stream_id}/offers")
def save_offers(
    campaign_id: int, stream_id: int, body: OfferReplace,
    client: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db),
):
    stream = replace_offers(
        client, campaign_id, stream_id,
        [offer.model_dump() for offer in body.offers],
        [offer.model_dump() for offer in body.expected_offers],
    )
    db.add(Operation(
        action="replace_offers", campaign_id=campaign_id, stream_id=stream_id,
        details={"from": [offer.model_dump() for offer in body.expected_offers],
                 "to": [offer.model_dump() for offer in body.offers]},
    ))
    db.commit()
    return stream
