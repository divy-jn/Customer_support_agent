import pytest
import uuid
import json
import psycopg2
import time
from app.tools import create_ticket, update_ticket, cancel_order, process_refund
from tests.test_idempotency_real import exec_rpc, get_ticket_description, conn_string

def test_missing_client_request_id():
    assert json.loads(create_ticket(1, "s", "d", client_request_id=None)).get("error") == "client_request_id is required for mutating operations"
    assert json.loads(update_ticket(1, description_append="a", client_request_id=None)).get("error") == "client_request_id is required for mutating operations"
    assert json.loads(cancel_order(1, client_request_id=None)).get("error") == "client_request_id is required for mutating operations"
    assert json.loads(process_refund(1, client_request_id=None)).get("error") == "client_request_id is required for mutating operations"

def test_ticket_append_legitimate_identical_request():
    payload = {
        "subject": "Append Legit",
        "description": "Original",
        "type": "inquiry",
        "status": "open",
        "priority": "low",
        "channel": "chat"
    }
    res_create = exec_rpc("create_ticket", "new", payload)
    ticket_id = res_create["ticket_id"]
    
    # R1:
    r1 = str(uuid.uuid4())
    append_payload = {"description_append": "Still broken"}
    res1 = exec_rpc("update_ticket", str(ticket_id), append_payload, client_request_id=r1)
    assert res1.get("status") == "success"
    
    # retry R1:
    res1_retry = exec_rpc("update_ticket", str(ticket_id), append_payload, client_request_id=r1)
    assert res1_retry.get("status") == "success"
    
    # R2:
    r2 = str(uuid.uuid4())
    res2 = exec_rpc("update_ticket", str(ticket_id), append_payload, client_request_id=r2)
    assert res2.get("status") == "success"
    
    desc = get_ticket_description(ticket_id)
    # R1 executed once, R2 executed once. So "Still broken" should be present exactly TWICE.
    assert desc.count("Still broken") == 2, f"Expected 2 appends, found {desc.count('Still broken')} in: {desc}"

def test_explicit_result_semantics_and_conflicts():
    req = str(uuid.uuid4())
    payload = {"subject": "Semantics", "description": "Desc", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
    
    # A. first request
    res_first = exec_rpc("create_ticket", "new", payload, client_request_id=req)
    assert res_first.get("status") == "success"
    
    # B. exact retry
    res_retry = exec_rpc("create_ticket", "new", payload, client_request_id=req)
    assert res_retry.get("status") == "success"
    assert res_retry["ticket_id"] == res_first["ticket_id"]
    
    # C. same request + different target
    res_diff_target = exec_rpc("create_ticket", "other", payload, client_request_id=req)
    assert res_diff_target.get("error") == "IdempotencyConflict"
    
    # D. same request + different action
    res_diff_action = exec_rpc("update_ticket", "new", payload, client_request_id=req)
    assert res_diff_action.get("error") == "IdempotencyConflict"
    
    # E. same request + different payload
    diff_payload = payload.copy()
    diff_payload["subject"] = "Different"
    res_diff_payload = exec_rpc("create_ticket", "new", diff_payload, client_request_id=req)
    assert res_diff_payload.get("error") == "IdempotencyConflict"
    
    # F. new request ID + same business payload
    req_new = str(uuid.uuid4())
    res_new = exec_rpc("create_ticket", "new", payload, client_request_id=req_new)
    assert res_new.get("status") == "success"
    assert res_new["ticket_id"] != res_first["ticket_id"]

def test_negative_customer_isolation():
    # Setup ticket owned by customer 1
    res_create = exec_rpc("create_ticket", "new", {"subject": "C1", "description": "D", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}, customer_id=1)
    ticket_id = res_create["ticket_id"]
    
    # D. Customer 2 attempts target resource owned by customer 1
    req = str(uuid.uuid4())
    res_unauth = exec_rpc("update_ticket", str(ticket_id), {"description_append": "hacked"}, customer_id=2, client_request_id=req)
    assert "not found or does not belong to you" in res_unauth.get("error", "")
    
    # B. Customer 2 attempts to retrieve customer 1's idempotency result (by guessing the req ID)
    req1 = str(uuid.uuid4())
    exec_rpc("create_ticket", "new", {"subject": "C1_2", "description": "D", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}, customer_id=1, client_request_id=req1)
    
    # Customer 2 uses the SAME req1 but for their own ticket creation
    res2 = exec_rpc("create_ticket", "new", {"subject": "C1_2", "description": "D", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}, customer_id=2, client_request_id=req1)
    # It succeeds independently because identity is (customer_id, client_request_id)
    assert res2.get("status") == "success"

def test_real_crash_equivalent():
    req = str(uuid.uuid4())
    subj = "Crash_" + req
    payload = {"subject": subj, "description": "D", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
    
    res1 = exec_rpc("create_ticket", "new", payload, client_request_id=req)
    assert res1.get("status") == "success"
    
    # Assume the python process crashed here and the websocket disconnected.
    # The user reconnects and the agent resends the SAME client_request_id
    
    res2 = exec_rpc("create_ticket", "new", payload, client_request_id=req)
    assert res2.get("status") == "success"
    assert res1["ticket_id"] == res2["ticket_id"]
    
    # Verify DB only has ONE crash ticket
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tickets WHERE subject = %s", (subj,))
    count = cur.fetchone()[0]
    assert count == 1
    cur.close()
    conn.close()
