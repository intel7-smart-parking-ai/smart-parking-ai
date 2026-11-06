"""OS-process writer contention and real process-death capture recovery."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import copy,gc,hashlib,json,os,subprocess,tempfile,threading,time,unittest
from unittest.mock import patch
from test_v19 import fixture,PROJECT
from dataset_lock import dataset_write,DatasetBusyError
from capture_recovery import recover_captures
from dataset19 import persist_scene,DatasetIndex
from collection_output import export_collection
from pipeline import read_json


def actor(mode,root,stage=None):
    root=Path(root)
    if mode=='hold':
        with dataset_write(root):print('LOCKED',flush=True);sys.stdin.readline()
        return
    cfg,p,roi=fixture()
    import capture_recovery,pipeline,dataset19
    journal_ready=False
    real_journal=capture_recovery.atomic_json;real_manifest=pipeline.atomic_json;real_asset=dataset19.atomic_json;real_fsync=os.fsync
    def journal(path,data):
        nonlocal journal_ready
        real_journal(path,data);journal_ready=True
        if stage=='journal':os._exit(87)
    def manifest(path,data):
        real_manifest(path,data)
        if stage=='manifest':os._exit(87)
    def asset(path,data):
        real_asset(path,data)
        if stage=='prediction' and Path(path).parent.name=='predictions':os._exit(87)
    def fsync(fd):
        real_fsync(fd)
        if journal_ready and stage=='raw':os._exit(87)
    capture_recovery.atomic_json=journal;pipeline.atomic_json=manifest;dataset19.atomic_json=asset;os.fsync=fsync
    if mode=='missing_prediction':pipeline.save_capture(root,p,roi,'b','s',cfg,scene_assets=True,prediction_expected=True)
    else:
        from vision19 import VisionEngine
        pred=VisionEngine().run(p,roi,cfg);persist_scene(root,p,roi,'b','s',cfg,pred)


def child(mode,root,stage=None):
    args=[sys.executable,str(Path(__file__).resolve()),mode,str(root)]
    if stage:args.append(stage)
    return subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)


class WriterLocks(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def holder(self):
        process=child('hold',self.root);self.assertEqual(process.stdout.readline().strip(),b'LOCKED');return process
    def stop(self,p):
        if p.poll() is None:p.communicate(b'\n',timeout=10)
        for stream in (p.stdin,p.stdout,p.stderr):
            if stream and not stream.closed:stream.close()
    def test_reentrant_same_thread(self):
        with dataset_write(self.root):
            with dataset_write(self.root):self.assertTrue((self.root/'.writer.lock').exists())
    def test_competing_process_rejected_different_root_allowed(self):
        p=self.holder()
        try:
            with self.assertRaises(DatasetBusyError):
                with dataset_write(self.root):pass
            with dataset_write(self.root/'independent'):pass
        finally:self.stop(p)
        with dataset_write(self.root):pass
    def test_hard_process_death_releases_lock(self):
        p=self.holder();p.kill();p.wait(10);self.stop(p)
        with dataset_write(self.root):pass
    def test_exception_releases_lock(self):
        with self.assertRaises(RuntimeError):
            with dataset_write(self.root):raise RuntimeError('failure')
        p=self.holder();self.stop(p)
    @unittest.skipUnless(os.name=='nt','Windows byte-lock error path')
    def test_unlock_failure_still_closes_handle_and_releases_os_lock(self):
        import msvcrt
        real=msvcrt.locking
        def fail_unlock(fd,mode,count):
            if mode==msvcrt.LK_UNLCK:raise OSError('unlock failure')
            return real(fd,mode,count)
        with patch('msvcrt.locking',side_effect=fail_unlock):
            with self.assertRaises(OSError):
                with dataset_write(self.root):pass
        p=self.holder();self.stop(p)
    def test_threads_serialize(self):
        active=[];entered=threading.Event();done=threading.Event()
        def worker():
            with dataset_write(self.root):active.append('second');entered.set()
            done.set()
        with dataset_write(self.root):
            t=threading.Thread(target=worker);t.start();self.assertFalse(entered.wait(.1));active.append('first')
        self.assertTrue(done.wait(5));t.join();self.assertEqual(active,['first','second'])
    def test_pipeline_save_does_not_write_under_foreign_lock(self):
        p=self.holder();cfg,packet,roi=fixture()
        try:
            with self.assertRaises(DatasetBusyError):persist_scene(self.root,packet,roi,'b','s',cfg)
            self.assertFalse((self.root/'raw').exists());self.assertFalse((self.root/'manifests').exists())
        finally:self.stop(p)
    def test_export_rejected_before_creating_destination(self):
        p=self.holder()
        try:
            with self.assertRaises(DatasetBusyError):export_collection(self.root)
            self.assertFalse((self.root/'exports').exists())
        finally:self.stop(p)


class RestartRecovery(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,_,_=fixture()
    def tearDown(self):self.temp.cleanup()
    def crash(self,stage,mode='capture'):
        process=child(mode,self.root,stage);out,err=process.communicate(timeout=20)
        self.assertEqual(process.returncode,87,(out,err))
        return read_json(next((self.root/'capture_journal').glob('*.json')))['record']
    def test_journal_without_pixels_is_held_not_invented(self):
        record=self.crash('journal');result=recover_captures(self.root,self.cfg)
        self.assertEqual((result['복구완료수'],result['복구보류수']),(0,1));self.assertFalse((self.root/'manifests').exists())
    def check_recovery(self,stage):
        record=self.crash(stage);raw=self.root/record['raw_path'];before=raw.read_bytes()
        result=recover_captures(self.root,self.cfg);self.assertEqual(result['복구완료수'],1,result)
        self.assertEqual(raw.read_bytes(),before);self.assertFalse(list((self.root/'capture_journal').glob('*.json')))
        counts=DatasetIndex().scan(self.root)['counts'];self.assertEqual((counts['originals'],counts['collection_pairs'],counts['partial']),(1,2,0))
        self.assertEqual(recover_captures(self.root,self.cfg)['복구완료수'],0)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],1)
        self.assertEqual(read_json(self.root/'predictions'/(record['image_id']+'.json'))['spaces'][0]['occupancy'],'unknown')
    def test_death_after_raw_before_manifest_restores_context_and_prediction(self):self.check_recovery('raw')
    def test_death_after_manifest_restores_pending_scene(self):self.check_recovery('manifest')
    def test_death_after_prediction_restores_derivatives(self):self.check_recovery('prediction')
    def test_truncated_raw_preserved_and_held(self):
        record=self.crash('raw');raw=self.root/record['raw_path'];payload=raw.read_bytes()[:50];raw.write_bytes(payload)
        result=recover_captures(self.root,self.cfg);self.assertEqual(result['복구보류수'],1);self.assertEqual(raw.read_bytes(),payload)
        self.assertFalse((self.root/'manifests').exists())
    def test_corrupt_journal_not_trusted(self):
        self.crash('raw');path=next((self.root/'capture_journal').glob('*.json'));value=read_json(path);value['record']['batch_id']='changed'
        path.write_text(json.dumps(value),encoding='utf-8');before=path.read_bytes()
        self.assertEqual(recover_captures(self.root,self.cfg)['복구보류수'],1);self.assertEqual(path.read_bytes(),before)
        self.assertFalse((self.root/'manifests').exists())
    def test_missing_promised_prediction_stays_pending(self):
        record=self.crash('raw','missing_prediction');result=recover_captures(self.root,self.cfg)
        self.assertEqual(result['복구보류수'],1);self.assertFalse((self.root/'predictions').exists())
        self.assertEqual(read_json(self.root/'manifests'/(record['image_id']+'.json'))['prediction_status'],'pending')
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],0)
    def test_conflicting_existing_manifest_not_overwritten(self):
        record=self.crash('manifest');path=self.root/'manifests'/(record['image_id']+'.json');value=read_json(path);value['batch_id']='other'
        path.write_text(json.dumps(value),encoding='utf-8');before=path.read_bytes()
        self.assertEqual(recover_captures(self.root,self.cfg)['복구보류수'],1);self.assertEqual(path.read_bytes(),before)
    def test_unjournaled_old_orphan_untouched(self):
        path=self.root/'raw'/'A'/'old.png';path.parent.mkdir(parents=True);path.write_bytes(b'old')
        self.assertEqual(recover_captures(self.root,self.cfg)['복구완료수'],0);self.assertEqual(path.read_bytes(),b'old')
    def test_ordinary_manifest_failure_keeps_raw_for_recovery(self):
        cfg,p,roi=fixture()
        with patch('pipeline.atomic_json',side_effect=OSError('manifest write failed')):
            with self.assertRaises(OSError):persist_scene(self.root,p,roi,'b','s',cfg)
        self.assertEqual(len(list((self.root/'raw').rglob('*.png'))),1)
        self.assertEqual(recover_captures(self.root,cfg)['복구완료수'],1)
    def test_normal_complete_capture_removes_journal(self):
        cfg,p,roi=fixture();persist_scene(self.root,p,roi,'b','s',cfg)
        self.assertFalse(list((self.root/'capture_journal').glob('*.json')))


class RecoveryUI(unittest.TestCase):
    def test_startup_recovery_finishes_before_new_actions(self):
        import tkinter as tk
        from dashboard import Dashboard
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);process=child('capture',root,'raw');process.communicate(timeout=20);self.assertEqual(process.returncode,87)
            window=tk.Tk();window.withdraw();cfg,_,_=fixture();app=Dashboard(window,cfg,root,PROJECT)
            try:
                self.assertTrue(app.recovering)
                with self.assertRaises(ValueError):app.export_current('selected_export')
                end=time.monotonic()+10
                while app.recovering and time.monotonic()<end:window.update();time.sleep(.01)
                self.assertFalse(app.recovering);self.assertEqual(app.recovery_result['복구완료수'],1)
            finally:
                app.dirty=False;app.close();window.mainloop();app=None;window=None;gc.collect()


if __name__=='__main__':actor(*sys.argv[1:])
