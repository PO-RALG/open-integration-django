class EmitRequestSerializer:
    recipient_schema = {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer"},
            "actor_type": {"type": "string"},
            "to_phone": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "to_email": {"type": "string", "format": "email"},
            "preferred_lang": {"type": "string"},
        },
        "required": ["user_id", "actor_type"],
    }

    schema = {
        "type": "object",
        "properties": {
            "event_code": {"type": "string"},
            "context": {"type": "object"},
            "recipients": {
                "type": "array",
                "items": recipient_schema,
            },
            "recipient_user_id": {"type": "integer"},
            "recipient_actor_type": {"type": "string"},
            "to_phone": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "to_email": {"type": "string", "format": "email"},
            "preferred_lang": {"type": "string"},
            "idempotency_key": {"type": "string"},
            "entity_type": {"type": "string"},
            "entity_id": {"type": "string"},
            "payload": {"type": "object"},
            "max_attempts": {"type": "integer"},
        },
        "required": ["event_code"],
    }

    example = {
        "event_code": "ACCOUNT_ROLE_CHANGED",
        "context": {"name": "Asha", "role": "Reviewer"},
        "recipients": [
            {
                "user_id": 2001,
                "actor_type": "STAFF",
                "to_phone": ["255762470046", "255710167020"],
                "to_email": "asha@example.org",
                "preferred_lang": "en",
            }
        ],
        "idempotency_key": "emit-20260302-0001",
        "entity_type": "ACCOUNT",
        "entity_id": "ACC-1001",
        "payload": {"changed_by": "system"},
        "max_attempts": 5,
    }

    @classmethod
    def swagger_request_body(cls):
        return {
            "required": True,
            "content": {
                "application/json": {
                    "schema": cls.schema,
                    "example": cls.example,
                }
            },
        }

    @classmethod
    def validate(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON body")

        event_code = payload.get("event_code")
        if not event_code:
            raise ValueError("event_code is required")

        context = payload.get("context", {})
        if not isinstance(context, dict):
            raise ValueError("context must be a JSON object")

        normalized = dict(payload)
        normalized["context"] = context

        recipients = normalized.get("recipients")
        if recipients is None:
            recipients = []

        if not recipients:
            single_user_id = normalized.get("recipient_user_id")
            single_actor_type = normalized.get("recipient_actor_type")
            if single_user_id is not None and single_actor_type:
                recipients = [
                    {
                        "user_id": single_user_id,
                        "actor_type": single_actor_type,
                        "to_phone": normalized.get("to_phone", ""),
                        "to_email": normalized.get("to_email", ""),
                        "preferred_lang": normalized.get("preferred_lang", ""),
                    }
                ]

        normalized["recipients"] = recipients
        return normalized
