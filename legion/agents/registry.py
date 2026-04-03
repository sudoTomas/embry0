"""Agent template metadata — describes each agent type's capabilities."""

AGENT_TYPES = [
    {
        "type": "triage",
        "phase": "triage",
        "description": "Analyzes the issue and configures the optimal pipeline. Assesses complexity, determines confidence, and can request more information or split oversized tasks.",
        "default_model": "claude-haiku-4-5",
        "default_tools": [],
        "default_skills": [],
        "inputs": [
            {"name": "repo", "description": "Repository to analyze"},
            {"name": "task", "description": "Issue or task description"},
            {"name": "issue_number", "description": "GitHub issue number (if webhook-triggered)"},
        ],
        "outputs": [
            {"name": "pipeline_config", "description": "Pipeline template and agent configuration"},
            {"name": "confidence", "description": "Confidence score (0.0-1.0)"},
        ],
        "responsibilities": [
            "Assess issue complexity and scope",
            "Determine confidence in implementation approach",
            "Select pipeline template and agent models",
            "Request more information when confidence is low",
            "Split oversized issues into sub-tasks",
        ],
    },
    {
        "type": "developer",
        "phase": "developer",
        "description": "Implements code changes, manages git operations, and creates pull requests. Uses Claude Code skills for advanced workflows including sub-agent dispatch and worktree management.",
        "default_model": "claude-sonnet-4-6",
        "default_tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "default_skills": ["superpowers:subagent-driven-development", "superpowers:verification-before-completion"],
        "inputs": [
            {"name": "task", "description": "Implementation task from triage"},
            {"name": "repo", "description": "Target repository"},
            {"name": "feedback_context", "description": "Feedback from reviewer (on retry)"},
        ],
        "outputs": [
            {"name": "agent_outputs", "description": "Implementation results and artifacts"},
            {"name": "branch_name", "description": "Git branch with changes"},
            {"name": "pr_url", "description": "Pull request URL"},
        ],
        "responsibilities": [
            "Explore codebase to understand context",
            "Implement code changes following existing patterns",
            "Run tests to verify no regressions",
            "Commit, push, and create pull request",
            "Use skills for structured development workflows",
        ],
    },
    {
        "type": "validator",
        "phase": "validator",
        "description": "Validates code changes by running tests, linting, and type checking. Reports pass/fail with detailed findings.",
        "default_model": "claude-sonnet-4-6",
        "default_tools": ["Read", "Bash", "Glob", "Grep"],
        "default_skills": [],
        "inputs": [
            {"name": "agent_outputs", "description": "Developer's implementation results"},
            {"name": "task", "description": "Original task for context"},
        ],
        "outputs": [
            {"name": "validation_result", "description": "Pass/fail with category and findings"},
            {"name": "agent_outputs", "description": "Validation report"},
        ],
        "responsibilities": [
            "Run test suite and report results",
            "Run linter and report issues",
            "Run type checker and report errors",
            "Classify result: full_pass, partial_pass, or full_fail",
            "Provide actionable feedback for retry",
        ],
    },
    {
        "type": "reviewer",
        "phase": "reviewer",
        "description": "Reviews code changes for correctness, quality, security, and scope. Approves or rejects with feedback.",
        "default_model": "claude-sonnet-4-6",
        "default_tools": ["Read", "Glob", "Grep"],
        "default_skills": [],
        "inputs": [
            {"name": "agent_outputs", "description": "Developer and validator outputs"},
            {"name": "task", "description": "Original task for context"},
        ],
        "outputs": [
            {"name": "agent_outputs", "description": "Review verdict (APPROVED/REJECTED)"},
        ],
        "responsibilities": [
            "Review implementation correctness",
            "Check code quality and maintainability",
            "Identify security concerns",
            "Verify adequate test coverage",
            "Ensure changes are focused and minimal",
        ],
    },
    {
        "type": "output",
        "phase": "output",
        "description": "Assembles the final job result from all agent outputs. Reports success/failure status, cost, and PR URL.",
        "default_model": "",
        "default_tools": [],
        "default_skills": [],
        "inputs": [
            {"name": "agent_outputs", "description": "All accumulated agent outputs"},
            {"name": "pr_url", "description": "Pull request URL (if created)"},
            {"name": "total_cost_usd", "description": "Total execution cost"},
        ],
        "outputs": [
            {"name": "result_summary", "description": "Final job result summary"},
            {"name": "current_stage", "description": "completed or failed"},
        ],
        "responsibilities": [
            "Assemble final result from agent outputs",
            "Report total cost and duration",
            "Surface PR URL if created",
            "Set final job status",
        ],
    },
]
