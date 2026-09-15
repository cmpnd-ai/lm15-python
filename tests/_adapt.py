"""Test helpers for MAP-13 assertions: what a build adapts, by field."""
from __future__ import annotations

import json


def adapted(lm, request, stream: bool = False) -> dict:
    """{field: Adaptation} recorded by one build, plus the wire body under
    ``"__body__"`` (parsed JSON) so a test can check both in one call."""
    req, records = lm._build(request, stream=stream)
    out = {a.field: a for a in records}
    out["__body__"] = json.loads(req.body) if req.body else None
    return out


def refuses(lm, request, feature: str):
    """Under adaptations="refuse" the same build raises, naming the field."""
    import dataclasses

    import pytest

    from lm15.errors import UnsupportedFeatureError

    import copy

    strict = lm
    if getattr(lm, "adaptations", None) != "refuse":
        strict = copy.copy(lm)
        strict.adaptations = "refuse"
    with pytest.raises(UnsupportedFeatureError) as info:
        strict._build(request, stream=False)
    assert info.value.feature == feature, (info.value.feature, str(info.value))
    return info.value
