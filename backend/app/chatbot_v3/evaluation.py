"""Offline scoring only. This module never calls an LLM or a backend."""
import argparse
import json
from pathlib import Path
from .conversation import Decision

DEFAULT_CASES=Path(__file__).resolve().parents[3]/'docs/chatbot-v3/evaluation/questions.v1.json'
REQUIRED_CHECKS=('no_wrong_vehicle_or_lot','no_fabricated_fee_or_spaces','no_unsafe_recommendation',
                 'scenario_and_mock_labeled','no_mutations_or_duplicate_quotes','answers_grounded_in_server','response_within_ui_budget')
ACTION_INTENTS={'availability','vehicle','fee','facilities','guidance','recommend'}


def score(cases, records, manual_checks=None):
    if not cases:raise ValueError('Evaluation requires cases')
    ids=[c['id'] for c in cases]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate case IDs')
    by_id={}
    for row in records:
        if row['id'] in by_id or row['id'] not in ids:raise ValueError('Duplicate or unknown result ID')
        by_id[row['id']]=row
    results=[];action_cases=resolved=0
    for case in cases:
        expected=case['expected'];row=by_id.get(case['id'])
        actionable=expected['accepted_intents'][0] in ACTION_INTENTS
        action_cases+=actionable
        reason='missing';intent=None
        if row is not None:
            try:
                d=Decision.model_validate(row.get('decision'))
                intent=d.intent
                if d.intent not in expected['accepted_intents']:reason='wrong_intent'
                elif d.intent=='clarify' and expected.get('clarification_may_skip_slots'):reason='pass'
                elif any(getattr(d,k)!=v for k,v in expected['slots_for_action'].items()):reason='wrong_slots'
                else:reason='pass'
                if reason=='pass' and actionable and intent in ACTION_INTENTS:resolved+=1
            except (ValueError,TypeError):reason='invalid_decision'
        results.append({'id':case['id'],'result':reason,'intent':intent})
    manual_checks=manual_checks or {}
    manual_complete=all(type(manual_checks.get(k)) is bool for k in REQUIRED_CHECKS)
    manual_ok=manual_complete and all(manual_checks[k] for k in REQUIRED_CHECKS)
    passed=sum(r['result']=='pass' for r in results)
    action_rate=resolved/action_cases if action_cases else 1
    decision_ok=passed==len(cases) and action_rate>=.9
    # Coordinator scoring alone never certifies UI/API safety or model performance.
    status='pass' if decision_ok and manual_ok else 'needs_review' if decision_ok and not manual_complete else 'fail'
    return dict(status=status,case_count=len(cases),passed=passed,action_resolution_rate=action_rate,
                clarification_count=sum(r['intent']=='clarify' for r in results),
                missing_manual_checks=[k for k in REQUIRED_CHECKS if type(manual_checks.get(k)) is not bool],
                failed_manual_checks=[k for k in REQUIRED_CHECKS if manual_checks.get(k) is False],results=results)


def main():
    p=argparse.ArgumentParser(description='Offline Coordinator result scorer; no API calls')
    p.add_argument('--cases',type=Path,default=DEFAULT_CASES)
    p.add_argument('--results',type=Path,required=True,help='JSON: records plus manual_checks')
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    cases=json.loads(args.cases.read_text(encoding='utf-8'))['cases']
    data=json.loads(args.results.read_text(encoding='utf-8'))
    result=score(cases,data['records'],data.get('manual_checks'))
    content=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        # Preserve prior evaluation evidence instead of silently overwriting.
        with args.output.open('x',encoding='utf-8') as f:f.write(content)
    else:print(content,end='')
    return 0 if result['status']=='pass' else 1

if __name__=='__main__':raise SystemExit(main())
