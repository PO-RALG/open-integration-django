SWAGGER_ATTR = "__swagger_docs__"


def swagger_doc(
    *,
    methods,
    summary,
    description="",
    tags=None,
    query_params=None,
    path_params=None,
    request_body=None,
    responses=None,
    security=None,
):
    if isinstance(methods, str):
        methods = [methods]

    doc_payload = {
        "summary": summary,
        "description": description,
        "tags": tags or [],
        "query_params": query_params or [],
        "path_params": path_params or [],
        "request_body": request_body,
        "responses": responses or {"200": {"description": "Success"}},
        "security": security or [],
    }

    def decorator(view_func):
        existing = dict(getattr(view_func, SWAGGER_ATTR, {}))
        for method in methods:
            existing[str(method).strip().lower()] = dict(doc_payload)
        setattr(view_func, SWAGGER_ATTR, existing)
        return view_func

    return decorator
