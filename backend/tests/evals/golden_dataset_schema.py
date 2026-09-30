import json
import pytest
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Dict

class CustomerContext(BaseModel):
    customer_id: Optional[int]
    session_id: Optional[str]
    authenticated: bool

class Turn(BaseModel):
    role: Literal["customer", "assistant"]
    content: str
    turn_index: int

class ToolExpectations(BaseModel):
    required_tools: List[str] = Field(default_factory=list)
    optional_tools: List[str] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)

    @field_validator('optional_tools', mode='after')
    def validate_disjoint(cls, v, info):
        required = set(info.data.get('required_tools', []))
        optional = set(v)
        if required.intersection(optional):
            raise ValueError(f"required_tools and optional_tools must be disjoint. Intersection: {required.intersection(optional)}")
        return v

    @field_validator('forbidden_tools', mode='after')
    def validate_all_disjoint(cls, v, info):
        required = set(info.data.get('required_tools', []))
        optional = set(info.data.get('optional_tools', []))
        forbidden = set(v)
        
        if required.intersection(forbidden):
            raise ValueError(f"required_tools and forbidden_tools must be disjoint. Intersection: {required.intersection(forbidden)}")
        if optional.intersection(forbidden):
            raise ValueError(f"optional_tools and forbidden_tools must be disjoint. Intersection: {optional.intersection(forbidden)}")
        return v

class ResponseContract(BaseModel):
    must_state: List[str] = Field(default_factory=list)
    must_ask_for: List[str] = Field(default_factory=list)
    must_not_claim: List[str] = Field(default_factory=list)
    must_offer: List[str] = Field(default_factory=list)

class GuardrailExpectations(BaseModel):
    type: Literal["unit", "e2e", "none"]
    input_blocked: bool
    output_blocked: bool

class Expected(BaseModel):
    intent: Optional[str]
    agent: Optional[str]
    outcome: Optional[str]
    expected_facts: List[str] = Field(default_factory=list)
    tools: ToolExpectations
    response_contract: ResponseContract
    rag_required: bool
    guardrail: GuardrailExpectations

class GoldenExample(BaseModel):
    id: str
    category: str
    execution_mode: Literal["READ_ONLY", "SANDBOX", "UNIT"]
    turns: List[Turn]
    customer_context: CustomerContext
    expected: Expected

class GoldenDataset(BaseModel):
    schema_version: str = Field(alias="$schema")
    version: str
    created_at: str
    examples: List[GoldenExample]

    @field_validator('examples', mode='after')
    def validate_unique_ids(cls, v):
        ids = [example.id for example in v]
        if len(ids) != len(set(ids)):
            duplicates = {x for x in ids if ids.count(x) > 1}
            raise ValueError(f"Example IDs must be unique. Found duplicates: {duplicates}")
        return v

# List of valid tools available in the application
REGISTERED_TOOLS = {
    "lookup_customer", "get_customer_history", "check_inventory", "track_order",
    "list_all_products", "cancel_order", "process_refund", "get_ticket",
    "create_ticket", "update_ticket", "send_ticket_email_to_customer", "get_chat_history"
}

def validate_dataset(file_path: str):
    """Parse and validate the golden dataset JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    dataset = GoldenDataset(**data)
    
    # Validate tool existence
    for example in dataset.examples:
        tools = example.expected.tools
        all_tools = set(tools.required_tools + tools.optional_tools + tools.forbidden_tools)
        invalid_tools = all_tools - REGISTERED_TOOLS
        if invalid_tools:
            raise ValueError(f"Example {example.id} contains unregistered tools: {invalid_tools}")
            
    return dataset

def test_golden_dataset_schema():
    dataset_path = Path(__file__).parent / "golden_dataset_v1.json"
    dataset = validate_dataset(str(dataset_path))
    assert len(dataset.examples) > 0, "Dataset must not be empty"
    print(f"Validated {len(dataset.examples)} examples.")

if __name__ == "__main__":
    test_golden_dataset_schema()
