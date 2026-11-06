from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import test_v34
import test_v46
import test_v48

from layouts import TYPE_NAMES
from pipeline import read_json


class DesignatedBays(unittest.TestCase):
    special={
        ('A','A-05'):'전기차',('A','A-06'):'전기차',('A','B-06'):'장애인',
        ('B','A-07'):'전기차',('B','A-08'):'전기차',('B','B-07'):'장애인',('B','B-08'):'장애인',
    }

    def test_both_monitor_tables_use_selected_lot_even_on_opposite_camera(self):
        from dual_dashboard import DualDashboard
        app=Mock()
        app.camera_frames={'A':Mock(),'B':Mock()}
        app.tables={'A':Mock(),'B':Mock()}
        for channel,lot,count in [('A','B',16),('B','A',12)]:
            app.tables[channel].get_children.return_value=()
            DualDashboard.fill_rows(app,channel,[],lot)
            rows=[c.kwargs['values'] for c in app.tables[channel].insert.call_args_list]
            self.assertEqual(len(rows),count)
            for sid,zone,*_ in rows:self.assertEqual(zone,self.special.get((lot,sid),'일반'))

    def test_saved_labels_field_form_and_approved_training_export_all_28_bays(self):
        from dual_capture import save_pair,read_complete_measurement
        from field_check import form_for,inspect_training_folder
        from review import rows_for_edit,save_review
        from dataset19 import export_training
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            results={'A':test_v34.data('A','B',35.),'B':test_v34.data('B','A',35.01)}
            info=save_pair(root,results,dict(A='same-arrangement',B='same-arrangement'),'qa',100)
            members=read_complete_measurement(root,info['id'])
            for channel,(record,bundle) in members.items():
                for item in bundle['items']:
                    if item['kind']=='korean_label':
                        label=read_json(root/item['path'])
                        self.assertEqual(label['주차칸종류'],self.special.get((record['lot_id'],item['space_id']),'일반'))
                rows=rows_for_edit(record)
                for row in rows:row.update(occupancy='vacant',review_status='approved')
                save_review(root,record,rows,'tester')
            form=form_for(info['id'],members)
            folder,n=export_training(root,paired_only=True)
            self.assertEqual(n,28)
            manifest,_=inspect_training_folder(folder)
            self.assertTrue(manifest['주차칸종류포함'])
            for row in form['주차칸']:
                self.assertEqual(row['주차칸종류'],self.special.get((row['주차장'],row['칸']),'일반'))
            for row in manifest['samples']:
                self.assertEqual(row['주차칸종류'],self.special.get((row['lot_id'],row['space_id']),'일반'))

    def test_guide_uses_same_map_and_keeps_physical_position_unverified(self):
        from sticker_guide import virtual_roi
        from dual_capture import label_fields
        for channel,lot in [('A','B'),('B','A')]:
            result=test_v34.data(channel,lot,35.)
            roi=virtual_roi(result.packet,lot,'fixed-v1__view-ceiling')
            self.assertIs(label_fields({'roi':roi,'analysis_config':{}})['실물칸확인'],False)
            for slot in roi['spaces']:
                self.assertEqual(TYPE_NAMES[slot['space_type']],self.special.get((lot,slot['space_id']),'일반'))

    def test_old_form_infers_zone_and_edited_form_cannot_move_it(self):
        case=test_v46.FieldMetrics()
        case.setUp()
        row=next(r for r in case.form['주차칸'] if r['칸']=='A-05')
        del row['주차칸종류']
        report=case.compare()
        self.assertEqual(next(r for r in report['칸별결과'] if r['칸']=='A-05')['주차칸종류'],'전기차')
        row['주차칸종류']='일반'
        with self.assertRaisesRegex(ValueError,'도면'):case.compare()

    def test_wrong_and_missing_zone_on_new_training_manifest_are_rejected(self):
        from field_check import inspect_training_folder
        fixture=test_v48.DurableEvidence()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        folder=fixture.delivery()
        for mut in (lambda doc,row:row.update(주차칸종류='다른 구역'),lambda doc,row:row.pop('주차칸종류')):
            fixture.rewrite_manifest(folder,mut)
            with self.assertRaisesRegex(ValueError,'도면'):inspect_training_folder(folder)
