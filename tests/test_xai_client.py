import json
import re
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _parse_grok_response(content: str):
    """Mirror the parsing logic in GrokAnalyst.analyze_market."""
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        return None
    return json.loads(json_match.group(0))


def test_parse_valid_yes_response():
    content = '{"decision": "YES", "confidence": 0.85, "reasoning": "strong data"}'
    data = _parse_grok_response(content)
    assert data["decision"] == "YES"
    assert data["confidence"] == 0.85
    assert "probability" not in data  # removed field


def test_parse_skip_response():
    content = '{"decision": "SKIP", "confidence": 0.3, "reasoning": "not enough info"}'
    data = _parse_grok_response(content)
    assert data["decision"] == "SKIP"


def test_parse_response_with_surrounding_text():
    content = 'Here is my analysis:\n{"decision": "NO", "confidence": 0.75, "reasoning": "declining trend"}\nDone.'
    data = _parse_grok_response(content)
    assert data["decision"] == "NO"


def test_parse_returns_none_on_no_json():
    data = _parse_grok_response("I cannot determine this market direction.")
    assert data is None


def test_prompt_does_not_request_probability():
    """Ensure the prompt no longer asks Grok for a probability estimate."""
    import inspect
    from src.clients.xai_client import GrokAnalyst
    source = inspect.getsource(GrokAnalyst.analyze_market)
    # The prompt should not contain a "probability" field in the JSON schema
    # (it's OK if the word appears in comments, but not as a requested field)
    assert '"probability"' not in source, \
        "GrokAnalyst.analyze_market should not request a 'probability' field — Grok fabricates them"


def test_prompt_shows_human_readable_close_time():
    """analyze_market source should compute close_display, not use close_time directly."""
    import inspect
    from src.clients.xai_client import GrokAnalyst
    source = inspect.getsource(GrokAnalyst.analyze_market)
    assert "close_display" in source, "Should compute human-readable close_display"
    assert "Closes in:" in source, "Prompt should say 'Closes in:'"


def test_prompt_contains_skepticism_instruction():
    import inspect
    from src.clients.xai_client import GrokAnalyst
    source = inspect.getsource(GrokAnalyst.analyze_market)
    assert "consensus" in source.lower()


def test_prompt_requests_key_evidence_field():
    import inspect
    from src.clients.xai_client import GrokAnalyst
    source = inspect.getsource(GrokAnalyst.analyze_market)
    assert "key_evidence" in source
