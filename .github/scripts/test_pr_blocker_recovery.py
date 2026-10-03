"""Behavioural blocker-recovery tests; all network calls are mocked."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
os.environ.setdefault('GITHUB_REPOSITORY','owner/repo')
os.environ.setdefault('GH_TOKEN','test-token')
os.environ.setdefault('GITHUB_EVENT_NAME','schedule')
os.environ.setdefault('DEFAULT_BRANCH','main')
sys.path.insert(0,str(Path(__file__).parent))
import pr_blocker_recovery as m

class Recovery(unittest.TestCase):
    def setUp(self):
        self.pr={'number':7,'html_url':'https://github.com/owner/repo/pull/7','state':'open','draft':False,'labels':[],
                 'user':{'login':'owner'},'head':{'sha':'a'*40,'ref':'ci/test','repo':{'full_name':'owner/repo'}},
                 'base':{'ref':'main'},'mergeable':True,'mergeable_state':'clean'}
        self.thread={'id':'PRRT_test','isResolved':False,'isOutdated':True,'path':'.github/workflows/security.yml','line':None,
                     'comments':{'nodes':[{'body':'Fix the missing independent validation in the current security workflow.','author':{'login':'chatgpt-codex-connector'}}]}}
        self.config=patch.multiple(m.router,REPO='owner/repo',DEFAULT='main',KILO_IMPLEMENTER='kilo-code-bot[bot]',REPAIR_APP_LOGIN='repair[bot]')
        self.config.start(); self.addCleanup(self.config.stop)

    def receipt(self, author='kilo-code-bot[bot]', sha=None, base=None):
        return {'user':{'login':author},'body':'<!-- pr-blocker-resolution:'+json.dumps({'sha':sha or 'a'*40,'base_sha':base or 'b'*40,
                   'threads':[{'id':'PRRT_test','evidence':'Implemented independent validation at workflow line 35; regression tests passed.'}]})+' -->'}

    def api(self,method,path,payload=None):
        if path.endswith('/commits/main'): return {'sha':'b'*40}
        if path=='/graphql': return {'data':{'resolveReviewThread':{'thread':{'isResolved':True}}}}
        raise AssertionError((method,path))

    def test_conflict_routes_even_without_failed_ci(self):
        self.pr.update(mergeable=False,mergeable_state='dirty')
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api),patch.object(m.router,'dispatch',return_value='requested') as dispatch,patch.object(m,'review_threads') as threads:
            result=m.recover(7)
        self.assertEqual(result['state'],'conflict-requested')
        self.assertEqual(dispatch.call_args.args[1],'merge-conflict-'+'b'*40)
        threads.assert_not_called()

    def test_behind_branch_routes_existing_source_update(self):
        self.pr['mergeable_state']='behind'
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api),patch.object(m.router,'dispatch',return_value='requested') as dispatch:
            self.assertEqual(m.recover(7)['state'],'behind-requested')
        self.assertEqual(dispatch.call_args.args[1],'branch-behind-'+'b'*40)

    def test_exhausted_attempts_are_visible_not_reported_as_requested(self):
        self.pr['mergeable']=False
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api),patch.object(m.router,'dispatch',return_value='attempt-limit'):
            self.assertEqual(m.recover(7)['state'],'conflict-attempt-limit')

    def test_unknown_mergeability_does_not_guess(self):
        self.pr['mergeable']=None
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api),patch.object(m.router,'dispatch') as dispatch:
            self.assertEqual(m.recover(7)['state'],'mergeability-pending')
        dispatch.assert_not_called()

    def test_every_owner_hold_is_respected(self):
        for label in m.HOLD_LABELS:
            self.pr['labels']=[{'name':label}]
            with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api') as api:
                self.assertEqual(m.recover(7)['state'],'excluded')
                api.assert_not_called()

    def test_ineligible_pr_is_excluded(self):
        with patch.object(m.router,'pr_details',return_value=None): self.assertEqual(m.recover(7)['state'],'excluded')

    def test_receipt_rejects_untrusted_author_and_stale_tips(self):
        for receipt in [self.receipt(author='owner'),self.receipt(sha='c'*40),self.receipt(base='c'*40)]:
            self.assertEqual(m.verified_receipts([receipt],'a'*40,'b'*40),{})
        self.assertIn('PRRT_test',m.verified_receipts([self.receipt()],'a'*40,'b'*40))

    def test_receipt_accepts_configured_app_identity(self):
        self.assertIn('PRRT_test',m.verified_receipts([self.receipt(author='repair[bot]')],'a'*40,'b'*40))

    def test_receipt_rejects_missing_implementation_evidence(self):
        receipt=self.receipt(); receipt['body']=receipt['body'].replace('Implemented independent validation at workflow line 35; regression tests passed.','fixed')
        self.assertEqual(m.verified_receipts([receipt],'a'*40,'b'*40),{})

    def test_outdated_thread_is_routed_not_blindly_resolved(self):
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api) as api,patch.object(m,'review_threads',return_value=[self.thread]),patch.object(m.router,'all_pages',return_value=[]),patch.object(m.router,'dispatch') as dispatch:
            self.assertEqual(m.recover(7)['bot_threads_remaining'],1)
        self.assertFalse(any(c.args[1]=='/graphql' for c in api.call_args_list))
        dispatch.assert_called_once()

    def test_verified_receipt_and_required_checks_resolve_thread(self):
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api),patch.object(m,'review_threads',return_value=[self.thread]),patch.object(m.router,'all_pages',return_value=[self.receipt()]),patch.object(m,'required_checks_pass',return_value=True),patch.object(m.router,'dispatch') as dispatch:
            self.assertEqual(m.recover(7)['resolved'],['PRRT_test'])
        dispatch.assert_not_called()

    def test_failed_required_checks_preserve_review(self):
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api) as api,patch.object(m,'review_threads',return_value=[self.thread]),patch.object(m.router,'all_pages',return_value=[self.receipt()]),patch.object(m,'required_checks_pass',return_value=False),patch.object(m.router,'dispatch'):
            self.assertEqual(m.recover(7)['resolved'],[])
        self.assertFalse(any(c.args[1]=='/graphql' for c in api.call_args_list))

    def test_head_movement_before_resolution_defers(self):
        changed=copy.deepcopy(self.pr);changed['head']['sha']='c'*40
        with patch.object(m.router,'pr_details',side_effect=[self.pr,changed]),patch.object(m.router,'api',side_effect=self.api) as api,patch.object(m,'review_threads',return_value=[self.thread]),patch.object(m.router,'all_pages',return_value=[self.receipt()]),patch.object(m,'required_checks_pass',return_value=True):
            self.assertEqual(m.recover(7)['state'],'changed-during-recovery')
        self.assertFalse(any(c.args[1]=='/graphql' for c in api.call_args_list))

    def test_human_review_threads_are_never_resolved_or_routed(self):
        self.thread['comments']['nodes'][0]['author']['login']='human'
        with patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api) as api,patch.object(m,'review_threads',return_value=[self.thread]),patch.object(m.router,'all_pages',return_value=[self.receipt()]),patch.object(m,'required_checks_pass',return_value=True),patch.object(m.router,'dispatch') as dispatch:
            self.assertEqual(m.recover(7)['human_threads_remaining'],1)
        dispatch.assert_not_called()
        self.assertFalse(any(c.args[1]=='/graphql' for c in api.call_args_list))

    def test_graphql_errors_are_not_a_clean_review(self):
        with patch.object(m.router,'api',return_value={'errors':[{}]}):
            with self.assertRaises(RuntimeError):m.review_threads(7)

    def test_no_native_required_checks_never_authorises_resolution(self):
        with patch.object(m.router,'api',return_value=[]):self.assertFalse(m.required_checks_pass(self.pr))

    def test_check_requires_success_from_expected_app(self):
        requirement=[{'type':'required_status_checks','parameters':{'required_status_checks':[{'context':'security','integration_id':1}]}}]
        for conclusion,app,expected in [('success',1,True),('failure',1,False),('skipped',1,False),('success',2,False)]:
            checks={'check_runs':[{'name':'security','app':{'id':app},'status':'completed','conclusion':conclusion}]}
            with patch.object(m.router,'api',side_effect=[requirement,checks,{'statuses':[]}]):
                self.assertEqual(m.required_checks_pass(self.pr),expected)

    def test_manual_pr_selection_and_invalid_number(self):
        self.assertEqual(m.candidate_numbers({'inputs':{'pr_number':'7'}}),[7])
        with self.assertRaises(ValueError):m.candidate_numbers({'inputs':{'pr_number':'7;bad'}})

    def test_one_pr_error_does_not_stop_others_and_fails_visibly(self):
        with tempfile.TemporaryDirectory() as directory:
            event=Path(directory)/'event.json'; event.write_text('{}')
            summary=Path(directory)/'summary.md'
            previous=Path.cwd();os.chdir(directory)
            try:
                with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event),'GITHUB_STEP_SUMMARY':str(summary)}),patch.object(m,'candidate_numbers',return_value=[7,8]),patch.object(m,'recover',side_effect=[RuntimeError('hidden detail'),{'pr':8,'state':'no-conflict-or-review-blocker'}]):
                    with self.assertRaises(SystemExit):m.main()
                report=json.loads(Path('pr-blocker-recovery.json').read_text())
                self.assertEqual(len(report['results']),2)
                self.assertNotIn('hidden detail',summary.read_text())
            finally:os.chdir(previous)

    def test_conflict_dispatch_requests_existing_branch_and_no_force_push(self):
        with patch.object(m.router,'all_pages',return_value=[]),patch.object(m.router,'valid_kilo_webhook_url',return_value=True),patch.object(m.router,'pr_details',return_value=self.pr),patch.object(m.router,'api',side_effect=self.api),patch.object(m.router.urllib.request,'urlopen') as openurl,patch.dict(os.environ,{'KILO_REPAIR_TRIGGER_URL':'https://example.invalid/repair'}):
            openurl.return_value.__enter__.return_value.status=202
            # The acknowledgement write is mocked separately from base reads.
            def api(method,path,payload=None):
                return {'sha':'b'*40} if method=='GET' else None
            with patch.object(m.router,'api',side_effect=api):m.router.dispatch(self.pr,'merge-conflict-'+'b'*40,['conflict'])
            task=json.loads(openurl.call_args.args[0].data)['task']
            self.assertIn('existing source PR branch',task)
            self.assertIn('Never force-push',task)
            self.assertIn('Do not merge pull requests or deploy',task)

    def test_dispatch_head_race_makes_no_webhook_call(self):
        changed=copy.deepcopy(self.pr);changed['head']['sha']='c'*40
        with patch.object(m.router,'all_pages',return_value=[]),patch.object(m.router,'valid_kilo_webhook_url',return_value=True),patch.object(m.router,'pr_details',return_value=changed),patch.object(m.router,'api',side_effect=self.api),patch.object(m.router.urllib.request,'urlopen') as openurl:
            m.router.dispatch(self.pr,'merge-conflict-'+'b'*40,['conflict'])
            openurl.assert_not_called()

    def test_duplicate_exact_head_base_is_not_dispatched(self):
        marker='<!-- kilo-auto-repair:'+'a'*40+':merge-conflict-'+'b'*40+' -->'
        with patch.object(m.router,'all_pages',return_value=[{'user':{'login':'github-actions[bot]'},'body':marker}]),patch.object(m.router.urllib.request,'urlopen') as openurl:
            m.router.dispatch(self.pr,'merge-conflict-'+'b'*40,['conflict'])
            openurl.assert_not_called()

if __name__=='__main__':unittest.main()
