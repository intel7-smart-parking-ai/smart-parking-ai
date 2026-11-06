"""Scoped dataset coverage and prediction-vs-human diagnostics; no fabricated scores."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from dataset_lock import dataset_writer
import uuid
from contracts import CONTRACT_REVISION
from dataset19 import DatasetIndex,validate_prediction
from layouts import layout
from capture_views import VIEW_NAMES,view_code
from pipeline import read_json
from records import validate_record,load_original
from review import rows_for_edit,validate_review
from parking_ai.storage import atomic_json


TASKS=('occupancy','number','color')


def empty_metric():return {'compared':0,'predicted':0,'correct':0,'abstained':0,'confusion':{}}


def observe(metric,truth,predicted):
    metric['compared']+=1
    known=predicted not in (None,'unknown','')
    metric['predicted']+=int(known);metric['abstained']+=int(not known)
    metric['correct']+=int(known and predicted==truth)
    cells=metric['confusion'].setdefault(truth,{})
    value=predicted if known else 'unknown';cells[value]=cells.get(value,0)+1


def finalize(metric):
    metric.update(accuracy_including_abstention=metric['correct']/metric['compared'] if metric['compared'] else None,
        accuracy_when_predicted=metric['correct']/metric['predicted'] if metric['predicted'] else None,
        prediction_coverage=metric['predicted']/metric['compared'] if metric['compared'] else None)
    return metric


def build_audit(root,lot=None,session=None):
    root=Path(root);disk=DatasetIndex().scan(root,lot,session,True)
    # The disk scan has already verified both members, even for a single-lot report.
    verified_dual = {entry['id'] for entry in disk['entries'] if entry['verified_dual']}
    excluded_dual = []
    metrics={t:empty_metric() for t in TASKS};truth_counts=Counter();number_counts=Counter();color_counts=Counter()
    estimate_metric=empty_metric()
    slots={f'{l}/{s["space_id"]}':{'frames':0,'approved':0,'number_labels':0,'color_labels':0} for l in ('A','B') if lot in (None,l) for s in layout(l)['spaces']}
    lots={l:{'frames':0,'fully_reviewed_frames':0,'full_roi_frames':0,'batches':set(),'sessions':set(),'sources':Counter(),
        'metrics':{t:empty_metric() for t in TASKS}} for l in ('A','B') if lot in (None,l)}
    views={v:{'name':name,'frames':0,'approved_truth_counts':Counter(),'unmatched_truth_counts':Counter(),'metrics':{t:empty_metric() for t in TASKS}} for v,name in VIEW_NAMES.items()}
    errors=list(disk['errors']);mismatches=[];missing_predictions=0;unmatched_truth=Counter();identities=[]
    for path in sorted((root/'manifests').glob('*.json')):
        try:
            record=validate_record(read_json(path),root,path.stem)
            if lot and record['lot_id']!=lot:continue
            if session and record['session_id']!=session:continue
            if record['lot_id'] not in lots:continue
            if record['analysis_config'].get('dual_measurement') is not None and path.stem not in verified_dual:
                excluded_dual.append(path.stem)
                continue
            from capture_recovery import require_finished_capture
            require_finished_capture(root,record)
            image=load_original(root,record);l=record['lot_id'];item=lots[l]
            view=views[view_code(record)];view['frames']+=1
            item['frames']+=1;item['batches'].add(record['batch_id']);item['sessions'].add(record['session_id']);item['sources'][record['source']['kind']]+=1
            item['full_roi_frames']+=int(len(record['roi']['spaces'])==layout(l)['space_count'])
            for slot in record['roi']['spaces']:slots[l+'/'+slot['space_id']]['frames']+=1
            review_path=root/'reviews'/path.name
            if not review_path.exists():continue
            previous=read_json(review_path)
            rows=validate_review(record,rows_for_edit(record,previous),previous['reviewer'],image)
            approved=[r for r in rows if r['review_status']=='approved']
            item['fully_reviewed_frames']+=int(len(approved)==layout(l)['space_count'])
            prediction=None;pp=root/'predictions'/path.name
            try:
                if pp.exists():prediction=validate_prediction(read_json(pp),record)
            except Exception as exc:errors.append(path.name+' 예측 비교 제외: '+str(exc))
            if not prediction:missing_predictions+=1
            predicted={r['space_id']:r for r in prediction['spaces']} if prediction else {}
            for row in approved:
                s=slots[l+'/'+row['space_id']];s['approved']+=1
                truth={} if record['roi'].get('registration_method')=='virtual_sticker_guide_v1' else {'occupancy':row['occupancy']}
                if row['display_number']:
                    truth['number']=row['display_number'];number_counts[l+'/'+row['display_number']]+=1;s['number_labels']+=1
                if row['color']!='unknown':truth['color']=row['color'];color_counts[l+'/'+row['color']]+=1;s['color_labels']+=1
                for task,value in truth.items():
                    truth_counts[task]+=1;view['approved_truth_counts'][task]+=1
                    if row['space_id'] not in predicted:unmatched_truth[task]+=1;view['unmatched_truth_counts'][task]+=1;continue
                    actual=predicted[row['space_id']][task]
                    if task=='number' and not actual:
                        estimate=predicted[row['space_id']].get('number_estimate') or {}
                        if estimate.get('number'):
                            observe(estimate_metric,value,estimate['number'])
                    observe(metrics[task],value,actual);observe(item['metrics'][task],value,actual);observe(view['metrics'][task],value,actual)
                    if actual!=value:mismatches.append({'image_id':path.stem,'lot_id':l,'space_id':row['space_id'],'task':task,
                        'truth':value,'prediction':actual,'raw_path':record['raw_path'],'captured_at_utc':record['captured_at_utc']})
            identities.append({'image_id':path.stem,'raw_sha256':record.get('raw_sha256'),'batch_id':record['batch_id'],
                'prediction_schema':prediction.get('schema') if prediction else None,'approved_rows':len(approved)})
        except Exception as exc:errors.append(path.name+': '+str(exc))
    for item in lots.values():
        item['batches']=sorted(item['batches']);item['sessions']=sorted(item['sessions']);item['sources']=dict(item['sources'])
        item['metrics']={t:finalize(m) for t,m in item['metrics'].items()}
    for item in views.values():
        item['metrics']={t:finalize(m) for t,m in item['metrics'].items()}
        item['approved_truth_counts']=dict(item['approved_truth_counts']);item['unmatched_truth_counts']=dict(item['unmatched_truth_counts'])
    return {'views':views,'schema':'dataset-audit-v1','product_version':'V51','created_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':{'lot':lot,'session':session},'root':str(root.resolve()),'disk_counts':disk['counts'],
        'excluded_dual_scenes':excluded_dual,
        'approved_truth_counts':dict(truth_counts),'unmatched_truth_counts':dict(unmatched_truth),'reviewed_frames_without_valid_prediction':missing_predictions,
        'metrics':{t:finalize(m) for t,m in metrics.items()},'lots':lots,'slot_coverage':slots,'number_label_counts':dict(number_counts),
        'number_estimates_with_truth':finalize(estimate_metric),
        'color_label_counts':dict(color_counts),'mismatches':mismatches,'errors':errors,'reviewed_frames':identities,
        'interpretation':['This compares stored predictions with human-approved labels in the selected scope, not an independent field benchmark.',
            'Unknown predictions count as misses in accuracy_including_abstention; coverage and conditional accuracy are separate.',
            'Missing/invalid prediction files are excluded from metric denominators and explicitly counted in unmatched_truth_counts.',
            'Empty denominators are null, never 0% or 100%. Approval is not proof that a human label is correct.',
            'Frames and labels from repeated captures are not independent samples. Source kinds and batch counts must be inspected.']}


def render_report(report):
    def percent(value):return '평가 자료 없음' if value is None else f'{value*100:.2f}%'
    lines=['# V51 데이터 품질·정답 대조 보고서','',f"범위: {report['scope']} · 데이터 루트: {report['root']}",'',
        '저장 예측과 사람이 승인한 정답을 대조한 수집 진단입니다. 독립 시험셋의 현장 정확도로 해석하지 않습니다.','',
        '| 작업 | 비교 정답 수 | 자동 판독 수 | 일치 수 | 보류 수 | 보류 포함 일치율 | 판독한 것 중 일치율 | 판독률 |',
        '|---|---:|---:|---:|---:|---|---|---|']
    for task,m in report['metrics'].items():lines.append(f"| {task} | {m['compared']} | {m['predicted']} | {m['correct']} | {m['abstained']} | {percent(m['accuracy_including_abstention'])} | {percent(m['accuracy_when_predicted'])} | {percent(m['prediction_coverage'])} |")
    estimate=report.get('number_estimates_with_truth')
    if estimate:
        lines+=['',f"번호 직접 판독과 별도로, 추정 번호와 승인 번호를 비교한 {estimate['compared']}건 중 {estimate['correct']}건이 일치했습니다."]
    lines+=['',f"두 장면의 파일 대조를 통과하지 못해 평가에서 제외한 장면 {len(report.get('excluded_dual_scenes', []))}개",
        f"예측 누락/오류로 비교하지 못한 승인 항목: {report['unmatched_truth_counts']}",
        f"파일 대조 오류/비교 제외 기록: {len(report['errors'])}건. 승인 수와 실제 크롭 가능 수: {report['disk_counts']}",'',
        '## 주차장별 수집 범위','', '| 주차장 | 원본 장면 | 전체 ROI 장면 | 전칸 승인 장면 | 배치 수 | 출처 |','|---|---:|---:|---:|---:|---|']
    for lot,item in report['lots'].items():lines.append(f"| {lot} | {item['frames']} | {item['full_roi_frames']} | {item['fully_reviewed_frames']} | {len(item['batches'])} | {dict(item['sources'])} |")
    lines+=['','## 촬영 시점별 정답 대조','', '| 시점 | 장면 | 작업 | 비교 수 | 보류 포함 일치율 |','|---|---:|---|---:|---|']
    for view,item in report.get('views',{}).items():
        for task,m in item['metrics'].items():lines.append(f"| {item['name']} | {item['frames']} | {task} | {m['compared']} | {percent(m['accuracy_including_abstention'])} |")
    lines+=['','## 칸별 자료 수','', '| 주차장/칸 | 촬영 장면 | 승인 칸 | 번호 정답 | 색 정답 |','|---|---:|---:|---:|---:|']
    for slot,c in report['slot_coverage'].items():lines.append(f"| {slot} | {c['frames']} | {c['approved']} | {c['number_labels']} | {c['color_labels']} |")
    lines+=['','## 결과 해석','', '같은 배치의 반복 사진을 독립 시험으로 세지 마세요. 미등록·미검수 칸과 부족한 번호·색 조합을 보강하고 원본 가독성을 확인하세요. 번호·색별 수량, 작업별 혼동행렬, 불일치 장면 및 전체 오류는 같은 이름의 JSON에 기록했습니다. 값이 없는 경우 평가 자료 없음으로 표시합니다.','',
        '## 불일치 장면','', '| 장면 | 주차장/칸 | 작업 | 승인 정답 | 예측 |','|---|---|---|---|---|']
    for row in report['mismatches'][:200]:lines.append(f"| {row['image_id']} | {row['lot_id']}/{row['space_id']} | {row['task']} | {row['truth']} | {row['prediction']} |")
    if len(report['mismatches'])>200:lines.append('\n표시는 200건으로 제한했습니다. JSON에는 모든 불일치가 있습니다.')
    return '\n'.join(lines)+'\n'


@dataset_writer
def export_audit(root,lot=None,session=None):
    report=build_audit(root,lot,session);path=Path(root)/'reports'/('audit-'+uuid.uuid4().hex+'.json')
    atomic_json(path,report);path.with_suffix('.md').write_text(render_report(report),encoding='utf-8')
    return str(path),report
