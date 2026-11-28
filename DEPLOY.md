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
| Optional app token lifetime override | `JWT_EXPIRATION_MINUTES` |
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
3. Check `/health` for the application version, `git_sha` matching both image labels, outbox dispatch and reconciliation status, and `last_model_success_at` after the chat turn. The timestamp is process-local and resets on restart. The outbox status reports worker-loop liveness. Confirm email delivery separately.
4. Sign in with the designated test customer. Send these four queries in one chat and compare the answers with the customer's known records.

| Query | Expected result |
|---|---|
| Check my ticket status | Active cases with status and last update, or a verified empty reply |
| is there any active ticket for my account | Active cases or a verified empty reply |
| give me my order history | The customer's order history or a verified empty reply |
| my order history | The customer's order history or a verified empty reply |

The opt-in classifier regression uses the real configured LLM and synthetic data. Run it from `backend` in an authorized test environment with `RUN_LIVE_INCIDENT_ROUTING` enabled. It can make billable model calls.

```sh
RUN_LIVE_INCIDENT_ROUTING=1 python -m pytest -m llm_eval tests/evals/test_semantic_routing.py tests/evals/test_incident_routing_live.py -q
```


## If a query still fails

1. Confirm `/health` reports the intended `git_sha`. In backend startup logs, look for `LangSmith tracing enabled project=CSA endpoint=https://api.smith.langchain.com api_key_set=True`. An intentionally disabled instance logs `LangSmith tracing disabled`. A startup line that says the key is set confirms configuration. Verify ingestion separately by looking for a completed trace in CSA.
2. Use a test account and send a single verification query. In LangSmith, open **Tracing → CSA**, find the corresponding LangGraph run by time, expand **semantic_supervisor**, and open its classifier model span. In its output, locate the exact JSON `intent` value. Compare it with the normalized `intent` and `semantic_failure_type` on the backend `workflow_turn` event. The event records approved raw intent labels under `raw_classifier_intent` and redacts unfamiliar labels.
3. Deployed traces hide inputs and outputs by default. If the raw intent is absent, reproduce the query in an isolated environment with synthetic account, history, and tool results. Enable trace outputs only for that isolated replay. Keep production trace payloads hidden. If classification failed, investigate the reported failure type before changing routing.
4. For a valid but unfamiliar account intent, add the observed label to the appropriate alias set in [routing_policy.py](backend/app/agents/routing_policy.py). [domain_graph.py](backend/app/agents/domain_graph.py) calls that normalizer before dispatch. Extend [test_customer_lookup_routing.py](backend/tests/test_customer_lookup_routing.py) with the exact observed label and assert the authenticated lookup is called. Use [test_incident_routing_live.py](backend/tests/evals/test_incident_routing_live.py) to verify the live classifier-to-tool contract against synthetic data.
5. Repeat the four verification prompts in one conversation.

   - “Check my ticket status”
   - “give me my order history”
   - “my order history”
   - “is there any active ticket for my account”

The GitHub **Live Bedrock Routing Evaluation** workflow runs only when dispatched manually. Set repository variable `BEDROCK_EVAL_ROLE_ARN` to an existing evaluation role trusted for GitHub OIDC and permitted to call the Bedrock model. The workflow uses synthetic data for tool lookups.
