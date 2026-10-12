from __future__ import annotations

from typing import Annotated, Any, TypeAlias, TypeGuard

from pydantic import BeforeValidator


def _is_dict(v: Any) -> TypeGuard[dict[Any, Any]]:
    return isinstance(v, dict)


def _is_list(v: Any) -> TypeGuard[list[Any]]:
    return isinstance(v, list)


def _scalarize(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if _is_dict(value):
        return _recover_dict(value)
    if _is_list(value):
        return _recover_list(value)
    return str(value)


def _recover_dict(item: dict[Any, Any]) -> str:
    pairs = [f"{k}: {_scalarize(v)}" for k, v in item.items()]
    return ", ".join(pairs)


def _recover_list(item: list[Any]) -> str:
    parts: list[str] = []
    for elem in item:
        recovered = recover_item(elem)
        if recovered:
            parts.append(recovered)
    return " ".join(parts)


def recover_item(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if _is_dict(item):
        return _recover_dict(item)
    if _is_list(item):
        return _recover_list(item)
    return _scalarize(item)


def normalize_str_list(value: Any) -> Any:
    if not _is_list(value):
        return value
    result: list[str] = []
    for item in value:
        recovered = recover_item(item)
        if recovered:
            result.append(recovered)
    return result


FreeTextList: TypeAlias = Annotated[list[str], BeforeValidator(normalize_str_list)]
