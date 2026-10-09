"""The guidance every connecting agent receives must forbid invented memory."""

from space.guide import full_guide, operating_instructions, provider_line
from space.model_provider import MemoryModels
from space.settings import Settings


def models(**overrides):
    values = {
        "memory_provider": "openai_compatible",
        "memory_provider_url": "https://models.invalid/v1",
        "memory_api_key": "test-secret-key",
        "memory_text_model": "test",
        "memory_embedding_model": "test-embed",
    }
    return MemoryModels(Settings(**(values | overrides)))


def test_operating_instructions_forbid_inventing_memory_and_predict_found_false():
    text = operating_instructions(models(), resource_access=True)
    assert "found=false" in text
    assert "Never invent, guess, reconstruct" in text
    assert "not proof that the fact is false" in text
    assert "test-secret-key" not in text


def test_full_guide_states_that_an_empty_result_is_a_valid_answer():
    text = full_guide(models(), resource_access=True)
    assert "## When recall finds nothing" in text
    assert "Silence from the index is a valid answer" in text
    # The earlier wording invited an agent to keep going and improvise. It is gone.
    assert "insufficient evidence, not proof that nothing happened" not in text


def test_guide_without_a_provider_does_not_promise_model_work():
    disabled = models(memory_provider="disabled")
    assert "No memory provider is configured" in provider_line(disabled)
    text = full_guide(disabled, resource_access=True)
    assert "at most" in text
    assert "test-embed" not in text


def test_document_policy_is_served_from_the_setting():
    """The mining policy is generated from the setting, so the two cannot diverge.

    Off is the default, which is what an unconfigured deployment serves and what the
    guidance recommends: a document is already found by the words it stores.
    """
    off = operating_instructions(models(), resource_access=True, mine_documents=False)
    on = operating_instructions(models(), resource_access=True, mine_documents=True)
    assert "Do not mine documents or session handoffs on your own initiative" in off
    assert "This Space mines ordinary source documents into memory" in on
    assert "Do not automatically mine session handoffs" in on
    assert "This Space mines ordinary source documents" not in off
    assert operating_instructions(models(), resource_access=True) == off
    assert "Do not mine documents or session handoffs on your own initiative" in full_guide(
        models(), resource_access=True, mine_documents=False
    )


def test_handoff_guidance_does_not_save_at_session_end():
    short = operating_instructions(models(), resource_access=True)
    full = full_guide(models(), resource_access=True)
    assert "Do not save a handoff merely because work ended" in short
    assert "Report current blockers, progress and next actions in the final response" in short
    assert "Do not save a handoff automatically at the end of a session" in full
    assert "Do not call remember, extract_memory or build_memory as a routine closeout step" in full
    assert "memory for a distinct durable fact, decision, lesson or preference" in full


def test_typesafe_processor_and_fallback_are_disclosed_without_secrets():
    text = provider_line(
        models(memory_relevance_provider="typesafe", typesafe_api_key="private-typesafe-canary")
    )
    assert "TypeSafe jev-1.13.0" in text
    assert "https://api.typesafe.ai/v1/systemone" in text
    assert "relevance model test at https://models.invalid/v1" in text
    assert "TypeSafe requests have no store=false option" in text
    assert "private-typesafe-canary" not in text and "test-secret-key" not in text
