import inspect
import re

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from gateway.swagger_annotations import SWAGGER_ATTR


SECURITY_SCHEMES = {
    "BasicAuth": {"type": "http", "scheme": "basic"},
    "XClientId": {"type": "apiKey", "in": "header", "name": "X-Client-Id"},
    "XClientSecret": {"type": "apiKey", "in": "header", "name": "X-Client-Secret"},
}


def _converter_type(name):
    mapping = {
        "int": "integer",
        "float": "number",
        "uuid": "string",
        "slug": "string",
        "path": "string",
        "str": "string",
    }
    return mapping.get(name, "string")


def _regex_type(expr):
    if re.fullmatch(r"\\d\+|\[0-9\]\+", expr):
        return "integer"
    if "uuid" in expr.lower() or "a-f" in expr.lower():
        return "string"
    return "string"


def _pattern_text(pattern):
    if hasattr(pattern, "route"):
        return pattern.route
    return pattern.regex.pattern


def _iter_patterns(patterns, prefix=""):
    for pattern in patterns:
        part = _pattern_text(pattern.pattern)
        full = f"{prefix}{part}"
        if isinstance(pattern, URLResolver):
            yield from _iter_patterns(pattern.url_patterns, full)
        elif isinstance(pattern, URLPattern):
            yield full, pattern.callback, pattern.name


def _to_openapi_path(raw_path):
    text = (raw_path or "").replace("\\/", "/")
    text = text.replace("^", "").replace("\\Z", "").replace("$", "")

    path_params = {}

    def regex_repl(match):
        name = match.group("name")
        expr = match.group("expr")
        path_params[name] = {"name": name, "in": "path", "required": True, "schema": {"type": _regex_type(expr)}}
        return f"{{{name}}}"

    text = re.sub(
        r"\(\?P<(?P<name>\w+)>(?P<expr>[^)]+)\)",
        regex_repl,
        text,
    )

    def route_repl(match):
        converter = match.group("converter") or "str"
        name = match.group("name")
        path_params[name] = {"name": name, "in": "path", "required": True, "schema": {"type": _converter_type(converter)}}
        return f"{{{name}}}"

    text = re.sub(
        r"<(?:(?P<converter>[^>:]+):)?(?P<name>[^>]+)>",
        route_repl,
        text,
    )

    text = text.replace("\\", "")
    text = re.sub(r"/{2,}", "/", text)
    text = "/" + text.lstrip("/")
    if text == "/":
        return text, list(path_params.values())
    return text, list(path_params.values())


def _extract_docs(callback):
    docs = getattr(callback, SWAGGER_ATTR, None)
    if docs:
        return docs
    unwrapped = inspect.unwrap(callback)
    return getattr(unwrapped, SWAGGER_ATTR, None)


def _merge_parameters(inferred_path_params, documented_path_params, query_params):
    merged_path = {param["name"]: dict(param) for param in inferred_path_params}
    for param in documented_path_params:
        merged_path[param["name"]] = {
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            **param,
        }

    merged = list(merged_path.values())
    for param in query_params:
        merged.append(
            {
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                **param,
            }
        )
    return merged


def _operation_id(path_name, method, summary):
    source = path_name or summary or method
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", source).strip("_").lower()
    return f"{method.lower()}_{token}" if token else f"{method.lower()}_operation"


def _build_paths():
    paths = {}
    resolver = get_resolver()

    for raw_path, callback, path_name in _iter_patterns(resolver.url_patterns):
        if "api/docs/" in raw_path:
            continue

        docs = _extract_docs(callback)
        if not docs:
            continue

        openapi_path, inferred_params = _to_openapi_path(raw_path)
        path_item = paths.setdefault(openapi_path, {})

        for method, doc in docs.items():
            operation = {
                "summary": doc["summary"],
                "responses": doc["responses"],
                "operationId": _operation_id(path_name, method, doc["summary"]),
            }

            if doc.get("description"):
                operation["description"] = doc["description"]
            if doc.get("tags"):
                operation["tags"] = doc["tags"]
            if doc.get("security"):
                operation["security"] = doc["security"]

            parameters = _merge_parameters(
                inferred_params,
                doc.get("path_params", []),
                doc.get("query_params", []),
            )
            if parameters:
                operation["parameters"] = parameters

            request_body = doc.get("request_body")
            if request_body:
                operation["requestBody"] = request_body

            path_item[method] = operation

    return paths


def _build_openapi_schema(request):
    server_url = request.build_absolute_uri("/").rstrip("/")
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "TTPB Mediator API",
            "version": "1.0.0",
            "description": "Annotation-driven API documentation for mediator and notification services.",
        },
        "servers": [{"url": server_url}],
        "components": {"securitySchemes": SECURITY_SCHEMES},
        "paths": _build_paths(),
    }


def openapi_schema(request):
    return JsonResponse(_build_openapi_schema(request))


def swagger_ui(request):
    return render(
        request,
        "swagger/swagger_ui.html",
        {"schema_url": reverse("openapi-schema")},
    )
