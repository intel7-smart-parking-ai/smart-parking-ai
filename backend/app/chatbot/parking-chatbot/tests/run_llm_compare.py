"""LLM 전용 세트 비교. --compare와 활성화 설정이 있을 때만 실제 API를 호출한다."""
import argparse
import csv
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chatbot import config, parser, llm_router


def evaluate(rows, classify):
    correct, times, failures = 0, [], []
    for index, row in enumerate(rows, 1):
        started = time.perf_counter()
        result = classify(row['question'])
        times.append((time.perf_counter() - started) * 1000)
        actual = (result.intent, *(result.params.get(k, '') for k in ('lot_id','car_id','zone')))
        expected = tuple(row[k] for k in ('intent','lot_id','car_id','zone'))
        if actual == expected: correct += 1
        else: failures.append(index)
    return {'correct':correct,'total':len(rows),'accuracy':round(correct/len(rows),4) if rows else 0,
            'mean_ms':round(sum(times)/len(times),2) if times else 0,'max_ms':round(max(times,default=0),2),
            'failed_rows':failures}


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('file', nargs='?', default=str(Path(__file__).with_name('llm_eval_questions.csv')))
    args.add_argument('--compare', action='store_true', help='활성화된 LLM과 비교 (실제 API 비용 발생 가능)')
    options = args.parse_args()
    with Path(options.file).open(encoding='utf-8') as stream: rows=list(csv.DictReader(stream))
    if not rows: raise SystemExit('평가 문장이 없습니다.')
    matched=[i for i,row in enumerate(rows,1) if parser.parse_rules(row['question']).intent!='unknown']
    if matched: raise SystemExit('규칙에서 이미 처리하는 행이 포함됨: '+str(matched))
    report={'rules':evaluate(rows,parser.parse_rules)}
    key_name='OPENAI_API_KEY' if config.LLM_PROVIDER=='openai' else 'ANTHROPIC_API_KEY'
    if not options.compare or config.LLM_PROVIDER=='none' or not config.LLM_MODEL or not os.getenv(key_name):
        report['hybrid']={'skipped':'--compare 및 LLM_PROVIDER/LLM_MODEL/API 키 설정 필요. 실제 LLM 호출 없음.'}
    elif config.LLM_ENGINE!='langchain':
        report['hybrid']={'skipped':'LangChain 비교에는 LLM_ENGINE=langchain을 사용하세요.'}
    else:
        llm_router.warmup()
        end=time.monotonic()+3
        while llm_router.stats()['preparing'] and time.monotonic()<end:time.sleep(.01)
        before=llm_router.stats()['counts']
        report['hybrid']=evaluate(rows,parser.parse)
        report['hybrid']['router_counts']={k:v-before.get(k,0) for k,v in llm_router.stats()['counts'].items()}
    report['notice']='직접 작성한 평가 세트. 가짜 모델 검사는 LLM 정확도의 증거가 아닙니다.'
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
