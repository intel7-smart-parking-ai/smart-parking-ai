"""Exit assistance; depends only on the shared reply templates."""
from .. import templates as T


def h_exit_help(s, p):
    """출구·차단기 문의: 진단하지 않고 현장 관리자 연결을 안내한다(차량 번호·API 조회 없음)."""
    return T.EXIT_HELP, None, ["call_admin", "fee", "find_car"], None
