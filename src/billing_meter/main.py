"""App entrypoint stub — routes land in a later phase."""

from fastapi import FastAPI

app = FastAPI(title="billing-meter")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
