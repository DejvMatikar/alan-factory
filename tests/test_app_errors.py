from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from alan import server
from alan.errors import AppError
from alan.routes._deps import app_error_handler


def _bare_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/app-error/{status}")
    async def raise_app_error(status: int) -> None:
        raise AppError(status_code=status, detail="app error detail")

    @app.get("/http-exception/{status}")
    async def raise_http_exception(status: int) -> None:
        raise HTTPException(status_code=status, detail="app error detail")

    @app.get("/app-error-dict")
    async def raise_app_error_dict() -> None:
        raise AppError(status_code=400, detail={"error": "shape", "ids": ["a", "b"]})

    @app.get("/http-exception-dict")
    async def raise_http_exception_dict() -> None:
        raise HTTPException(status_code=400, detail={"error": "shape", "ids": ["a", "b"]})

    return app


def test_app_error_carries_status_code_and_detail() -> None:
    error = AppError(status_code=418, detail="teapot")
    assert error.status_code == 418
    assert error.detail == "teapot"
    assert str(error) == "teapot"


def test_app_error_is_not_an_http_exception() -> None:
    assert not issubclass(AppError, HTTPException)


def test_app_error_detail_may_be_a_structured_payload() -> None:
    payload = {"error": "shape", "ids": ["a"]}
    assert AppError(status_code=400, detail=payload).detail == payload


@pytest.mark.parametrize("status", [400, 404, 409, 422, 500])
def test_handler_renders_the_same_response_as_an_http_exception(status: int) -> None:
    client = TestClient(_bare_app())
    raised = client.get(f"/app-error/{status}")
    reference = client.get(f"/http-exception/{status}")
    assert raised.status_code == reference.status_code == status
    assert raised.content == reference.content
    assert raised.headers["content-type"] == reference.headers["content-type"]


def test_handler_renders_a_structured_detail_like_an_http_exception() -> None:
    client = TestClient(_bare_app())
    raised = client.get("/app-error-dict")
    reference = client.get("/http-exception-dict")
    assert raised.status_code == reference.status_code == 400
    assert raised.content == reference.content
    assert raised.json() == {"detail": {"error": "shape", "ids": ["a", "b"]}}


def test_the_application_registers_the_app_error_handler() -> None:
    assert server.app.exception_handlers[AppError] is app_error_handler
