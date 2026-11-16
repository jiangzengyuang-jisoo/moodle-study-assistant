"""Real replacement plus interrupted deletion recovery; synthetic data only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from library import Library
from learning_library import versions
from chapter_finalize import finalize


class ChapterFinalizeTests(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        study=Path(temp.name)/'Study';path=study/'_System/state.json';path.parent.mkdir(parents=True)
        site='https://school.example/moodle';state={'revision':1,'courses':[{'id':'COURSE','moodle_course_ids':[101]}],
            'settings':{'profile_account_scope':{'schema':1,'site':site,'user_id':7},'course_library_authorization':{'schema':1,'scope_version':1,'courses':{'COURSE':{'moodle_courseid':101,'mode':'full','scope_version':1,'account_scope':{'site':site,'user_id':7}}}}},
            'materials':[],'mistakes':[{'id':'keep','answer':'Real original answer'}]}
        for i in range(2):
            source=study/f'COURSE/Lectures/L{i}.txt';source.parent.mkdir(parents=True,exist_ok=True);source.write_text('Old body '+str(i))
            state['materials'].append({'id':f'old-{i}','course_id':'COURSE','category':'Lectures','filename':source.name,'relative_path':source.relative_to(study).as_posix(),'sha256':versions.sha(source),
                'moodle_file_sources':[{'module_id':i+1,'remote_filename':source.name,'filepath':'/','site':site,'user_id':7,'page_id':101}],
                'source_pages':[{'moodle_courseid':101,'module_id':i+1,'page_url':site+'/course/view.php?id=101','page_course_id':'COURSE'}]})
        path.write_text(json.dumps(state))
        for old in state['materials']:
            source=study/'_Inbox'/old['filename'];source.parent.mkdir(exist_ok=True);source.write_text('New readable body '+old['id'])
            new={**deepcopy(old),'id':old['id'].replace('old','new'),'relative_path':source.relative_to(study).as_posix(),'sha256':versions.sha(source)}
            versions.promote(path,new)
        Library(path).sync('COURSE')
        return path,study

    def test_unlink_failure_keeps_pending_and_recovers_only_recorded_successor(self):
        path,study=self.fixture();original=Path.unlink
        def fail_second(target,*args,**kwargs):
            if '_Archive' in target.parts and target.name=='L1.txt':raise PermissionError('simulated locked original')
            return original(target,*args,**kwargs)
        with patch.object(Path,'unlink',fail_second),self.assertRaises(PermissionError):
            finalize(path,{'material_ids':['new-0','new-1'],'previous_ids':['old-0','old-1']})
        state=json.loads(path.read_text());old={m['id']:m for m in state['materials']}
        self.assertEqual(old['old-0']['version_status'],'deleted')
        self.assertEqual(old['old-1']['version_status'],'deletion_pending')
        self.assertNotIn('deleted_at',old['old-1']);self.assertTrue((study/old['old-1']['relative_path']).is_file())
        result=finalize(path,{'material_ids':['new-1'],'previous_ids':[]})
        self.assertEqual(result['deleted_original_ids'],['old-1'])
        self.assertEqual(json.loads(path.read_text())['mistakes'],state['mistakes'])

    def test_shared_original_or_foreign_scope_is_preserved(self):
        for scenario in ('shared','foreign'):
            with self.subTest(scenario=scenario):
                path,study=self.fixture();state=json.loads(path.read_text());old=next(m for m in state['materials'] if m['id']=='old-0')
                if scenario=='shared':state['materials'].append({**old,'id':'alias'})
                else:old['moodle_file_sources'][0]['user_id']=8
                path.write_text(json.dumps(state))
                with self.assertRaises(ValueError):finalize(path,{'material_ids':['new-0'],'previous_ids':['old-0']})
                self.assertTrue((study/old['relative_path']).is_file())
                self.assertEqual(next(m for m in json.loads(path.read_text())['materials'] if m['id']=='old-0')['version_status'],'archived')

if __name__=='__main__':unittest.main()
