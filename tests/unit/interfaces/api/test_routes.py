from alignment_memory.application import ProjectKnowledgeService
from alignment_memory.interfaces.api.routes import _projected_knowledge_node_id


def test_finding_target_id_matches_projected_knowledge_node_id() -> None:
    repository_id = "10000000-0000-0000-0000-000000000001"
    logical_key = "decision:privacy-safe-debugging"

    assert _projected_knowledge_node_id(
        repository_id,
        logical_key,
    ) == ProjectKnowledgeService._stable_id(
        "knowledge-node",
        repository_id,
        logical_key,
    )
