# Graph Report - backend  (2026-09-20)

## Corpus Check
- 67 files · ~38,419 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 789 nodes · 1052 edges · 85 communities (57 shown, 28 thin omitted)
- Extraction: 79% EXTRACTED · 21% INFERRED · 0% AMBIGUOUS · INFERRED: 225 edges (avg confidence: 0.77)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `38733c48`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]

## God Nodes (most connected - your core abstractions)
1. `validate_input()` - 27 edges
2. `classify_intent()` - 23 edges
3. `validate_output()` - 14 edges
4. `_validate_positive_int()` - 12 edges
5. `handle_customer_ws()` - 12 edges
6. `ConnectionManager` - 12 edges
7. `determine_db_action()` - 11 edges
8. `_send_email()` - 10 edges
9. `AgentState` - 10 edges
10. `parse_and_validate_router_output()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `test_malicious_inputs_blocked()` --calls--> `validate_input()`  [INFERRED]
  tests/evals/test_guardrails.py → app/guardrails.py
- `test_off_topic_blocked()` --calls--> `validate_input()`  [INFERRED]
  tests/evals/test_guardrails.py → app/guardrails.py
- `main()` --calls--> `get_llm()`  [INFERRED]
  tests/test_llm.py → app/llm_factory.py
- `get_test_customer_id()` --calls--> `get_all_customers()`  [INFERRED]
  tests/test_agent_flow.py → app/tools.py
- `test_planner_malformed_output()` --calls--> `determine_db_action()`  [INFERRED]
  tests/test_db_planner.py → app/agents/db_agent.py

## Communities (85 total, 28 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (29): _check_hallucination(), get_rejection_message(), GuardrailResult, Guardrails — validates every user input and AI output before processing.  Input, Validate a customer's input message.     Returns a GuardrailResult with sanitize, Validate the AI's response before sending it to the customer.      Args:, Simple heuristic hallucination detection.     Flags responses that contain speci, Generate a user-friendly rejection message based on the violations. (+21 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (52): classify_intent(), get_router_llm(), parse_and_validate_router_output(), Intent Router Agent — classifies customer intent and sentiment, then routes to t, Get the small LLM for fast routing decisions., Deterministic parsing boundary to handle markdown-wrapped JSON from the provider, Classify the intent, sentiment, and urgency of a customer message.      Args:, Intent Routing Evaluation Tests — validates the intent classification accuracy, (+44 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (43): _extract_sources(), generate_response(), get_rag_llm(), RAG Agent — answers customer questions using the Pinecone knowledge base.  Imp, Get the large LLM for generating RAG responses., Generate a RAG-grounded response to a customer query.      Args:         mess, Extract source document names from the formatted context string., admin_get_metrics() (+35 more)

### Community 3 - "Community 3"
Cohesion: 0.04
Nodes (27): WebSocket connection and message tests.  Uses FastAPI TestClient for WebSocket, GET /api/v1/dashboard/stats should return statistics., Test WebSocket chat connections., WebSocket should connect and receive welcome message., WebSocket should connect with a specific session ID., Empty messages should be silently ignored (no response)., WebSocket should handle plain text (not JSON) gracefully., Reconnecting with the same session ID should append to transcript consistently. (+19 more)

### Community 4 - "Community 4"
Cohesion: 0.08
Nodes (29): db_execute_node(), Node: Executes a previously approved high-risk action., global_exception_handler(), health_check(), lifespan(), FastAPI Application Entry Point — main.py  Serves: - REST API on /api/v1/* -, Catch unhandled exceptions and return a clean JSON error + log it., Customer chat — auto-generates a session ID. (+21 more)

### Community 5 - "Community 5"
Cohesion: 0.09
Nodes (20): AgentState, The state that is passed between nodes in the graph., enforce_sandbox_safety(), evaluate_example(), evaluate_example_with_retry(), MockSupabaseBuilder, MockSupabaseClient, MockSupabaseExecute (+12 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (27): admin_reingest_kb(), admin_update_llm_settings(), cancel_order_endpoint(), create_new_ticket(), dashboard_stats(), dev_login(), get_ticket_detail(), list_customers() (+19 more)

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (16): admin_get_connection_metrics(), Get WebSocket connection metrics., ConnectionManager, get_connection_metrics(), WebSocket Connection Manager — handles real-time connections for the customer ch, Remove an agent's connection., Send a message directly to a specific customer., Broadcast a message to all agents monitoring this session. (+8 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (7): auth_headers_a(), auth_headers_b(), create_mock_jwt(), expired_headers(), test_wrong_role(), test_ws_customer_a_can_connect_own_session(), test_ws_customer_a_cannot_connect_customer_b_session()

### Community 9 - "Community 9"
Cohesion: 0.14
Nodes (23): _base_template(), _info_row(), _info_table(), Email Notification Service — sends emails via Gmail OAuth API.  Provides templ, Send escalation alert to the support team (and optionally the customer)., Send ticket resolution notification to the customer., Send system alert to the tech team., Send an update or custom message regarding a ticket to the customer. (+15 more)

### Community 10 - "Community 10"
Cohesion: 0.1
Nodes (20): CompressedTimedRotatingFileHandler, get_recent_logs(), get_trace_id(), JSONFormatter, Human-readable console format that includes trace_id., Setup production-grade logging with rotating files and JSON formatting.      A, Read recent logs from the JSON log file for the Admin Dashboard.      Args:, Get the current trace ID from context, or generate a new one. (+12 more)

### Community 11 - "Community 11"
Cohesion: 0.11
Nodes (23): test_handover(), cancel_order(), check_inventory(), create_ticket(), get_customer_history(), get_dashboard_stats(), get_ticket(), lookup_customer() (+15 more)

### Community 12 - "Community 12"
Cohesion: 0.14
Nodes (18): OrderStatus, Pydantic schemas for API request/response validation., Sentiment, TicketPriority, TicketStatus, TicketType, Urgency, create_ticket() (+10 more)

### Community 13 - "Community 13"
Cohesion: 0.12
Nodes (21): cancel_order(), get_all_customers(), get_all_tickets(), get_customer_by_id(), get_customer_history(), get_dashboard_stats(), get_ticket(), process_refund() (+13 more)

### Community 14 - "Community 14"
Cohesion: 0.1
Nodes (18): generate_escalation_response(), get_escalation_llm(), Escalation Agent — handles angry customers or explicit human handoff requests. P, Get the small LLM for fast escalation processing., Generate an empathetic handover message., determine_search_query(), generate_response(), get_web_llm() (+10 more)

### Community 15 - "Community 15"
Cohesion: 0.12
Nodes (13): generate_customers(), generate_full_dataset(), generate_orders(), generate_tickets(), Indian Synthetic Dataset Generator for Customer Support Agent.  Generates dete, Generate synthetic Indian customers., Generate orders. Returns list of dicts with customer_index and product_index, Generate support tickets referencing valid order indices.     Each ticket maps (+5 more)

### Community 16 - "Community 16"
Cohesion: 0.2
Nodes (15): DBPlannerOutput, determine_db_action(), generate_response(), get_db_llm(), Database / Memory Agent — handles customer-specific operations by querying the P, Generate a response using database tool results.      Args:         message: The, Get the large LLM for generating database-informed responses., Determine which database action(s) to take based on the customer message and int (+7 more)

### Community 17 - "Community 17"
Cohesion: 0.12
Nodes (16): create_customer_support_graph(), db_plan_node(), escalation_node(), rag_node(), LangGraph Orchestration — wires all agent nodes together into a state graph. Al, Node: Searches the web for external answers., Node: Handles angry customers and human handoffs., Conditional edge function to determine the next node. (+8 more)

### Community 18 - "Community 18"
Cohesion: 0.16
Nodes (17): ChatMessage, ChatRequest, ChatResponse, CustomerProfile, DashboardStats, EscalationAlert, Sent to the Agent Dashboard when a customer is escalated., Aggregated stats for the analytics dashboard. (+9 more)

### Community 19 - "Community 19"
Cohesion: 0.15
Nodes (11): CustomerContext, Expected, GoldenDataset, GoldenExample, GuardrailExpectations, Parse and validate the golden dataset JSON file., ResponseContract, test_golden_dataset_schema() (+3 more)

### Community 20 - "Community 20"
Cohesion: 0.14
Nodes (8): Test customer lookup and retrieval., Search for a customer by a common Indian name., Search for a customer by email domain., Search for a non-existent customer returns empty list., Get paginated list of customers., Get a customer's order and ticket history., Invalid customer ID returns error., TestCustomerOperations

### Community 21 - "Community 21"
Cohesion: 0.15
Nodes (3): Application configuration loaded from environment variables., Settings, BaseSettings

### Community 22 - "Community 22"
Cohesion: 0.18
Nodes (10): Verify the agent token from headers (for REST routes)., Verify agent secret for WebSocket connections., Verify customer JWT token from Authorization header and return customer_id., Verify the admin API key from headers., Verify customer JWT token from WebSocket query parameters., verify_admin_key(), verify_agent_token(), verify_agent_ws_token() (+2 more)

### Community 23 - "Community 23"
Cohesion: 0.22
Nodes (5): AgentSkill, Renders the AgentSkill into a prompt format for LLM context., render_skill_prompt(), test_agent_skill_validation(), test_skill_rendering()

### Community 24 - "Community 24"
Cohesion: 0.22
Nodes (6): Integration tests for Supabase database tools.  Tests customer lookup, order t, Test dashboard/analytics operations., Get dashboard statistics., Test that Supabase is reachable., TestDashboardStats, TestSupabaseHealth

### Community 25 - "Community 25"
Cohesion: 0.25
Nodes (8): admin_health_check(), Aggregated health check for all services., check_llm_health(), check_supabase_health(), check_vectordb_health(), Check if the cloud LLM API is reachable and which models are available., Check if Pinecone is accessible., Check if Supabase is accessible.

### Community 26 - "Community 26"
Cohesion: 0.25
Nodes (8): check_inventory(), lookup_customer(), Check if a specific product is in stock and get its details., Search the web using DuckDuckGo Instant Answer API., Ensure a parameter is a non-empty string., Look up a customer by name or email address., _validate_non_empty_str(), web_search()

### Community 27 - "Community 27"
Cohesion: 0.25
Nodes (8): _execute_tool(), Execute a database tool by name, injecting the authenticated customer_id securel, get_chat_history(), list_all_products(), Get past chat conversations for a customer., Send an email to the customer with an update regarding their ticket., List all available products in the catalog. Does not include stock quantity., send_ticket_email_to_customer()

### Community 28 - "Community 28"
Cohesion: 0.25
Nodes (7): list_documents(), File System MCP Server — exposes knowledge base document operations as MCP tools, List all available knowledge base documents.     Returns the filename and size o, Read the full contents of a knowledge base document.      Args:         filename, Search across all knowledge base documents for a keyword or phrase.     Returns, read_document(), search_documents()

### Community 29 - "Community 29"
Cohesion: 0.25
Nodes (5): Test ticket retrieval., Get paginated tickets., Get details of a specific ticket., Non-existent ticket returns error., TestTicketOperations

### Community 30 - "Community 30"
Cohesion: 0.25
Nodes (5): Test ticket creation and updates., Test that invalid enums are rejected., Test that invalid enums are rejected in updates., Test that closing a ticket sets closed_at using valid ISO format (if ticket exis, TestTicketMutations

### Community 31 - "Community 31"
Cohesion: 0.25
Nodes (5): Test product/inventory operations., List all products in catalog., Search for a product by name., Search for non-existent product returns error., TestProductOperations

### Community 32 - "Community 32"
Cohesion: 0.4
Nodes (5): ingest(), Ingest knowledge base documents into Pinecone for RAG retrieval.  Reads all .md, Split text into overlapping chunks by character count, respecting paragraph boun, Main ingestion function., split_text()

### Community 33 - "Community 33"
Cohesion: 0.33
Nodes (4): Track an order that exists in the database., Tracking a non-existent order returns error., Test order tracking and retrieval., TestOrderOperations

### Community 34 - "Community 34"
Cohesion: 0.5
Nodes (4): admin_get_kb_doc(), Read a specific knowledge base document., get_knowledge_base_doc(), Read a knowledge base document's content.

### Community 35 - "Community 35"
Cohesion: 0.5
Nodes (4): admin_list_kb(), List all knowledge base documents., list_knowledge_base_docs(), List all knowledge base documents.

### Community 36 - "Community 36"
Cohesion: 0.5
Nodes (4): admin_system_info(), Get system configuration and version info., get_system_info(), Get system information for the admin dashboard.

### Community 37 - "Community 37"
Cohesion: 0.5
Nodes (3): Web Search MCP Server — exposes web search capabilities as MCP tools.  Allows, Search the web for information related to a customer query.     Use this ONLY w, web_search()

## Knowledge Gaps
- **289 isolated node(s):** `Application configuration loaded from environment variables.`, `Parse comma-separated CORS origins into a list.`, `Resolve knowledge_base_dir relative to backend/ root.`, `Extract the host portion from supabase_url (e.g. tesfafbkhbleipxbpcpk).`, `Async PostgreSQL connection string for SQLAlchemy (asyncpg).` (+284 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **28 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `handle_customer_ws()` connect `Community 4` to `Community 0`, `Community 11`?**
  _High betweenness centrality (0.150) - this node is a cross-community bridge._
- **Why does `classify_intent()` connect `Community 1` to `Community 2`, `Community 11`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Why does `validate_input()` connect `Community 0` to `Community 2`, `Community 4`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Are the 57 inferred relationships involving `str` (e.g. with `test_handover()` and `_send_email()`) actually correct?**
  _`str` has 57 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `validate_input()` (e.g. with `record_guardrail_input()` and `handle_customer_ws()`) actually correct?**
  _`validate_input()` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `classify_intent()` (e.g. with `track_llm_call()` and `str`) actually correct?**
  _`classify_intent()` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `validate_output()` (e.g. with `record_guardrail_output()` and `handle_customer_ws()`) actually correct?**
  _`validate_output()` has 10 INFERRED edges - model-reasoned connections that need verification._