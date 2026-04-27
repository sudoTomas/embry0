"""Agent template metadata — backward compatibility wrapper.

The authoritative agent definitions now live in the database (agent_definitions table).
This module provides the AGENT_TYPES list for any code that still imports it directly.
New code should use AgentDefinitionsRepository instead.
"""

from athanor.storage.repositories.agent_definitions import BUILTIN_SEED

# Legacy format for backward compatibility
AGENT_TYPES = [
    {
        "type": agent_type,
        "phase": agent_type,
        "description": seed["description"],
        "default_model": seed["model"],
        "default_tools": seed["tools"],
        "default_skills": seed["skills"],
        "inputs": [],
        "outputs": [],
        "responsibilities": [],
    }
    for agent_type, seed in BUILTIN_SEED.items()
]
