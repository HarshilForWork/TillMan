"""Pinecone Inference, used only to turn text into vectors (#27). Vectors are stored in Neon, not Pinecone.

Called over REST through the process's shared `httpx2.AsyncClient`, not the `pinecone` SDK (which
brings `httpx`, a second HTTP library). `input_type` is `passage` for Products and `query` for
searches: swapping them fails silently, so each has its own method and nothing takes it as an argument.
"""

from typing import Annotated, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from tillhand.core.deadlines import EMBED_PASSAGES_SECONDS, EMBED_QUERY_SECONDS, StepTimeout, deadline

EMBED_URL = "https://api.pinecone.io/embed"
API_VERSION = "2026-07"
MAX_INPUTS_PER_REQUEST = 96
"""Pinecone's limit for `llama-text-embed-v2`."""

InputType = Literal["passage", "query"]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EmbedParameters(_Wire):
    input_type: InputType
    truncate: Literal["END", "NONE"]
    dimension: int


class _EmbedInput(_Wire):
    text: str


class _EmbedRequest(_Wire):
    model: str
    parameters: _EmbedParameters
    inputs: list[_EmbedInput]


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Pinecone may add fields; we validate the ones we rely on and ignore the rest."""


class _Embedding(_Response):
    values: list[float]


class _Usage(_Response):
    total_tokens: Annotated[int, Field(ge=0)]


class EmbedResponse(_Response):
    model: str
    data: list[_Embedding]
    usage: _Usage


class EmbeddingError(Exception):
    """No usable vectors: Pinecone failed, refused, or answered with the wrong model, count or dimension.

    Timeouts are not this: they raise `StepTimeout`, so the caller can name the step that overran.
    """


class PineconeEmbedder:
    def __init__(self, client: httpx2.AsyncClient, *, api_key: SecretStr, model: str, dimension: int) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._dimension = dimension

    @property
    def model(self) -> str:
        return self._model

    async def embed_query(self, text: str) -> list[float]:
        """One search query. Truncated at the model's 2,048-token limit rather than refused."""
        step = "pinecone.embed_query"
        async with deadline(step, EMBED_QUERY_SECONDS):
            (vector,) = await self._embed([text], input_type="query", truncate="END", step=step)
        return vector

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Product documents, in batches of 96. Refuses over-long text rather than embedding half of it."""
        vectors: list[list[float]] = []
        step = "pinecone.embed_passages"
        async with deadline(step, EMBED_PASSAGES_SECONDS):
            for start in range(0, len(texts), MAX_INPUTS_PER_REQUEST):
                batch = texts[start : start + MAX_INPUTS_PER_REQUEST]
                vectors.extend(await self._embed(batch, input_type="passage", truncate="NONE", step=step))
        return vectors

    async def _embed(
        self, texts: list[str], *, input_type: InputType, truncate: Literal["END", "NONE"], step: str
    ) -> list[list[float]]:
        request = _EmbedRequest(
            model=self._model,
            parameters=_EmbedParameters(input_type=input_type, truncate=truncate, dimension=self._dimension),
            inputs=[_EmbedInput(text=text) for text in texts],
        )
        try:
            response = await self._client.post(
                EMBED_URL,
                json=request.model_dump(),
                headers={
                    "Api-Key": self._api_key.get_secret_value(),
                    "X-Pinecone-Api-Version": API_VERSION,
                },
            )
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            raise EmbeddingError(f"Pinecone answered HTTP {exc.response.status_code}") from exc
        except httpx2.TimeoutException as exc:
            # The client's own timeout can fire before our deadline; either way the step overran.
            raise StepTimeout(step) from exc
        except httpx2.HTTPError as exc:
            raise EmbeddingError(f"could not reach Pinecone: {type(exc).__name__}") from exc
        try:
            parsed = EmbedResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise EmbeddingError("Pinecone's response did not have the expected shape") from exc
        # Pinecone may serve a different model from the one asked for (#27); vectors from two models
        # in one column would make similarity meaningless, so that is an error, not a warning.
        if parsed.model != self._model:
            raise EmbeddingError(f"asked for {self._model}, Pinecone answered with {parsed.model}")
        if len(parsed.data) != len(texts):
            raise EmbeddingError(f"sent {len(texts)} texts, got {len(parsed.data)} vectors")
        if bad := [len(e.values) for e in parsed.data if len(e.values) != self._dimension]:
            raise EmbeddingError(f"expected {self._dimension} dimensions, got {sorted(set(bad))}")
        return [e.values for e in parsed.data]
