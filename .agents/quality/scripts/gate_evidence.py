#!/usr/bin/env python3
"""Check required journey evidence. Exit 0 pass, 1 acceptance incomplete, 2 invalid input.
Evidence presence is necessary, not a proof of authenticity: run from a trusted CI reporter.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ALLOWED_STATUS = {'passed', 'failed', 'blocked', 'not_run', 'skipped', 'flaky'}


def evidence_path(root: Path, value: str) -> Path:
    p = Path(value)
    if p.is_absolute() or '..' in p.parts or ':' in value or '\\' in value:
        raise ValueError('Evidence path must be a portable relative path under the report folder')
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f'Missing or unsafe evidence file: {value}')
    return resolved


def evaluate(contracts: dict, report: dict, evidence_root: Path, expected_revision: str, scope: str, expected_fingerprint: str):
    if not isinstance(contracts, dict) or not isinstance(report, dict):
        raise ValueError('Contracts and report must be JSON objects')
    if not isinstance(contracts.get('contracts'), list):
        raise ValueError('contracts must contain a contracts list')
    if not isinstance(report.get('run'), dict) or not isinstance(report.get('checks'), list):
        raise ValueError('Report requires run object and checks list')
    if any(not isinstance(c, dict) for c in contracts['contracts'] + report['checks']):
        raise ValueError('Every contract/check must be an object')
    problems = []
    run = report['run']
    if not expected_revision or run.get('revision') != expected_revision:
        problems.append('Report revision does not match expected revision')
    if run.get('kind') != 'website-acceptance':
        problems.append('Report is not a website-acceptance run')
    if not expected_fingerprint or run.get('source_fingerprint') != expected_fingerprint:
        problems.append('Source fingerprint does not match the trusted runner input')
    if run.get('environment') not in {'local-isolated', 'staging-isolated'}:
        problems.append('Acceptance must identify an isolated local/staging environment')
    checks = report.get('checks', [])
    by_id = {}
    for c in checks:
        cid = c.get('id')
        if not cid or cid in by_id:
            problems.append(f'Missing/duplicate check ID: {cid}')
        by_id[cid] = c
        if c.get('status') not in ALLOWED_STATUS:
            problems.append(f'{cid}: unknown status')
    required = [c for c in contracts['contracts'] if c.get('required') and (scope == 'all' or c.get('scope') == scope)]
    if not required:
        problems.append('Selected scope contains zero required contracts')
    for contract in required:
        cid = contract['id']
        c = by_id.get(cid)
        if not c:
            problems.append(f'{cid}: not_run (no check reported)')
            continue
        if c.get('status') != 'passed':
            problems.append(f"{cid}: {c.get('status')} does not count as passed")
        if c.get('kind') != 'browser' or c.get('exit_code') != 0:
            problems.append(f'{cid}: requires an executed browser check with exit_code=0')
        assertions = c.get('assertions', {})
        for a in contract['assertions']:
            if assertions.get(a) is not True:
                problems.append(f'{cid}: missing/failed assertion {a}')
        kinds = set()
        for evidence in c.get('evidence', []):
            try:
                evidence_path(evidence_root, evidence['path'])
                kinds.add(evidence['kind'])
            except (ValueError, KeyError, TypeError) as exc:
                problems.append(f'{cid}: {exc}')
        for kind in contract.get('required_evidence', ['screenshot', 'execution-log']):
            if kind not in kinds:
                problems.append(f'{cid}: missing evidence kind {kind}')
    for f in report.get('findings', []):
        if f.get('blocking') and f.get('status') != 'resolved':
            problems.append(f"Open blocking finding: {f.get('id', 'unassigned')}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--contracts', type=Path, default=Path(__file__).resolve().parents[1] / 'contracts.json')
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--revision', required=True)
    ap.add_argument('--source-fingerprint', required=True, help='Independently computed identity of commit plus working changes; do not copy from report.')
    ap.add_argument('--scope', default='all', choices=['all', 'auth', 'team', 'submission', 'organizer'])
    args = ap.parse_args()
    try:
        contracts = json.loads(args.contracts.read_text(encoding='utf-8'))
        report = json.loads(args.report.read_text(encoding='utf-8'))
        problems = evaluate(contracts, report, args.report.parent, args.revision, args.scope, args.source_fingerprint)
        print(json.dumps({'verdict': 'no-go' if problems else 'go', 'problems': problems,
                          'scope': args.scope, 'meaning': 'Evidence-contract gate only; no deployment authorization.'}, ensure_ascii=False, indent=2))
        return 1 if problems else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f'INVALID_REPORT: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
