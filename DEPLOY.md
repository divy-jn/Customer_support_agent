# Deploy IntelliSupport

Use a clean checkout of the reviewed AWS release commit. Keep the deployment environment file on the host and enter credentials through the host's secret-management process.

## Environment variable names

| Purpose | Names |
|---|---|
| Runtime and model | `APP_ENVIRONMENT`, `AGENT_ARCHITECTURE_MODE`, `LLM_PROVIDER`, `LLM_MODEL`, `AWS_REGION`, `AWS_EC2_METADATA_DISABLED` |
| Public routing and browser configuration | `APP_DOMAIN`, `CORS_ORIGINS`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| Backend data and auth | `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_ANON_KEY`, `JWT_SECRET`, `APPROVAL_SIGNING_SECRET`, `SUPPORT_TEAM_EMAIL` |
| Knowledge retrieval | `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` |
| Notification dispatch and reconciliation | `OUTBOX_WORKER_ENABLED`, `DATABASE_URL` or `DATABASE_PASSWORD`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` |
| Optional hosted tracing | `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`, `LANGSMITH_API_KEY`, `LANGSMITH_HIDE_INPUTS`, `LANGSMITH_HIDE_OUTPUTS` |
| Optional alternate env file | `PROD_ENV_FILE` |
| Set by the build command | `BUILD_GIT_SHA` |

Select production mode, V2 all-domain routing, and the Amazon Bedrock DeepSeek V3.2 model in US East (N. Virginia). Enable AWS instance metadata when authenticating through the EC2 instance role. Use the site's HTTPS and WSS origins in the frontend build inputs and authorize the frontend origin in CORS. Use the same Supabase project for the backend and browser. The browser receives only the public Supabase key.

Compose starts the standalone outbox dispatcher and reconciliation by default. Configure the database and Gmail credentials before enabling email. To disable both jobs, set `OUTBOX_WORKER_ENABLED` to false in the Compose env file and apply the Compose configuration again. Ticket and escalation transactions can still enqueue work, which the worker can process when re-enabled.

## Build and start

Run from the checkout on the deployment host. The Compose env file must be readable by the deploying account.

```sh
git status --short
git rev-parse HEAD
BUILD_GIT_SHA="$(git rev-parse HEAD)" COMPOSE_PARALLEL_LIMIT=1 \
  docker compose --env-file .env.prod -f docker-compose.prod.yml build
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --no-build
```

Record the printed Git SHA and the image IDs and revision labels for the running backend and frontend. The outbox shares the backend image.

```sh
for service in backend frontend; do
  container="$(docker compose --env-file .env.prod -f docker-compose.prod.yml ps -q "$service")"
  image="$(docker inspect --format '{{.Image}}' "$container")"
  docker image inspect --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}' "$image"
done
```

The revision on both images should match the clean checkout's commit. Restarting containers alone cannot apply frontend build-time URL changes.

## Verify the release

1. Check the backend startup log for `LangSmith tracing enabled project=`, the expected `endpoint=`, and `api_key_set=True` when hosted tracing is configured. An intentionally disabled installation logs `LangSmith tracing disabled`. Inspect one synthetic chat turn in the selected LangSmith project. Keep inputs and outputs hidden for deployed chat.
2. Confirm the backend logs identify the expected Bedrock provider and model. When email is enabled, check the outbox logs for `Standalone Outbox Worker Initialized` and `Standalone process started background reconciliation scheduler.`
3. Check `/health` for the application version, outbox dispatch and reconciliation status, and `last_model_success_at` after the chat turn. The timestamp is process-local and resets on restart. The outbox status reports worker-loop liveness. Confirm email delivery separately.
4. Sign in with the designated test customer. Send these four queries in one chat and compare the answers with the customer's known records.

| Query | Expected result |
|---|---|
| Check my ticket status | Active cases with status and last update, or a verified empty reply |
| is there any active ticket for my account | Active cases or a verified empty reply |
| give me my order history | The customer's order history or a verified empty reply |
| my order history | The customer's order history or a verified empty reply |

The opt-in classifier regression uses the real configured LLM and synthetic data. Run it from `backend` in an authorized test environment with `RUN_LIVE_INCIDENT_ROUTING` enabled. It can make billable model calls.

```sh
RUN_LIVE_INCIDENT_ROUTING=1 python -m pytest -m llm_eval tests/evals/test_incident_routing_live.py -q
```
