"""D1 (Tool-Schema Documentation v1.1): every tool description's first sentence says
what the tool returns."""

from __future__ import annotations

import re

import pytest

from genereview_link.config import ServerConfig
from genereview_link.models.genereview_models import RankedPassage, SearchResult
from genereview_link.server_manager import UnifiedServerManager
from tests.unit.test_mcp_tool_surface import _build_app_with_state

# ponytail: copied from genefoundry-router scripts/surface.py (lead_states_return);
# re-vendor byte-identically if this repo ever vendors surface.py.
LEAD_CHARS = 300
_LEAD_VERBS = (  # noqa: SIM905 - kept identical to the router copy
    "get fetch retrieve list look lookup search find resolve report read predict annotate "
    "compute calculate count summarize summarise map translate convert describe show check "
    "validate classify score rank query browse discover explain compare diff normalize normalise "
    "expand evaluate assess enumerate identify locate export download match suggest recommend "
    "inspect generate render estimate"
).split()
_VERB_LEAD = re.compile(
    rf"^(?:use this (?:tool )?to )?(?:{'|'.join(_LEAD_VERBS)})(?:s|es|ies)?\b", re.IGNORECASE
)
_RETURNS = re.compile(
    r"(?<!not )(?<!n't )(?<!n\u2019t )(?<!never )\breturn(?:s|ed|ing)?\b(?!\s*[~<\d])|→|->",
    re.IGNORECASE,
)
_FIELD_LIST = re.compile(r"\w+\W+\w+[^:]*:\s+[^.;:]+,")
_SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def _lead(description: str | None) -> str:
    text = " ".join((description or "").split())
    return _SENTENCE_END.split(text, maxsplit=1)[0][:LEAD_CHARS]


def lead_states_return(description: str | None) -> bool:
    lead = _lead(description)
    return bool(_VERB_LEAD.match(lead) or _RETURNS.search(lead) or _FIELD_LIST.search(lead))


async def _descriptions() -> dict[str, str]:
    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(
        ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    )
    app.state._state.update(_build_app_with_state().state._state)
    mcp = await mgr.create_mcp_server(app, ServerConfig())
    return {t.name: t.description or "" for t in await mcp.list_tools()}


@pytest.mark.asyncio
async def test_every_tool_description_leads_with_what_it_returns() -> None:
    descriptions = await _descriptions()
    failing = sorted(name for name, d in descriptions.items() if not lead_states_return(d))
    assert len(descriptions) >= 13
    assert not failing, f"D1: first sentence must say what the tool returns: {failing}"


@pytest.mark.asyncio
async def test_pinned_tool_leads_name_real_response_fields() -> None:
    """Contract truth: fields a pinned tool's lead promises exist in its response model."""
    descriptions = await _descriptions()
    promised = {
        "search_passages": (
            RankedPassage,
            ["passage_id", "nbk_id", "chapter_section", "snippet", "recommended_citation"],
        ),
        "search_genereviews": (SearchResult, ["ids", "count"]),
    }
    for tool, (model, fields) in promised.items():
        lead = _lead(descriptions[tool])
        for field in fields:
            assert field in lead, (tool, field)
            assert field in model.model_fields, (tool, field)
