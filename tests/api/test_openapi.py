"""The OpenAPI page at /docs is the mocked client, so its examples must work and its
error responses must match what the API sends."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts.seed import seed

pytestmark = pytest.mark.anyio


async def openapi(client: AsyncClient) -> dict:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


async def test_booking_example_books_on_seed_data(engine: AsyncEngine, client: AsyncClient) -> None:
    # real clock: "Try it out" in /docs must work on the day a reviewer opens it
    async with engine.begin() as conn:
        await seed(conn)
    spec = await openapi(client)
    examples = spec["paths"]["/appointments"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    example = next(iter(examples.values()))["value"]
    assert datetime.fromisoformat(example["start"]) > datetime.now(UTC)
    response = await client.post("/appointments", json=example)
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("path", "method", "codes"),
    [
        ("/appointments", "post", {"409", "422", "503"}),
        ("/appointments/{appointment_id}", "get", {"404", "422", "503"}),
        ("/appointments/{appointment_id}/cancel", "post", {"404", "422", "503"}),
    ],
)
async def test_error_responses_are_documented_as_problem_json(
    client: AsyncClient, path: str, method: str, codes: set[str]
) -> None:
    spec = await openapi(client)
    responses = spec["paths"][path][method]["responses"]
    assert codes <= responses.keys()
    for code in codes:
        schema = responses[code]["content"]["application/problem+json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/Problem"}, (code, responses[code])
