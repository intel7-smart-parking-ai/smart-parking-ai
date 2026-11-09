"""Public chat capabilities. Client text/role/session IDs never grant privileges."""
PUBLIC_TASKS = frozenset({'spaces', 'fees', 'vehicle', 'policies'})
ADMIN_TASKS = frozenset({'revenue', 'alerts'})


class PermissionDenied(Exception):
    pass


def require_task(task, allowed_tasks):
    if task not in allowed_tasks:
        raise PermissionDenied('관리자 전용 정보입니다. 관리자 페이지에 로그인한 뒤 조회해 주세요.')
