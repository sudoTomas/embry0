# ADR 0017: What embry0 keeps of a repository's code is bounded by two opt-in retention settings

Status: accepted (2026-10-19)

ADR 0001 said customer code never outlives its sandbox. It does, in three places on the host: the job's events in `job_logs` (Write and Edit inputs, Bash commands, every tool result, so whole files), the agents' saved sessions in `agent_sessions` (the full conversation), and pre-built QA images and per-repo warm volumes on the sandbox daemon (a checkout with `.git`). This record corrects that clause. `JOB_LOG_RETENTION_DAYS` replaces the code in ended jobs' events older than N days with a marker that keeps the tool, file path, size and SHA-256, and deletes those jobs' sessions. `QA_IMAGE_RETENTION_DAYS` removes QA images and per-repo volumes unused for N days, never one an active `qa_image_tags` row names, a container uses or a live job's repo needs. Both default to unset, which keeps today's behaviour: deleting is the operator's decision, and `GET /api/v1/admin/retention/preview` shows its effect first. Registry copies, backups and agent prose are not covered; SECURITY.md says so.

Evidence: `embry0/services/job_log_retention.py`, `embry0/cache/qa_image_retention.py`, `embry0/api/v1/retention.py`; SECURITY.md, "Data retention".
