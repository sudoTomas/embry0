# Enterprise-Scale Roadmap

A short note on the work that turns embry0 from a single-stack
deployment into a multi-tenant SaaS / on-prem product. The current
architecture (orchestrator non-privileged + DinD privileged + per-job
sandbox + proxy-injected credentials + three-ring defense) is the right
foundation. Most of what follows is about replacing in-memory or
single-stack assumptions with horizontally-scalable equivalents — not
rewriting core components.

## Tier 1 — required before the first paying customer

1. **Replace DinD with a managed container runtime.** The
   `SandboxManager.create() → container_id` interface is already abstract;
   swap the implementation for Kubernetes (Jobs + per-tenant Namespaces +
   NetworkPolicies), AWS ECS-on-Fargate, or Nomad. Wins: no privileged
   container in the customer-facing stack; horizontal scale; standard
   ops tooling (kubectl logs, dashboards, autoscaling). The proxy
   containers move to the same runtime as sidecars or per-tenant
   Deployments.

2. **Tenant isolation primitives.** Today there's one DB, one MinIO
   bucket, one set of proxy tokens, one orchestrator process. Per-tenant
   isolation needs:
   - **DB**: row-level security on every table keyed on `tenant_id`,
     OR per-tenant schema, OR per-tenant database. RLS is the cheapest;
     per-tenant DB is the most defensible against a SQL-injection blast
     radius.
   - **MinIO**: per-tenant bucket OR per-tenant prefix with bucket
     policies. Lifecycle + retention configurable per tenant.
   - **Network**: per-tenant `sandbox-restricted` network in the runtime
     so a tenant's sandbox can never DNS-resolve another tenant's app
     stack.
   - **Credentials**: rotate per-tenant `ENVIRONMENT_SECRET_KEY`,
     `PROXY_ADMIN_TOKEN`. Use a real secrets manager (Vault, AWS Secrets
     Manager) instead of `.env` files.

3. **Orchestrator horizontal scale.** Today's orchestrator holds
   in-memory state: `EventBus` subscribers, `ProxyManager` token registry,
   `IssueExecutor._tasks_by_job`. That kills horizontal scaling.
   - **EventBus**: move to Redis Pub/Sub or NATS so any orchestrator
     replica can serve any job's WebSocket subscribers.
   - **Job-to-orchestrator binding**: today crash-recovery assumes one
     orchestrator owns all jobs. Add a job lease (Postgres advisory
     lock or Redis lease) so a crashed orchestrator's jobs get picked up
     by a replica within the lease TTL.
   - **Background tasks** (`ContainerReaper`, checkpoint sweep): elect
     a leader (Postgres advisory lock) so duplicate replicas don't fight.

4. **Authentication + authorization.** Today the API key gate is a
   single shared secret. Need:
   - Per-user OAuth/SAML/OIDC at the dashboard
   - Per-tenant API keys with scoped permissions
   - Audit log entries already exist; just need the actor field populated
     from session, not request IP

5. **Cost attribution + billing hooks.** Per-job `total_cost_usd` is
   already tracked. Aggregate by tenant for billing; add
   per-tenant `budget_usd` enforcement that hard-stops jobs at the cap
   and emits a `tenant.budget_exceeded` event.

## Tier 2 — required to scale the first 10 customers

6. **Object storage abstraction.** Replace direct MinIO with S3 + MinIO
   as deploy-time choices. Same `QAMinioClient` interface; different
   creds. Required for cloud-native deploys.

7. **Production observability.**
   - Metrics: Prometheus scrape on every replica. Job counts, queue
     depth, sandbox lifecycle latencies, agent cost per minute, MCP
     server failures.
   - Distributed tracing: the existing `trace_id` is per-job; promote
     to OpenTelemetry spans across orchestrator → DinD → MinIO.
   - Centralized logs: structured JSON already; ship to
     Loki/Datadog/CloudWatch.

8. **Sandbox image registry.** Today images live in DinD's local
   registry. Move to ECR/GCR/private registry; sign images with
   cosign; the `SandboxImageManager.ensure_image()` build-hash check
   becomes a registry pull check.

9. **Rate limiting + abuse controls.** Per-tenant: max concurrent jobs,
   max jobs per hour, max sandbox lifetime. Today the only limits are
   compose-level memory/CPU caps that apply globally.

10. **Disaster recovery story.** Today the daily `pg_dump` cron is
    in-stack. Production wants:
    - Point-in-time recovery on Postgres (logical replication or WAL-G)
    - MinIO bucket replication to a second region
    - Tested restore drill, not just backup

## Tier 3 — required to scale the first 100 customers

11. **Multi-region deploy.** Tenant home-region pinning; cross-region
    failover with checkpoints replicated. The LangGraph checkpoint
    table is the only stateful piece that *must* be co-located with
    the orchestrator running the workflow.

12. **Sandbox cold-start optimization.** Pre-warmed sandbox pools
    per profile (qa-jvm, qa-node, qa-python, default). `SandboxManager`
    grabs from pool instead of `docker run`. Drops boot latency from
    ~5s to <500ms.

13. **Per-tenant model routing.** Some tenants pay for Opus on every
    agent; others use Sonnet/Haiku tiers. The agent_definition + triage
    `pipeline_config.agent_models` is the existing knob; expose it in
    the tenant settings UI.

14. **Compliance.** SOC 2 / ISO 27001 / GDPR / HIPAA for healthcare
    customers. The audit log table is the foundation; everything else
    is process + documentation. Notable gap today: no PII redaction in
    `job_logs` (LLM prompts often contain user data).

15. **Marketplace + extensibility.** Custom workflows beyond
    issue-to-PR and QA. The `WorkflowRegistry` already supports this;
    productize it: per-tenant workflow upload, sandbox profile catalog,
    MCP server registry.

## Things explicitly NOT on the roadmap

- **Collapsing the sandbox boundary** — the security model is the
  product.
- **Replacing LangGraph with a custom workflow engine** — checkpointing,
  conditional edges, and stream events are exactly what we need. The
  surface area is small enough that swapping is cheap if it ever
  becomes necessary.
- **Self-hosted Claude / open-weights model fallback** — adds an enormous
  ops burden (GPU fleet, model serving) for marginal product value.
  Defer until customers contractually require it.

## Recommended order

Tier 1 items 1–4 are blocking for any production customer. Item 5 (cost
attribution) can land in parallel with the sandbox-runtime swap (#1)
because the cost tracking already exists in code. Tier 2 work can be
scheduled customer-by-customer based on what each one's procurement
team asks for. Tier 3 is "good problems to have" — don't build any
of it speculatively.
