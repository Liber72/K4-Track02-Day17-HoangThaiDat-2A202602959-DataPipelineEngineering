"""Additional API tests; the original grading tests remain unchanged.

All provider responses are mocked, so this suite makes no network requests.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import duckdb
import pytest

from pipeline import config, llm_label
from pipeline.llm_api import OpenAILLM, _price


def response(text='{"label":"bug"}', *, status='completed', number=1, refusal=None):
    content = [SimpleNamespace(type='refusal', refusal=refusal)] if refusal else []
    return SimpleNamespace(id=f'resp_{number}', model='gpt-4o-mini-2024-07-18',
                           status=status, output_text=text,
                           output=[SimpleNamespace(content=content)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=8,
                                                 input_tokens_details=SimpleNamespace(cached_tokens=20)))


def provider(*responses, model='gpt-4o-mini'):
    client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=responses)))
    return OpenAILLM(model=model, client=client,
                     input_price=0.15, cached_input_price=0.075, output_price=0.60)


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setattr(llm_label, 'PROMPT_VERSION', 'api-test-v1')
    con = duckdb.connect(':memory:')
    con.execute('''CREATE TABLE silver_tickets (
        ticket_id VARCHAR, subject VARCHAR, body VARCHAR, is_deleted BOOLEAN, category VARCHAR)''')
    con.execute("INSERT INTO silver_tickets VALUES ('T-1','Error','SSO crash',false,'bug')")
    yield con
    con.close()


def test_structured_request_and_real_usage_are_recorded(con):
    llm = provider(response())
    result = llm_label.label_tickets(con, llm)
    request = llm.client.responses.create.call_args.kwargs
    assert request['text']['format']['strict'] is True
    assert request['text']['format']['schema']['additionalProperties'] is False
    assert request['store'] is False
    assert result['labeled'] == 1
    assert result['input_tokens'] == 100 and result['output_tokens'] == 8
    assert result['usage_cost_usd'] == pytest.approx((80 * 0.15 + 20 * 0.075 + 8 * 0.60) / 1_000_000)
    assert con.execute('SELECT response_model FROM llm_api_usage').fetchone() == ('gpt-4o-mini-2024-07-18',)


def test_cached_rerun_makes_zero_provider_requests(con):
    first = llm_label.label_tickets(con, provider(response()))
    second_provider = provider()
    second = llm_label.label_tickets(con, second_provider)
    assert first['calls'] == 1 and second['calls'] == 0
    assert second['estimated_cost_usd'] == 0 and second['usage_cost_usd'] == 0
    second_provider.client.responses.create.assert_not_called()


def test_duplicate_inputs_share_one_request_and_estimate(con):
    con.execute("INSERT INTO silver_tickets VALUES ('T-2','Error','SSO crash',false,'bug')")
    result = llm_label.label_tickets(con, provider(response()))
    assert result['uncached_inputs'] == result['calls'] == 1
    assert result['labeled'] == 2


def test_dry_run_does_not_call_model_or_publish_labels(con):
    llm = provider()
    result = llm_label.label_tickets(con, llm, dry_run=True)
    assert result['calls'] == 0 and result['uncached_inputs'] == 1
    llm.client.responses.create.assert_not_called()
    assert con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='gold_ticket_labels'").fetchone() == (0,)


@pytest.mark.parametrize('bad_response', [
    response('{"label":"export"}'),
    response('{"label":"bug","extra":true}'),
    response('{"label":"bug"}', status='incomplete'),
    response('', refusal='Cannot classify this ticket'),
])
def test_rejected_responses_go_to_quarantine_and_are_cached(con, bad_response):
    llm = provider(bad_response)
    first = llm_label.label_tickets(con, llm)
    second = llm_label.label_tickets(con, llm)
    assert first['quarantined'] == 1 and first['labeled'] == 0
    assert second['calls'] == 0
    assert con.execute('SELECT count(*) FROM gold_ticket_labels').fetchone() == (0,)
    assert con.execute('SELECT count(*) FROM llm_label_quarantine').fetchone() == (1,)


def test_new_prompt_and_model_have_separate_cache_entries(con, monkeypatch):
    llm_label.label_tickets(con, provider(response(number=1)))
    monkeypatch.setattr(llm_label, 'PROMPT_VERSION', 'api-test-v2')
    prompt_change = llm_label.label_tickets(con, provider(response(number=2)))
    model_change = llm_label.label_tickets(con, provider(response(number=3), model='another-model'))
    assert prompt_change['calls'] == model_change['calls'] == 1
    assert con.execute('SELECT count(*) FROM llm_label_cache').fetchone() == (3,)


def test_network_failure_preserves_previous_gold_and_completed_cache(con):
    llm_label.label_tickets(con, provider(response(number=1)))
    previous_gold = con.execute('SELECT * FROM gold_ticket_labels').fetchall()
    con.execute("INSERT INTO silver_tickets VALUES ('T-2','Invoice','VAT issue',false,'billing'), ('T-3','Help','Usage question',false,'other')")
    interrupted = provider(response('{"label":"billing"}', number=2), ConnectionError('offline'))
    with pytest.raises(ConnectionError):
        llm_label.label_tickets(con, interrupted)
    assert con.execute('SELECT * FROM gold_ticket_labels').fetchall() == previous_gold
    assert con.execute('SELECT count(*) FROM llm_label_cache').fetchone() == (2,)
    resumed = llm_label.label_tickets(con, provider(response('{"label":"other"}', number=3)))
    assert resumed['calls'] == 1 and resumed['labeled'] == 3


def test_deleted_tickets_are_not_sent_to_provider(con):
    con.execute("INSERT INTO silver_tickets VALUES ('T-97',NULL,NULL,true,NULL)")
    result = llm_label.label_tickets(con, provider(response()))
    assert result['tickets'] == result['calls'] == 1


@pytest.mark.parametrize('value', ['-1', 'nan', 'inf', 'not-a-number'])
def test_bad_price_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv('LLM_INPUT_PRICE_PER_1M', value)
    with pytest.raises(ValueError):
        _price('LLM_INPUT_PRICE_PER_1M', 0.15)


def test_environment_credentials_take_precedence_over_private_file(monkeypatch, tmp_path):
    import openai
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text('OPENAI_API_KEY=file-key\nLLM_MODEL=gpt-4o-mini\n')
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-key')
    monkeypatch.setenv('LLM_MODEL', 'gpt-4o-mini')
    factory = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(openai, 'OpenAI', factory)
    OpenAILLM.from_env()
    assert factory.call_args.kwargs['api_key'] == 'environment-key'


def test_missing_key_is_reported_without_a_network_request(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='OPENAI_API_KEY'):
        OpenAILLM.from_env()
