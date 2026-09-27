"""The Pinecone client over a mock transport: what it sends, and what it refuses to accept back."""

import json
from collections.abc import Callable

import httpx2
import pytest
from pydantic import SecretStr

from tillhand.core.deadlines import StepTimeout
from tillhand.integrations.pinecone import API_VERSION, EMBED_URL, EmbeddingError, PineconeEmbedder

pytestmark = pytest.mark.anyio

MODEL = "llama-text-embed-v2"
Handler = Callable[[httpx2.Request], httpx2.Response]


def answer(*, count: int | None = None, model: str = MODEL, dimension: int = 1024) -> Handler:
    """A handler answering like Pinecone, one vector per input unless `count` says otherwise."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(request.content)["inputs"]
        n = len(inputs) if count is None else count
        body = {
            "model": model,
            "vector_type": "dense",
            "data": [{"values": [0.5] * dimension, "vector_type": "dense"} for _ in range(n)],
            "usage": {"total_tokens": 7 * n},
        }
        return httpx2.Response(200, json=body)

    return handle


def embedder(handler: Handler, requests: list[httpx2.Request] | None = None) -> PineconeEmbedder:
    def record(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return handler(request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(record))
    return PineconeEmbedder(client, api_key=SecretStr("test-key"), model=MODEL, dimension=1024)


async def test_a_query_is_embedded_as_a_query() -> None:
    requests: list[httpx2.Request] = []
    vector = await embedder(answer(), requests).embed_query("serum for oily skin")
    assert len(vector) == 1024
    (request,) = requests
    assert str(request.url) == EMBED_URL
    assert request.headers["Api-Key"] == "test-key"
    assert request.headers["X-Pinecone-Api-Version"] == API_VERSION
    assert json.loads(request.content) == {
        "model": MODEL,
        "parameters": {"input_type": "query", "truncate": "END", "dimension": 1024},
        "inputs": [{"text": "serum for oily skin"}],
    }


async def test_passages_are_embedded_as_passages_in_batches_of_96() -> None:
    requests: list[httpx2.Request] = []
    vectors = await embedder(answer(), requests).embed_passages([f"product {n}" for n in range(100)])
    assert len(vectors) == 100
    bodies = [json.loads(r.content) for r in requests]
    assert [len(b["inputs"]) for b in bodies] == [96, 4]
    assert {(b["parameters"]["input_type"], b["parameters"]["truncate"]) for b in bodies} == {
        ("passage", "NONE")
    }


@pytest.mark.parametrize(
    ("handler", "problem"),
    [
        (answer(model="some-other-model"), "answered with some-other-model"),
        (answer(count=2), "got 2 vectors"),
        (answer(dimension=768), "expected 1024 dimensions"),
        (lambda _: httpx2.Response(401, json={"error": {"code": "UNAUTHENTICATED"}}), "HTTP 401"),
        (lambda _: httpx2.Response(200, json={"unexpected": True}), "expected shape"),
    ],
)
async def test_an_unusable_answer_is_an_embedding_error(handler: Handler, problem: str) -> None:
    with pytest.raises(EmbeddingError, match=problem):
        await embedder(handler).embed_query("serum")


async def test_a_transport_timeout_is_a_step_timeout_naming_the_step() -> None:
    def time_out(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(StepTimeout) as caught:
        await embedder(time_out).embed_query("serum")
    assert caught.value.step == "pinecone.embed_query"
