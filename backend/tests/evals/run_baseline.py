import os
import sys
import json
import asyncio
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tests.evals.golden_dataset_schema import validate_dataset

# Import the production application
import app.tools
from app.agents.graph import customer_support_graph, AgentState

# Mutating tools in app.tools
MUTATING_TOOLS = [
    "create_ticket", "update_ticket", "cancel_order", 
    "process_refund", "send_ticket_email_to_customer"
]

class MockSupabaseExecute:
    def __init__(self, data):
        self.data = data
    def execute(self):
        return Mock(data=self.data)

class MockSupabaseBuilder:
    def __init__(self, table_name):
        self.table_name = table_name
        self.payload = None
    
    def select(self, *args, **kwargs):
        return self
    
    def insert(self, payload):
        self.payload = payload
        return self
        
    def update(self, payload):
        self.payload = payload
        return self
        
    def eq(self, column, value):
        return self
        
    def execute(self):
        # Default mock responses based on table
        if self.table_name == "tickets":
            return Mock(data=[{"id": 999, "status": "closed"}])
        elif self.table_name == "orders":
            return Mock(data=[{"id": 999, "status": "cancelled"}])
        return Mock(data=[{"id": 999}])

class MockSupabaseClient:
    def table(self, table_name: str):
        return MockSupabaseBuilder(table_name)

def enforce_sandbox_safety():
    """Discover every mutating tool's DB-client resolution path and verify it is mocked."""
    for tool_name in MUTATING_TOOLS:
        func = getattr(app.tools, tool_name, None)
        # Unwrap decorators if any (like @track_tool_call)
        while hasattr(func, "__wrapped__"):
            func = func.__wrapped__
            
        if func is None:
            continue
            
        supabase_ref = func.__globals__.get("supabase")
        if not isinstance(supabase_ref, MockSupabaseClient):
            raise RuntimeError(
                f"SAFETY ABORT: Tool {tool_name} resolved a real Supabase client! "
                f"Resolved type: {type(supabase_ref)}. Cannot proceed with SANDBOX."
            )

async def evaluate_example(example, mock_supabase_client=None):
    mode = example.execution_mode
    if mode == "UNIT":
        return {"status": "SKIPPED", "error": "Unit guardrails skipped in E2E baseline runner"}
        
    # Apply patching if SANDBOX
    if mode == "SANDBOX":
        # Global patch in app.tools
        original_supabase = app.tools.supabase
        app.tools.supabase = mock_supabase_client
        enforce_sandbox_safety()
    else:
        original_supabase = None
        
    try:
        # Initialize one conversation state
        state: AgentState = {
            "customer_id": example.customer_context.customer_id,
            "customer_name": None,
            "session_id": example.customer_context.session_id,
            "message": "",
            "conversation_history": [],
            "intent": "",
            "sentiment": "",
            "urgency": "",
            "route_to": "",
            "tool_results": None,
            "response": None,
            "escalated": False,
            "pending_approval": None,
            "approval_granted": None,
        }
        
        # Sequentially invoke
        tool_calls = []
        final_state = None
        
        for turn in example.turns:
            if turn.role == "assistant":
                # Preserve conversation history/context; do not replay
                state["conversation_history"].append({"role": "assistant", "content": turn.content})
            elif turn.role == "customer":
                state["message"] = turn.content
                # Closest possible equivalent of actual customer-facing execution path
                start_t = time.perf_counter()
                try:
                    output_state = await customer_support_graph.ainvoke(state)
                    state = output_state # Feed N into N+1
                    final_state = output_state
                except Exception as e:
                    return {"status": "ERROR", "error": str(e)}
                    
                # We would capture tool calls from LangSmith or internal tracking here.
                # For baseline, we just check if it ran.
                # (Assuming tool_results in state has some hints, or we parse logs later)

        # Basic status evaluation
        # Extract new metadata
        router_transport_failure = final_state.get("router_transport_failure", False) if final_state else False
        router_parse_failure = final_state.get("router_parse_failure", False) if final_state else False
        router_internal_failure = final_state.get("router_internal_failure", False) if final_state else False
        router_error_type = final_state.get("router_error_type", "") if final_state else ""
        
        # Determine status and failure_category
        status = "PASS"
        failure_category = None
        
        if router_transport_failure:
            status = "SYSTEM_FAILURE"
            failure_category = "ROUTER_TRANSPORT_FAILURE"
        elif router_parse_failure:
            status = "ERROR"
            failure_category = "ROUTER_PARSE_FAILURE"
        elif router_internal_failure:
            status = "ERROR"
            failure_category = "ROUTER_INTERNAL_FAILURE"
        else:
            # Semantic / Routing evaluation
            if example.expected.agent and final_state.get("route_to") != example.expected.agent:
                if example.expected.agent != "db_plan_node" or final_state.get("route_to") != "db_agent":
                    if not (example.expected.agent == "rag_node" and final_state.get("route_to") == "rag_agent"):
                        status = "FAIL"
                        # Check if intent was right but canonical route was wrong
                        if example.expected.intent and final_state.get("intent") == example.expected.intent:
                            failure_category = "ROUTE_CONTRACT_FAILURE"
                        else:
                            failure_category = "ROUTER_CLASSIFICATION_FAILURE"

        return {
            "example_id": example.id,
            "status": status,
            "failure_category": failure_category,
            "predicted_intent": final_state.get("intent") if final_state else None,
            "selected_agent": final_state.get("route_to") if final_state else None,
            "final_response": final_state.get("response") if final_state else None,
            "error": None,
            "router_error_type": router_error_type,
            "llm_transport_failure": router_transport_failure,
            "parser_failure": router_parse_failure
        }

    finally:
        # Restore patching
        if mode == "SANDBOX" and original_supabase:
            app.tools.supabase = original_supabase

async def evaluate_example_with_retry(example, mock_supabase, sem, max_retries):
    async with sem:
        retries = 0
        start_t = time.perf_counter()
        while retries <= max_retries:
            try:
                res = await evaluate_example(example, mock_supabase)
                
                # Check if it's a transport failure and whether we should retry
                if res.get("llm_transport_failure") and retries < max_retries:
                    err_type = res.get("router_error_type", "").lower()
                    # Determine if the error is retryable
                    is_retryable = False
                    if "429" in err_type or "502" in err_type or "503" in err_type or "timeout" in err_type or "connect" in err_type or "unavailable" in err_type:
                        is_retryable = True
                    if "401" in err_type or "403" in err_type or "unauthorized" in err_type:
                        is_retryable = False
                        
                    if is_retryable:
                        retries += 1
                        await asyncio.sleep(2 ** retries)
                        continue

                res["retries"] = retries
                res["latency"] = time.perf_counter() - start_t
                return res
            except Exception as e:
                err_str = str(e).lower()
                is_transport = any(term in err_str for term in ["timeout", "connect", "502", "503", "429"])
                is_non_retryable = any(term in err_str for term in ["401", "403", "unauthorized"])
                
                if is_transport and not is_non_retryable and retries < max_retries:
                    retries += 1
                    await asyncio.sleep(2 ** retries)
                    continue
                return {
                    "example_id": example.id, 
                    "status": "ERROR", 
                    "error": str(e), 
                    "retries": retries,
                    "latency": time.perf_counter() - start_t,
                    "llm_transport_failure": is_transport or is_non_retryable,
                    "parser_failure": False,
                    "router_error_type": str(type(e).__name__) + ": " + str(e)
                }

async def run_baseline(target: str = "baseline", max_concurrency: int = 3, max_retries: int = 3):
    dataset_path = Path(__file__).parent / "golden_dataset_v1.json"
    dataset = validate_dataset(str(dataset_path))
    
    print(f"Running baseline for {len(dataset.examples)} examples with concurrency {max_concurrency} and {max_retries} retries...")
    
    mock_supabase = MockSupabaseClient()
    sem = asyncio.Semaphore(max_concurrency)
    
    tasks = [evaluate_example_with_retry(ex, mock_supabase, sem, max_retries) for ex in dataset.examples]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Handle unexpected top-level exceptions from gather
    clean_results = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            clean_results.append({
                "example_id": dataset.examples[i].id,
                "status": "ERROR",
                "error": str(res),
                "llm_transport_failure": False,
                "parser_failure": False,
                "retries": 0,
                "latency": 0.0
            })
        else:
            clean_results.append(res)
    results = clean_results

    # Determine state
    errors = [r for r in results if r["status"] == "ERROR"]
    skips = [r for r in results if r["status"] == "SKIPPED"]
    passes = [r for r in results if r["status"] == "PASS"]
    fails = [r for r in results if r["status"] == "FAIL"]
    
    if not results:
        baseline_state = "BLOCKED"
    elif len(errors) > 0 or len(skips) > 0:
        baseline_state = "PARTIAL"
    else:
        baseline_state = "FULL"
        
    # Separate semantic failures from system failures
    system_failures = [r for r in results if r.get("status") == "SYSTEM_FAILURE"]
    semantic_evaluation_denominator = len(dataset.examples) - len(skips) - len(errors) - len(system_failures)
    system_failure_rate = len(system_failures) / max(len(dataset.examples), 1)

    # Diagnostic metadata
    import os
    from app.config import settings
    env_present_os = "LLM_API_KEY" in os.environ
    env_present_dotenv = False
    dotenv_path = Path(".env")
    if dotenv_path.exists():
        with open(dotenv_path) as f:
            env_present_dotenv = any("LLM_API_KEY=" in line for line in f)

    if env_present_os:
        cred_source = "environment"
    elif env_present_dotenv:
        cred_source = "dotenv"
    else:
        cred_source = "default"

    metadata = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "dataset_version": dataset.version,
        "environment_diagnostics": {
            "credential_source": cred_source,
            "credential_present": env_present_os or env_present_dotenv,
            "provider": getattr(settings, 'llm_base_url', 'unknown'),
            "model": getattr(settings, 'llm_small_model', 'unknown')
        },
        "metrics": {
            "total_examples": len(dataset.examples),
            "passed": len(passes),
            "semantic_failures": len(fails),
            "system_failures": len(system_failures),
            "errors": len(errors),
            "skips": len(skips),
            "semantic_evaluation_denominator": semantic_evaluation_denominator,
            "system_failure_rate": system_failure_rate
        },
        "state": baseline_state,
    }
    print(f"\n--- Baseline State: {baseline_state} ---")
    print(f"Passes: {len(passes)}, Semantic Fails: {len(fails)}, System Fails: {len(system_failures)}, Errors: {len(errors)}, Skips: {len(skips)}")
    
    total_transport_failures = sum(1 for r in results if r.get("llm_transport_failure"))
    total_parser_failures = sum(1 for r in results if r.get("parser_failure"))
    
    if total_transport_failures > 0 or total_parser_failures > 0:
        print(f"\nObserved {total_transport_failures} transport failures and {total_parser_failures} parser failures.")
        
    # Save output
    output = {
        "dataset_version": dataset.version,
        "commit_identity": "HEAD",
        "model_provider": os.getenv("LLM_LARGE_MODEL", "unknown"),
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline_state": baseline_state,
        "evaluation_metadata": metadata,
        "results": results
    }
    
    baselines_dir = Path(__file__).parent / "baselines"
    baselines_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if target == "skills_v1":
        prefix = "CSA_SKILLS_V1"
    elif target == "router_v1":
        prefix = "CSA_ROUTER_V1"
    elif target == "baseline_stable":
        prefix = "CSA_BASELINE_STABLE_V1"
    elif target == "skills_v1_stable":
        prefix = "CSA_SKILLS_STABLE_V1"
    elif target == "router_v1_stable":
        prefix = "CSA_ROUTER_STABLE_V1"
    elif target == "dbplanner_base":
        prefix = "CSA_DBPLANNER_BASE_V1"
    elif target == "dbplanner_v1":
        prefix = "CSA_DBPLANNER_V1"
    elif target == "routing_base":
        prefix = "CSA_ROUTING_BASE_V1"
    elif target == "routing_base_stable":
        prefix = "CSA_ROUTING_BASE_STABLE_V1"
    elif target == "routing_v1":
        prefix = "CSA_ROUTING_V1"
    else:
        prefix = "CSA_BASELINE_V1"
        
    out_file = baselines_dir / f"{prefix}_{timestamp}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)
        
    print(f"Baseline saved to {out_file}")
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=str, default="baseline")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    # Workaround for Windows asyncio loop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_baseline(args.target, args.concurrency, args.retries))
