"""Session-separated evaluation of saved predictions against independent truth."""
from pathlib import Path
import argparse
import hashlib
import json
import statistics
import math
from .policy import CATALOG
from .image_input import MAX_BYTES, decode_image
from .registration import expected_ids


def cohort_metrics():
    return {'vehicle_observations':0,'vehicle_ids':[],
        'joint':{'correct':0,'compared':0,'incomplete_truth':0},
        'occupancy':{'correct':0,'wrong':0,'unknown':0},
        'number':{'correct':0,'wrong':0,'null':0},
        'color':{kind:{'correct':0,'wrong':0,'null':0} for kind in ('general','ev','accessible')}}


def evaluate(manifest,base):
    sessions={};hashes={};ids=set();samples=manifest['samples']
    roster=manifest.get('vehicle_cohorts',{})
    if not isinstance(roster,dict):raise ValueError('학습 여부 명부 형식 오류')
    evidence={}
    for number,entry in roster.items():
        if number not in CATALOG or not isinstance(entry,dict) or entry.get('cohort') not in ('trained','untrained','unknown'):
            raise ValueError('학습 여부 명부의 번호/구분 오류')
        if entry['cohort']!='unknown':
            reference=entry.get('evidence')
            if not isinstance(reference,str) or not reference.strip() or not (base/reference).is_file():
                raise ValueError('학습 여부를 확인할 근거 파일이 필요합니다.')
            evidence[number]={'cohort':entry['cohort'],'evidence_sha256':hashlib.sha256((base/reference).read_bytes()).hexdigest()}
    for sample in samples:
        split=sample['split'];session=sample['session_id'];identity=sample['sample_id']
        if split not in ('train','val','test') or not isinstance(session,str) or not session.strip() or not isinstance(identity,str) or not identity or identity in ids:
            raise ValueError('분할·세션·중복 sample_id 오류')
        ids.add(identity)
        with (base/sample['image']).open('rb') as source:
            photo=decode_image(source.read(MAX_BYTES+1))
        with photo:
            digest=(photo.size,hashlib.sha256(photo.tobytes()).digest())
        if session in sessions and sessions[session]!=split or digest in hashes:
            raise ValueError('같은 세션 또는 같은 원본이 여러 분할에 섞였습니다.')
        sessions[session]=split;hashes[digest]=split
    metrics={'scenes':0,'spaces':0,'occupancy':{'correct':0,'wrong':0,'unknown':0},
        'number':{'correct':0,'wrong':0,'null':0},
        'color':{kind:{'correct':0,'wrong':0,'null':0} for kind in ('general','ev','accessible')},
        'empty_false_identity':0,'failures':[],
        'cohorts':{name:cohort_metrics() for name in ('trained','untrained','unknown')}}
    seen_vehicles={name:set() for name in metrics['cohorts']}
    per_vehicle={}
    physical_tests=True
    elapsed=[]
    for sample in samples:
        if sample['split']!='test':continue
        if sample.get('truth_reviewed_independently') is not True:
            raise ValueError('시험 정답은 모델 출력과 독립적으로 검토되어야 합니다.')
        physical_tests=physical_tests and sample.get('sample_type')=='physical_capture'
        prediction=json.loads((base/sample['prediction']).read_text(encoding='utf8'))
        lot=sample['lot_id'];truth=sample['truth'];rows={r['space_id']:r for r in prediction['spaces']}
        if lot not in ('A','B'):raise ValueError('시험 주차장 이름 오류')
        if sorted(truth)!=sorted(expected_ids(lot)) or len(rows)!=len(prediction['spaces']):
            raise ValueError('전체 칸 정답 또는 중복 예측 오류')
        metrics['scenes']+=1
        duration=prediction.get('total_ms')
        if type(duration) in (int,float) and math.isfinite(duration) and duration>=0:elapsed.append(duration)
        for sid,target in truth.items():
            row=rows.get(sid,{}) if prediction.get('lot_id')==lot else {}
            actual=row.get('observation','unknown');expected=target['occupancy'];metrics['spaces']+=1
            if expected not in ('occupied','empty'):raise ValueError('점유 정답 형식 오류')
            if target.get('vehicle_type') not in (None,'general','ev','accessible'):raise ValueError('종류 정답 형식 오류')
            if target.get('car_id') is not None and target['car_id'] not in CATALOG:raise ValueError('정답 번호가 D-54 목록 밖입니다.')
            if expected=='empty' and (target.get('car_id') or target.get('vehicle_type')):raise ValueError('빈칸 정답에 차량 정보가 있습니다.')
            if target.get('car_id') and target.get('vehicle_type') and CATALOG[target['car_id']]!=target['vehicle_type']:raise ValueError('정답 번호와 종류 충돌')
            key='unknown' if actual not in ('occupied','empty') else 'correct' if actual==expected else 'wrong'
            metrics['occupancy'][key]+=1
            group=None
            if expected=='occupied':
                number=target.get('car_id');cohort=roster.get(number,{}).get('cohort','unknown')
                group=metrics['cohorts'][cohort];group['vehicle_observations']+=1;group['occupancy'][key]+=1
                if number:seen_vehicles[cohort].add(number)
                joint=group['joint']
                if number and target.get('vehicle_type'):
                    correct=(actual==expected and row.get('car_id')==number and
                             row.get('vehicle_type')==target['vehicle_type'])
                    joint['compared']+=1;joint['correct']+=int(correct)
                    counts=per_vehicle.setdefault(number,[0,0])
                    counts[0]+=int(correct);counts[1]+=1
                else:
                    joint['incomplete_truth']+=1
            if key!='correct':metrics['failures'].append({'sample':sample['sample_id'],'space':sid,'category':'occupancy','expected':expected,'actual':actual})
            if expected=='occupied' and target.get('car_id'):
                if target['car_id'] not in CATALOG:raise ValueError('정답 번호가 D-54 목록 밖입니다.')
                actual=row.get('car_id');key='null' if actual is None else 'correct' if actual==target['car_id'] else 'wrong'
                metrics['number'][key]+=1
                group['number'][key]+=1
                if key!='correct':metrics['failures'].append({'sample':sample['sample_id'],'space':sid,'category':'number','expected':target['car_id'],'actual':actual})
            if expected=='occupied' and target.get('vehicle_type'):
                kind=target['vehicle_type'];actual=row.get('vehicle_type')
                key='null' if actual is None else 'correct' if actual==kind else 'wrong'
                metrics['color'][kind][key]+=1;group['color'][kind][key]+=1
            if expected=='empty' and row.get('car_id') is not None:metrics['empty_false_identity']+=1
    if not metrics['scenes']:raise ValueError('독립 시험 장면이 없습니다.')
    for name,vehicles in seen_vehicles.items():
        metrics['cohorts'][name].update(vehicle_ids=sorted(vehicles),unique_vehicle_count=len(vehicles))
    # Equal weight per vehicle: repeated frames of one parked car must not
    # dominate the difference between the 6 trained and 18 other vehicles.
    rates={}
    for name,required in [('trained',6),('untrained',18)]:
        declared={n for n,item in roster.items() if item['cohort']==name}
        group=metrics['cohorts'][name]
        if (len(declared)==required and seen_vehicles[name]==declared and
                not group['joint']['incomplete_truth']):
            rates[name]=statistics.mean(per_vehicle[n][0]/per_vehicle[n][1] for n in declared)
    training_inventory=(manifest.get('training_inventory_complete') is True and 'train' in sessions.values())
    complete=(len(rates)==2 and len(roster)==24 and not metrics['cohorts']['unknown']['vehicle_observations']
              and physical_tests and training_inventory)
    metrics['full_24_comparison']={
        'available':complete,'train_split_supplied':'train' in sessions.values(),
        'all_test_samples_declared_physical':physical_tests,'training_inventory_declared_complete':training_inventory,
        'trained_joint_rate':rates['trained'] if complete else None,
        'untrained_joint_rate':rates['untrained'] if complete else None,
        'trained_minus_untrained_pp':100*(rates['trained']-rates['untrained']) if complete else None,
        'basis':'per-vehicle mean of correct occupied slot, number and type together; requires documented 6+18, independent test truth and train split'}
    metrics.update(model_version=manifest.get('model_version'),device=manifest.get('device'),
        mean_frame_ms=round(statistics.mean(elapsed),2) if elapsed else None,
        p95_frame_ms=round(sorted(elapsed)[min(len(elapsed)-1,int(len(elapsed)*.95))],2) if elapsed else None,
        split_checked=True,cohort_evidence=evidence,
        cohort_scope='occupied truth only; unique truth IDs counted separately from repeated observations; cohort declarations require evidence files, whose training provenance must be reviewed by a person')
    return metrics


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('manifest',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();report=evaluate(json.loads(args.manifest.read_text(encoding='utf8')),args.manifest.parent)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
