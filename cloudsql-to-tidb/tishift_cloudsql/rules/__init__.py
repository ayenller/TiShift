"""Machine-readable rule registries.

Each module here is the single source of truth for one rule family and is kept
in lockstep with its markdown counterpart under `references/`: SKILL.md loads
the markdown, the CLI loads these. When you change a rule in one place, change
it in the other — the tests assert the IDs, not the prose, so drift is silent.
"""
