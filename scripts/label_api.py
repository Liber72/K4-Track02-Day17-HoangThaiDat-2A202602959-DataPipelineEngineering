"""Label Silver tickets with OpenAI, then verify a free cache re-run.

    python main.py                         # first build Bronze/Silver/Gold
    python -m scripts.label_api --dry-run   # estimate without calling OpenAI
    python -m scripts.label_api             # real calls + cache re-run

Reuses the existing warehouse and does not reset the cache.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline import config, llm_label
from pipeline.llm_api import OpenAILLM, load_api_environment
from pipeline.run import connect


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='estimate only; make zero API calls')
    ap.add_argument('--prompt-version', help='a new version intentionally bypasses existing cache')
    ap.add_argument('--evidence', type=Path,
                    default=config.SUBMISSION_DIR / 'evidence' / 'real-api.json')
    args = ap.parse_args(argv)
    try:
        if not config.WAREHOUSE.exists():
            raise ValueError('Run python main.py first to build the Silver tickets.')
        load_api_environment()
        version = args.prompt_version or os.environ.get('LLM_PROMPT_VERSION', 'triage-api-v1')
        if not version.strip():
            raise ValueError('The prompt version must not be empty.')
        llm_label.PROMPT_VERSION = version
        llm = OpenAILLM.from_env()
        con = connect()
        try:
            if not llm_label.live_tickets(con):
                raise ValueError('No live Silver tickets. Run python main.py first.')
            print(f'=== real OpenAI ticket labels: model={llm.model}, prompt={version} ===')
            first = llm_label.label_tickets(con, llm, dry_run=args.dry_run)
            if args.dry_run:
                print(json.dumps(first, indent=2))
                print('DRY RUN: no OpenAI requests sent.')
                return 0
            second = llm_label.label_tickets(con, llm)
            records = con.execute("""SELECT g.ticket_id, g.label, s.category AS human_category,
                                             g.model, g.prompt_version
                                      FROM gold_ticket_labels g JOIN silver_tickets s USING (ticket_id)
                                      ORDER BY g.ticket_id""").fetchall()
            columns = ['ticket_id', 'label', 'human_category', 'model', 'prompt_version']
            labels = [dict(zip(columns, row)) for row in records]
            journal = con.execute("""SELECT count(*), sum(input_tokens), sum(output_tokens),
                                             sum(cost_usd), list(DISTINCT response_model)
                                      FROM llm_api_usage WHERE model=? AND prompt_version=?""",
                                  [llm.model, version]).fetchone()
            ok = second['calls'] == 0 and first['quarantined'] == 0 and len(labels) == first['tickets']
            evidence = {
                'ran_at_utc': datetime.now(timezone.utc).isoformat(), 'provider': 'OpenAI',
                'model': llm.model, 'prompt_version': version, 'first_run': first,
                'cache_rerun': second, 'labels': labels,
                'usage_journal': dict(zip(
                    ['responses', 'input_tokens', 'output_tokens', 'usage_cost_usd', 'response_models'],
                    journal)),
                'result': 'PASS' if ok else 'FAIL',
            }
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
            print(f"  first run: {first['calls']} API calls; {first['labeled']} labels; "
                  f"{first['quarantined']} quarantined")
            print(f"  usage: {first['input_tokens']} input tokens, {first['output_tokens']} output tokens")
            if first['usage_cost_usd'] is not None:
                print(f"  usage-based cost estimate: ${first['usage_cost_usd']:.6f}")
            print(f"  cache re-run: {second['calls']} API calls")
            for row in labels:
                print(f"  {row['ticket_id']}: LLM={row['label']}, human={row['human_category']}")
            print(f'Evidence: {args.evidence}')
            print('REAL API ' + evidence['result'])
            return 0 if ok else 1
        finally:
            con.close()
    except (ValueError, RuntimeError) as exc:
        print(f'Configuration error: {exc}', file=sys.stderr)
        return 2
    except Exception as exc:
        # Avoid logging request objects, credentials, or raw provider error bodies.
        status = getattr(exc, 'status_code', None)
        code = getattr(exc, 'code', None)
        safe_codes = {'insufficient_quota', 'rate_limit_exceeded', 'invalid_api_key',
                      'model_not_found', 'billing_hard_limit_reached', 'invalid_request_error'}
        code_text = f' [{code}]' if isinstance(code, str) and code in safe_codes else ''
        print(f'API run failed: {type(exc).__name__}' + (f' (HTTP {status})' if status else ''),
              file=sys.stderr)
        if code_text:
            print('Provider error code:' + code_text, file=sys.stderr)
        print('Check the API key, model access, network and billing. Re-run to reuse completed cache entries.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
