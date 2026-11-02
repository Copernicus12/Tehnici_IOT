from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

app = FastAPI(title="TPI IoT API")

DEV_PATTERN = r"^[a-z0-9-]{1,32}$"


class Reading(BaseModel):
    dev: str = Field(pattern=DEV_PATTERN)
    seq: int = Field(ge=0)
    ts: int = Field(gt=1_600_000_000)
    temp: float = Field(ge=-40, le=85)
    hum: float | None = Field(default=None, ge=0, le=100)


DB: dict[str, dict[int, Reading]] = {}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/readings", status_code=201)
def create_reading(reading: Reading) -> Reading:
    device_readings = DB.setdefault(reading.dev, {})

    if reading.seq in device_readings:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Citirea cu seq={reading.seq} există deja "
                f"pentru dispozitivul '{reading.dev}'."
            ),
        )

    device_readings[reading.seq] = reading
    return reading


@app.get("/devices/{dev_id}/readings")
def readings(
    dev_id: str = Path(pattern=DEV_PATTERN),
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    min_temp: float | None = Query(default=None),
) -> list[Reading]:
    device_readings = DB.get(dev_id)
    if not device_readings:
        raise HTTPException(
            status_code=404,
            detail=f"Dispozitivul '{dev_id}' nu are citiri salvate.",
        )

    filtered = [
        reading
        for reading in device_readings.values()
        if reading.seq > since
        and (min_temp is None or reading.temp >= min_temp)
    ]
    return sorted(filtered, key=lambda reading: reading.seq)[:limit]


@app.get("/devices")
def devices() -> list[dict[str, str | int]]:
    return [
        {"dev": dev_id, "count": len(device_readings)}
        for dev_id, device_readings in sorted(DB.items())
    ]
