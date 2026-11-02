"""두 서버가 공유하는 관리자 변경 계약."""
from . import engine, incidents
from .state import Conflict


def change_report(inc_id, action, body):
    # runtime 검사와 보고서 변경을 초기화와 원자적으로 처리한다.
    with engine._lock:
        return _change_report(inc_id, action, body)


def _change_report(inc_id, action, body):
    if body.get("runtime_id") != engine.RUNTIME_ID:
        raise Conflict("서버가 바뀌었거나 화면 정보가 없습니다. 최신 보고서를 다시 불러와 주세요.")
    revision = body.get("expected_revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("expected_revision이 필요합니다.")
    if action == "check":
        index, checked = body.get("index"), body.get("checked")
        if type(index) is not int or type(checked) is not bool:
            raise ValueError("index 정수와 checked boolean이 필요합니다.")
        result = incidents.set_check(inc_id, index, checked, revision)
    elif action == "notes":
        result = incidents.set_notes(inc_id, body.get("text", ""), revision)
    elif action == "confirm":
        result = incidents.confirm(inc_id, revision, body.get("notes"))
    else:
        raise ValueError("알 수 없는 보고서 작업입니다.")
    if not result:
        raise ValueError("없는 보고서입니다.")
    if "error" in result:
        raise (Conflict if result.get("conflict") else ValueError)(result["error"])
    return result
