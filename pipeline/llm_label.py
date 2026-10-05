"""BONUS — an LLM inside the pipeline (slide "LLM là một bước transform").

The support team wants an LLM pre-triage label on every live ticket
(gold_ticket_labels), to compare with the human `category` and to triage new
tickets faster. An LLM step is a transform like any other — except it is
expensive, slow and NOT deterministic, so the slide's four rules apply:

  1. key = hash(input) + model + prompt version  -> a re-run makes 0 LLM calls;
     changing the prompt re-labels everything ON PURPOSE
  2. force a structured output, validate it; invalid -> quarantine, never Gold
  3. estimate the cost BEFORE running (rows x tokens x price)
  4. LLM labels are versioned data (model + prompt_version stored on every row)

Labels and rejected responses are cached by input hash, model and prompt version.
Only validated JSON labels reach Gold; rejected responses remain in quarantine.
The grading checker uses FakeLLM; scripts.label_api uses the real OpenAI adapter.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Protocol

import duckdb

MODEL = "fake-llm-2026-09"
PROMPT_VERSION = "triage-v1"
ALLOWED_LABELS = ("bug", "billing", "other")
PRICE_PER_1K_TOKENS_USD = 0.002          # pretend price, for the cost estimate


PROMPT_TEMPLATE = """You triage customer-support tickets.
Answer ONLY with JSON: {{"label": "bug" | "billing" | "other"}}.
Ticket: {text}"""


class FakeLLM:
    """Deterministic stand-in for a chat model. Counts calls and tokens."""

    def __init__(self, model: str = MODEL) -> None:
        self.model = model
        self.calls = 0
        self.tokens = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        self.tokens += len(prompt.split()) + 8
        text = prompt.lower()
        if "xuất" in text:
            return 'Sure! Here is the label: {"label": "export"}'   # off-schema answer
        if re.search(r"crash|lỗi|sso|đăng nhập|chatbot", text):
            return '{"label": "bug"}'
        if re.search(r"tiền|hoá đơn|thanh toán|gói|vat", text):
            return '{"label": "billing"}'
        return '{"label": "other"}'


class LabelLLM(Protocol):
    model: str
    calls: int

    def complete(self, prompt: str) -> str: ...


def estimate_tokens(texts: list[str]) -> int:
    return sum(len(PROMPT_TEMPLATE.format(text=t).split()) + 8 for t in texts)


def parse_label(raw: str) -> str | None:
    """Require a JSON object containing exactly one allowed label."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or set(obj) != {"label"}:
        return None
    label = obj["label"]
    return label if label in ALLOWED_LABELS else None


def live_tickets(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    return con.execute("""
        SELECT ticket_id, subject || '. ' || body AS text
        FROM silver_tickets
        WHERE NOT is_deleted
        ORDER BY ticket_id
    """).fetchall()


def label_tickets(con: duckdb.DuckDBPyConnection, llm: LabelLLM, *, dry_run: bool = False) -> dict:
    """Cache both validated labels and rejected responses by input/model/prompt."""
    con.execute("""CREATE TABLE IF NOT EXISTS llm_label_cache (
        input_hash VARCHAR, model VARCHAR, prompt_version VARCHAR,
        label VARCHAR, raw VARCHAR,
        PRIMARY KEY (input_hash, model, prompt_version))""")
    con.execute("""CREATE TABLE IF NOT EXISTS llm_label_quarantine (
        ticket_id VARCHAR, input_hash VARCHAR, model VARCHAR, prompt_version VARCHAR,
        raw VARCHAR, reason VARCHAR,
        PRIMARY KEY (ticket_id, input_hash, model, prompt_version))""")
    tickets = live_tickets(con)
    pending = []
    for ticket_id, text in tickets:
        h = sha256(text.encode('utf-8')).hexdigest()
        cached = con.execute(
            "SELECT label, raw FROM llm_label_cache WHERE input_hash=? AND model=? AND prompt_version=?",
            [h, llm.model, PROMPT_VERSION]).fetchone()
        pending.append((ticket_id, text, h, cached))
    uncached = {h: text for _, text, h, cached in pending if cached is None}
    texts = list(uncached.values())
    if hasattr(llm, 'estimate'):
        estimate = llm.estimate(texts)
    else:
        tokens = estimate_tokens(texts)
        estimate = {"estimated_tokens": tokens,
                    "estimated_cost_usd": tokens / 1000 * PRICE_PER_1K_TOKENS_USD}
    cost = estimate['estimated_cost_usd']
    cost_text = f"${cost:.6f}" if cost is not None else 'unknown (configure model prices)'
    print(f"  uncached cost estimate before running: ~{estimate['estimated_tokens']} input tokens, "
          f"{len(uncached)} requests; estimated cost {cost_text}")
    summary = {"tickets": len(tickets), "uncached_inputs": len(uncached), **estimate}
    if dry_run:
        return {**summary, "calls": 0, "dry_run": True}
    before = llm.calls
    usage_before = [getattr(llm, name, 0) for name in
                    ('input_tokens', 'output_tokens', 'cached_input_tokens')]
    rows = []
    for ticket_id, text, h, cached in pending:
        # Check again: several tickets can share identical input text.
        cached = con.execute(
            "SELECT label, raw FROM llm_label_cache WHERE input_hash=? AND model=? AND prompt_version=?",
            [h, llm.model, PROMPT_VERSION]).fetchone()
        if cached is None:
            raw = llm.complete(PROMPT_TEMPLATE.format(text=text))
            label = parse_label(raw)
            usage = getattr(llm, 'last_usage', None)
            if usage is not None:
                con.execute("""CREATE TABLE IF NOT EXISTS llm_api_usage (
                    response_id VARCHAR PRIMARY KEY, input_hash VARCHAR, model VARCHAR,
                    response_model VARCHAR, prompt_version VARCHAR, input_tokens BIGINT,
                    output_tokens BIGINT, cached_input_tokens BIGINT, cost_usd DOUBLE,
                    response_status VARCHAR, created_at TIMESTAMP DEFAULT current_timestamp)""")
                con.execute("""INSERT INTO llm_api_usage
                    (response_id, input_hash, model, response_model, prompt_version,
                     input_tokens, output_tokens, cached_input_tokens, cost_usd, response_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [usage['response_id'], h, llm.model, usage['response_model'], PROMPT_VERSION,
                     usage['input_tokens'], usage['output_tokens'], usage['cached_input_tokens'],
                     usage['cost_usd'], usage['response_status']])
            con.execute("INSERT INTO llm_label_cache VALUES (?, ?, ?, ?, ?)",
                        [h, llm.model, PROMPT_VERSION, label, raw])
        else:
            label, raw = cached
        if label is None:
            con.execute("INSERT INTO llm_label_quarantine VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        [ticket_id, h, llm.model, PROMPT_VERSION, raw,
                         'Expected JSON object with exactly one allowed label'])
        else:
            rows.append((ticket_id, label, llm.model, PROMPT_VERSION))
    con.execute("""CREATE OR REPLACE TABLE gold_ticket_labels (
        ticket_id VARCHAR, label VARCHAR, model VARCHAR, prompt_version VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO gold_ticket_labels VALUES (?, ?, ?, ?)", rows)
    input_tokens, output_tokens, cached_tokens = [
        getattr(llm, name, 0) - previous for name, previous in zip(
            ('input_tokens', 'output_tokens', 'cached_input_tokens'), usage_before)]
    actual_cost = (llm.usage_cost(input_tokens, output_tokens, cached_tokens)
                   if hasattr(llm, 'usage_cost') else None)
    return {**summary, "labeled": len(rows), "quarantined": len(tickets) - len(rows),
            "calls": llm.calls - before, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cached_input_tokens": cached_tokens,
            "usage_cost_usd": actual_cost}
