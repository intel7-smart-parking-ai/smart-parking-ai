"""Dual-camera contracts, partial writes, recovery and default UI against two simulated SDK sources."""
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Lock
import copy,gc,tempfile,time,unittest
from unittest.mock import Mock,patch
import tkinter as tk
from camera_hub import CameraHub
from collection_output import export_collection,read_collection
from dataset19 import DatasetIndex,export_training,persist_scene
from dual_capture import CHANNELS,Result,PairHold,SharedReader,alignment_root,profile_specs,synchronized,save_pair,recover_pairs,require_complete_pair
from dual_dashboard import DualDashboard
from layouts import snapshot
from marker_setup import enroll
from parking_ai.storage import atomic_json
from pipeline import read_json
from review import rows_for_edit,save_review,export_reviewed
from test_v19 import PROJECT,fixture
from test_v31 import board
from vision19 import VisionEngine

def data(k,lot,now):
    cfg,p,roi=board(lot,count=6);p=replace(p,camera_id=k,serial='sim-'+k,source_kind='camera',received_monotonic=now)
    cfg.update(capture_source={'kind':'camera'},enable_ocr=False);roi=snapshot(lot,k,p.serial,p.image.shape[1::-1],'fixed-v1__view-ceiling',roi['spaces'])
    return Result(p,roi,VisionEngine().run(p,roi,cfg,None,'manual'),cfg)
def profile():
    cfg=read_json(PROJECT/'dual_cameras.json');cfg=copy.deepcopy(cfg)
    for k,c in cfg['channels'].items():c['serial']='sim-'+k
    return cfg

class PairTiming(unittest.TestCase):
    def setUp(self):
        _,p,_=fixture();self.cfg=profile();self.specs={s.camera_id:s for s in profile_specs(self.cfg)}
        self.frames={k:replace(p,camera_id=k,serial='sim-'+k,received_monotonic=100. if k=='A' else 100.1) for k in CHANNELS}
    def test_exact_limit_and_one_nanosecond_over(self):
        self.assertEqual(synchronized(self.frames,self.specs,100,now=100.2)[1],100.)
        self.frames['B']=replace(self.frames['B'],received_monotonic=100.100000001)
        with self.assertRaises(PairHold):synchronized(self.frames,self.specs,100,now=100.2)
    def test_either_channel_missing_stale_wrong_identity_or_reused_holds(self):
        keys,_=synchronized(self.frames,self.specs,100,now=100.2)
        for k in CHANNELS:
            p=self.frames[k]
            for bad in (None,replace(p,received_monotonic=98.),replace(p,serial='other'),replace(p,source_kind='video')):
                with self.subTest(k=k,packet=bad is None),self.assertRaises(PairHold):synchronized({**self.frames,k:bad},self.specs,100,now=100.2)
            with self.assertRaises(PairHold):synchronized(self.frames,self.specs,100,{k:keys[k]},now=100.2)
    def test_device_assignment_preserves_known_camera_even_if_discovery_order_reversed(self):
        self.cfg['channels']['B']['serial']='';devices=[{'serial':'new'},{'serial':'sim-A'}]
        self.assertEqual([s.serial for s in profile_specs(self.cfg,devices)],['sim-A','new'])
        with self.assertRaises(PairHold):profile_specs(self.cfg,devices[:1])
        with self.assertRaises(PairHold):profile_specs(self.cfg,[{'serial':'new'},{'serial':'another'}])
        with self.assertRaises(PairHold):profile_specs(self.cfg,[*devices,{'serial':'third'}])
    def test_batch_start_cannot_use_a_frame_from_before_start(self):
        with self.assertRaisesRegex(PairHold,'시작 이후'):synchronized(self.frames,self.specs,100,now=100.2,not_before=100.)
    def test_duplicate_assignment_rejected_but_same_lot_allowed(self):
        self.cfg['channels']['B']['lot']='A';self.assertEqual(len(profile_specs(self.cfg)),2)
        self.cfg['channels']['B']['serial']='sim-A'
        with self.assertRaisesRegex(ValueError,'시리얼'):profile_specs(self.cfg)
    def test_one_reader_for_two_concurrent_channels(self):
        shared=SharedReader(PROJECT,{});real=Mock();real.readtext.return_value=[];bar=Barrier(2)
        def read():bar.wait(5);return shared.readtext(None)
        with patch('analysis.make_reader',return_value=(real,{})) as loader,ThreadPoolExecutor(2) as pool:
            jobs=[pool.submit(read) for _ in CHANNELS];self.assertEqual([f.result() for f in jobs],[[],[]])
        loader.assert_called_once();self.assertEqual(real.readtext.call_count,2)

class PairedDisk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        now=time.monotonic();cls.results={k:data(k,k,now+i*.024) for i,k in enumerate(CHANNELS)}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def save(self):return save_pair(self.root,self.results,{'A':'ba','B':'bb'},'session',100)
    def records(self):return [read_json(p) for p in sorted((self.root/'manifests').glob('*.json'))]
    def test_two_sources_to_28_named_pairs_survive_portable_export(self):
        info=self.save();self.assertEqual(info['skew_ms'],24)
        target,report=export_collection(self.root,paired_only=True);self.assertEqual(report['counts'],{'measurements':1,'scenes':2,'originals':2,'images':32,'json_files':30,'space_pairs':28})
        target=Path(target);labels=[read_json(p) for p in (target/'collected').rglob('*.json')]
        self.assertEqual(len(labels),28)
        for r in labels:
            k=r['카메라ID'];self.assertEqual(r['동시측정ID'],info['id']);self.assertEqual(r['카메라시리얼'],'sim-'+k)
            self.assertEqual(r['두프레임_수신차_ms'],24);self.assertEqual(r['주차장'],k)
            self.assertTrue((target/r['원본사진파일']).exists());self.assertTrue((target/r['표시사진파일']).exists())
        self.assertEqual(recover_pairs(self.root,{})['bay_pairs'],{'A':12,'B':16})
    def test_partial_second_write_never_exports_single_member_as_pair_or_training(self):
        from pipeline import save_capture
        def fail_b(root,p,*args,**kw):
            if p.camera_id=='B':raise OSError('test disk interruption')
            return save_capture(root,p,*args,**kw)
        with patch('pipeline.save_capture',side_effect=fail_b),self.assertRaises(OSError):self.save()
        r,=self.records();raw=(self.root/r['raw_path']).read_bytes();receipt=next((self.root/'dual_measurements').glob('*.json'))
        self.assertEqual(read_json(receipt)['status'],'partial');self.assertEqual(recover_pairs(self.root,{})['held'],1)
        rows=rows_for_edit(r);rows[0].update(occupancy='vacant',review_status='approved');save_review(self.root,r,rows,'tester')
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['scenes'],0)
        self.assertEqual(export_training(self.root)[1],0);self.assertEqual(export_reviewed(self.root)[1],0)
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['partial'],1);self.assertEqual((self.root/r['raw_path']).read_bytes(),raw)
    def test_interrupt_before_final_receipt_recovers_only_two_verified_members(self):
        import dual_capture
        original=dual_capture.atomic_json
        def cut(path,value):
            if value.get('status')=='complete':raise SystemExit('test interrupted before commit')
            return original(path,value)
        with patch.object(dual_capture,'atomic_json',side_effect=cut),self.assertRaises(SystemExit):self.save()
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['measurements'],0)
        r=recover_pairs(self.root,{});self.assertEqual((r['complete'],r['held']),(1,0))
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['measurements'],1)
    def test_peer_raw_missing_blocks_both_members(self):
        self.save();r1,r2=self.records();(self.root/r2['raw_path']).unlink()
        with self.assertRaisesRegex(ValueError,'원본 누락'):require_complete_pair(self.root,r1)
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['scenes'],0)
    def test_complete_pair_export_cannot_publish_single_corrupt_side(self):
        self.save();r1,r2=self.records();bundle=read_collection(self.root,r2)
        item=next(i for i in bundle['items'] if i['kind']=='korean_label');(self.root/item['path']).write_text('{}',encoding='utf-8')
        target,report=export_collection(self.root,paired_only=True)
        self.assertEqual(report['counts']['measurements'],0);self.assertEqual(report['counts']['originals'],0);self.assertEqual(len(report['skipped']),2)
        self.assertFalse(any(Path(target).rglob('*.png')))
    def test_pre_v34_standalone_scene_still_exports_but_not_as_dual(self):
        a=self.results['A'];persist_scene(self.root,a.packet,a.roi,'batch','session',a.cfg,a.prediction)
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],1)
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['scenes'],0)
    def test_same_lot_two_views_keep_one_batch_and_separate_physical_anchors(self):
        from dual_capture import ChannelAnalyzer
        a=self.results['A'];b=data('B','A',a.packet.received_monotonic+.02)
        for k,r in [('A',a),('B',b)]:enroll(alignment_root(self.root,k),r.packet,r.roi)
        reader=Mock();reader.readtext.return_value=[]
        results={k:ChannelAnalyzer(reader).analyze(self.root,r.packet,{'lot':'A','view':v},r.cfg)
                 for k,r,v in [('A',a,'ceiling'),('B',b,'right')]}
        info=save_pair(self.root,results,dict.fromkeys(CHANNELS,'one-arrangement'),'s',100)
        for k,m in info['members'].items():
            self.assertEqual(m['lot'],'A');self.assertEqual(m['batch'],'one-arrangement');self.assertEqual(m['serial'],'sim-'+k)
        self.assertEqual({m['view'] for m in info['members'].values()},{'ceiling','right'})
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['space_pairs'],24)
    def test_dual_record_cannot_be_resaved_as_unpaired(self):
        self.save();r=self.records()[0];a=self.results[r['camera_id']]
        with self.assertRaisesRegex(ValueError,'동시측정 연결'):
            persist_scene(self.root,a.packet,a.roi,r['batch_id'],'session',a.cfg,a.prediction,r['image_id'])
        require_complete_pair(self.root,r)
    def test_one_held_peer_excludes_both_from_quality_only_then_both_pass(self):
        from test_v27 import full_fixture
        now=time.monotonic();results={}
        for i,k in enumerate(CHANNELS):
            cfg,p,roi=full_fixture(k);cfg['capture_view']='ceiling'
            p=replace(p,camera_id=k,serial='sim-'+k,source_kind='camera',received_monotonic=now+i*.01)
            roi=snapshot(k,k,p.serial,p.image.shape[1::-1],'fixed-v1__view-ceiling',roi['spaces'])
            results[k]=Result(p,roi,VisionEngine().run(p,roi,{**cfg,'enable_ocr':False},None,'manual'),cfg)
        save_pair(self.root,results,dict.fromkeys(CHANNELS,'one'),'session',100)
        for i,r in enumerate(self.records()):
            rows=rows_for_edit(r)
            for row in rows:row.update(occupancy='vacant',review_status='approved')
            save_review(self.root,r,rows,'tester')
            _,report=export_collection(self.root,selected_only=True,paired_only=True)
            self.assertEqual(report['counts']['measurements'],i);self.assertEqual(report['counts']['space_pairs'],28*i)

class TwoCameraConsole(unittest.TestCase):
    def test_default_ui_one_start_two_simultaneous_sources_then_export(self):
        # Real CameraHub threads and OpenCV marker detection. Only SDK sources and OCR are simulated.
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);root=project/'dataset';cfg,_,_=fixture();cfg.update(warmup_seconds=0,save_interval=999)
            spec=profile();spec['channels']['B']['serial']='';atomic_json(project/'dual_cameras.json',spec)
            images={}
            for k in CHANNELS:
                r=data(k,k,time.monotonic());images[k]=r.packet.image;enroll(alignment_root(root,k),r.packet,r.roi)
            opened=set();released=set();lock=Lock();barrier=Barrier(2)
            class Source:
                def __init__(self,s):self.k=s.camera_id
                def open(self):
                    with lock:opened.add(self.k)
                    barrier.wait(5)
                def read(self):time.sleep(.03);return images[self.k]
                def close(self):
                    with lock:released.add(self.k)
            def factory(specs):return CameraHub(specs,source_factory=Source,retry_seconds=.05)
            w=tk.Tk();w.withdraw()
            with patch('dual_dashboard.find_realsense_devices',return_value=[{'serial':'sim-B'},{'serial':'sim-A'}]),patch('dual_dashboard.CameraHub',side_effect=factory),patch.object(SharedReader,'readtext',return_value=[]):
                app=DualDashboard(w,cfg,root,project)
                def wait(pred,timeout=24):
                    end=time.monotonic()+timeout
                    while not pred() and time.monotonic()<end:w.update();time.sleep(.01)
                    self.assertTrue(pred(),app.note.get())
                try:
                    wait(lambda:not app.busy());app.start();wait(lambda:app.run_count==1)
                    self.assertEqual(opened,set(CHANNELS));self.assertEqual(app.bays,{'A':12,'B':16})
                    self.assertEqual([len(app.tables[k].get_children()) for k in CHANNELS],[12,16])
                    self.assertEqual(read_json(project/'dual_cameras.json')['channels']['B']['serial'],'sim-B')
                    app.stop()
                    self.assertTrue(app.selected_only.get())
                    app.export_pairs();wait(lambda:not app.busy());self.assertIn('0칸 짝',app.note.get())
                    app.selected_only.set(False)
                    app.export_pairs();wait(lambda:not app.busy());self.assertIn('28칸 짝',app.note.get())
                    with patch('dual_dashboard.messagebox.askyesno',return_value=False):
                        app.register_empty();wait(lambda:not app.busy())
                    self.assertFalse(app.empty_references)
                    self.assertFalse((root/'references').exists())
                    with patch('dual_dashboard.messagebox.askyesno',return_value=True):
                        app.register_empty();wait(lambda:not app.busy())
                    self.assertEqual(set(app.empty_references),set(CHANNELS))
                    app.start();wait(lambda:app.run_count==2);app.stop()
                    for k in CHANNELS:
                        pred=app.last_display[k].prediction
                        self.assertEqual(pred['mode'],'reference')
                        self.assertTrue(all(r['occupancy']=='vacant' for r in pred['spaces']))
                finally:
                    app.close();w.mainloop();del app,w;gc.collect()
            self.assertEqual(released,set(CHANNELS))
