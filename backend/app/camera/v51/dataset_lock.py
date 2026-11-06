"""Reentrant local-process/thread serialization with an OS dataset byte lock.

Cooperating V28+ writers only. Never delete the lock path: replacing its inode
would let a second process lock a different file while the first still writes.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import errno,os,threading,time


class DatasetBusyError(ValueError):pass


_registry={};_registry_guard=threading.Lock()


def acquire_os_lock(handle):
    # A killed Windows writer can briefly leave a busy byte lock after wait().
    # Retry contention only, for at most 350 ms; never proceed without a lock.
    delays=(0,.01,.04,.1,.2)
    for attempt,delay in enumerate(delays):
        if delay:time.sleep(delay)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES,errno.EAGAIN) or attempt==len(delays)-1:raise


@contextmanager
def dataset_write(root):
    root=Path(root).resolve();key=os.path.normcase(str(root))
    with _registry_guard:
        state=_registry.setdefault(key,{'mutex':threading.RLock(),'depth':0})
    with state['mutex']:
        if state['depth']:
            state['depth']+=1
            try:yield
            finally:state['depth']-=1
            return
        root.mkdir(parents=True,exist_ok=True)
        handle=(root/'.writer.lock').open('a+b');locked=False
        try:
            handle.seek(0,os.SEEK_END)
            if handle.tell()==0:handle.write(b'0');handle.flush()
            handle.seek(0)
            try:
                acquire_os_lock(handle)
            except OSError as exc:raise DatasetBusyError('다른 프로그램이 이 데이터 폴더를 수정 중입니다. 작업 완료 후 다시 시도하세요: '+str(root)) from exc
            locked=True;state['depth']=1
            try:yield
            finally:state['depth']=0
        finally:
            try:
                if locked:
                    handle.seek(0)
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            finally:handle.close()


def dataset_writer(fn):
    @wraps(fn)
    def wrapped(root,*args,**kwargs):
        with dataset_write(root):return fn(root,*args,**kwargs)
    return wrapped


def dataset_method_writer(fn):
    @wraps(fn)
    def wrapped(self,*args,**kwargs):
        with dataset_write(self.root):return fn(self,*args,**kwargs)
    return wrapped
