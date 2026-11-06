"""Publish export completion only after verifying the actual destination files."""
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from parking_ai.storage import atomic_json,safe_path


def unique_report_fields(pairs):
    fields=dict(pairs)
    if len(fields)!=len(pairs):
        raise ValueError('내보낸 결과 기록에 중복된 JSON 필드가 있습니다.')
    return fields


class ExportTransaction:
    def __init__(self,root):
        self.root=Path(root);self.files={};self.finished=False
        self.status_path=self.root/'EXPORT_STATUS.json'
        self.started=datetime.now(timezone.utc).isoformat()
    def status(self,state,**extra):
        return {'상태':state,'시작시각_UTC':self.started,
            '설명':'완료 상태와 파일 해시가 확인된 폴더만 사용하세요. 원본 데이터는 삭제하지 않습니다.',**extra}
    def __enter__(self):
        atomic_json(self.status_path,self.status('진행 중'))
        return self
    def verify(self,relative,digest):
        path=safe_path(self.root,relative);relative=path.relative_to(self.root.resolve()).as_posix()
        previous=self.files.get(relative)
        if previous is not None and previous['SHA256']!=digest:
            raise ValueError('내보내기 검증 기준 해시를 바꿀 수 없습니다: '+relative)
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=digest:raise ValueError('내보낸 파일 SHA256 불일치: '+relative)
        self.files[relative]={'파일':relative,'바이트':len(payload),'SHA256':digest}
    def report(self,relative,expected):
        path=safe_path(self.root,relative);payload=path.read_bytes()
        # JSON true, 1 and 1.0 must not compare equal in counts or coordinates.
        actual=json.loads(payload,object_pairs_hook=unique_report_fields)
        if json.dumps(actual,sort_keys=True,allow_nan=False)!=json.dumps(expected,sort_keys=True,allow_nan=False):
            raise ValueError('내보낸 결과 기록 불일치: '+relative)
        self.verify(relative,hashlib.sha256(payload).hexdigest())
    def finish(self):
        actual={p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file() and p!=self.status_path}
        if actual!=set(self.files):raise ValueError('내보내기 기록과 실제 파일 목록이 다릅니다.')
        for row in list(self.files.values()):self.verify(row['파일'],row['SHA256'])
        atomic_json(self.status_path,self.status('완료',완료시각_UTC=datetime.now(timezone.utc).isoformat(),
            검증파일수=len(self.files),파일목록=list(self.files.values())))
        self.finished=True
    def __exit__(self,kind,error,traceback):
        # Hard interruptions leave the durable pending marker. Ordinary errors
        # add diagnostics if the disk remains writable, without masking the error.
        if kind is not None and issubclass(kind,Exception):
            try:
                atomic_json(self.root/'FAILED.json',{'사유':str(error),'설명':'완료되지 않은 내보내기입니다. 다시 내보내세요.'})
                atomic_json(self.status_path,self.status('실패',사유=str(error)))
            except Exception:pass
        if kind is None and not self.finished:raise RuntimeError('검증 없이 내보내기를 완료할 수 없습니다.')
        return False
