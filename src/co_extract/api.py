import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import anthropic
import pypdfium2
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from .client import build_client, check_credentials, provider
from .cli import process
from . import extract
from .schema import ExtractionResult


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = build_client()
    app.state.credential_error = check_credentials(client)
    app.state.client = client
    yield
    client.close()


def get_client(request: Request) -> anthropic.Anthropic:
    """The shared client, or a 503 if it was never usable.

    Extraction is refused rather than attempted so the caller gets a clear
    cause instead of an auth error wrapped in a model failure. The docs and
    the health check stay available either way.
    """
    if request.app.state.credential_error:
        raise HTTPException(
            status_code=503,
            detail=f"server has no usable Anthropic credentials: {request.app.state.credential_error}",
        )
    return request.app.state.client


app = FastAPI(
    title="Change Order Extraction",
    version="0.1.0",
    lifespan=lifespan,
)

router = APIRouter(tags=["extraction"])


@router.get("/health", summary="Liveness check")
def health(request: Request) -> dict[str, str]:
    error = request.app.state.credential_error
    return {
        "status": "ok",
        "provider": provider(),
        "model_access": "unavailable" if error else "ok",
    }


@router.post(
    "/extract",
    response_model=ExtractionResult,
    summary="Extract a change order",
    responses={
        400: {"description": "Not a PDF, or the file could not be parsed"},
        502: {"description": "The model request failed"},
        503: {"description": "The server has no usable Anthropic credentials"},
    },
)
def extract_change_order(
    client: Annotated[anthropic.Anthropic, Depends(get_client)],
    file: Annotated[UploadFile, File(description="The change order PDF")],
    verify: Annotated[
        bool, Query(description="Run a second pass on a stronger model and flag disagreements")
    ] = False,
    model: Annotated[str, Query(description="Model for the first pass")] = extract.FIRST_PASS_MODEL,
) -> ExtractionResult:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="expected a .pdf file")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(file.filename).name
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            return process(
                str(path),
                model,
                extract.SECOND_PASS_MODEL if verify else None,
                client=client,
            )
        # PdfiumError subclasses RuntimeError, so it is caught before the
        # model-failure branch or a bad upload reads as a bad model.
        except pypdfium2.PdfiumError as exc:
            raise HTTPException(status_code=400, detail=f"could not read that PDF: {exc}") from exc
        except (anthropic.APIError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=f"model request failed: {exc}") from exc


app.include_router(router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
