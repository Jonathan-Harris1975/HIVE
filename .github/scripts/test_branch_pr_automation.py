"""Behavioural safety tests; no GitHub credentials or network access required."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('GH_TOKEN', 'test-token')
os.environ.setdefault('GITHUB_REPOSITORY', 'owner/repo')
spec = importlib.util.spec_from_file_location('branch_controller', Path(__file__).with_name('branch_pr_automation.py'))
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class BranchSafety(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GITHUB_EVENT_NAME':'workflow_run'})
        self.env.start()
        self.config = patch.multiple(m, REPO='owner/repo', DEFAULT_BRANCH='main', REPAIR_APP_LOGIN='repair[bot]', REQUIRED_WORKFLOWS=['CI','Security'], REQUIRED_CHECKS=['ci-gate','security'])
        self.config.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.env.stop)
        self.pr = {'number':7,'node_id':'PR_7','state':'open','draft':False,'auto_merge':None,'user':{'login':'repair[bot]'},'head':{'ref':'fix/example','sha':'a'*40,'repo':{'full_name':'owner/repo'}},'base':{'ref':'main','sha':'b'*40},'labels':[{'name':m.MANAGED_LABEL}],'mergeable':True,'mergeable_state':'clean'}

    def test_branch_namespaces(self):
        for branch in ['fix/x','feat/x','chore/x','ci/x','work/x','codex/x/y']:
            self.assertTrue(m.allowed_branch(branch),branch)
        for branch in ['main','autonomy/repair-1','renovate/x','dependabot/x','mergify/merge-queue/x','tmp/x','internal/x','refs/tags/v1','fix/','fix/a..b','fix/a//b','fix/x;echo']:
            self.assertFalse(m.allowed_branch(branch),branch)

    def test_duplicate_matches_repository_source_and_target(self):
        fork = copy.deepcopy(self.pr); fork['head']['repo']['full_name']='fork/repo'
        other = copy.deepcopy(self.pr); other['base']['ref']='release'
        self.assertIsNone(m.exact_open_pr([fork,other],'fix/example'))
        self.assertEqual(m.exact_open_pr([fork,other,self.pr],'fix/example')['number'],7)
        with self.assertRaises(RuntimeError):m.exact_open_pr([self.pr,self.pr],'fix/example')

    def test_existing_human_pr_is_reused_without_adoption(self):
        existing=copy.deepcopy(self.pr);existing['user']['login']='owner'
        with patch.object(m,'branch_signal_candidate',return_value=('fix/example','a'*40)),patch.object(m,'list_open_prs',return_value=[existing]),patch.object(m,'post') as post:
            m.create_or_reuse_pr();post.assert_not_called()

    def test_bot_label_failure_recovers_on_retry(self):
        with patch.object(m,'branch_signal_candidate',return_value=('fix/example','a'*40)),patch.object(m,'list_open_prs',return_value=[self.pr]),patch.object(m,'add_label') as label:
            m.create_or_reuse_pr();label.assert_called_once_with(7)

    def test_concurrent_pr_creation_is_reused(self):
        with patch.object(m,'branch_signal_candidate',return_value=('fix/example','a'*40)),patch.object(m,'list_open_prs',side_effect=[[],[self.pr]]),patch.object(m,'pr_metadata',return_value=('title','body')),patch.object(m,'post',side_effect=m.ApiError(422,'already exists')),patch.object(m,'add_label') as label:
            m.create_or_reuse_pr();label.assert_called_once_with(7)

    def test_unrelated_validation_failure_is_not_swallowed(self):
        with patch.object(m,'branch_signal_candidate',return_value=('fix/example','a'*40)),patch.object(m,'list_open_prs',return_value=[]),patch.object(m,'pr_metadata',return_value=('title','body')),patch.object(m,'post',side_effect=m.ApiError(422,'invalid')):
            with self.assertRaises(m.ApiError):m.create_or_reuse_pr()

    def test_latest_failed_rerun_blocks_old_success(self):
        runs=[{'name':'CI','id':2,'status':'completed','conclusion':'failure'},{'name':'CI','id':1,'status':'completed','conclusion':'success'},{'name':'Security','id':3,'status':'completed','conclusion':'success'}]
        with patch.object(m,'get',return_value={'workflow_runs':runs}):self.assertFalse(m.required_checks_green(self.pr)[0])

    def test_missing_or_pending_workflow_blocks(self):
        for runs in [{},{'CI':{'status':'in_progress','conclusion':None}}]:
            with patch.object(m,'latest_pull_request_runs',return_value=runs):self.assertFalse(m.required_checks_green(self.pr)[0])

    def test_workflow_runs_are_paginated(self):
        first=[{'id':i,'name':f'other-{i}'} for i in range(100)]
        with patch.object(m,'get',side_effect=[{'workflow_runs':first},{'workflow_runs':[{'id':101,'name':'Security'}]}]) as get:
            self.assertIn('Security',m.latest_pull_request_runs('a'*40));self.assertEqual(get.call_count,2)

    def policy(self, settings=None, protected=True, rules=None, legacy=None):
        defaults={'allow_auto_merge':True,'allow_squash_merge':True}
        if settings is not None:defaults.update(settings)
        if rules is None:rules=[{'type':'required_status_checks','parameters':{'required_status_checks':[{'context':'ci-gate'},{'context':'security'}],'strict_required_status_checks_policy':True}}]
        def get(path):
            if path=='/repos/owner/repo':return defaults
            if '/rules/branches/' in path:return rules
            if '/branches/' in path:return {'protected':protected}
            self.fail(path)
        data={'repository':{'ref':{'branchProtectionRule':legacy}}}
        with patch.object(m,'get',side_effect=get),patch.object(m,'graphql',return_value=data):return m.native_merge_policy()

    def test_unprotected_branch_withholds_merge(self):self.assertIsNone(self.policy(protected=False)[0])
    def test_disabled_auto_merge_withholds_merge(self):self.assertIsNone(self.policy(settings={'allow_auto_merge':False})[0])
    def test_absent_enforced_checks_withholds_merge(self):self.assertIsNone(self.policy(rules=[])[0])
    def test_partial_enforced_checks_withholds_merge(self):self.assertIsNone(self.policy(rules=[{'type':'required_status_checks','parameters':{'required_status_checks':[{'context':'ci-gate'}],'strict_required_status_checks_policy':True}}])[0])
    def test_non_strict_policy_withholds_merge(self):self.assertIsNone(self.policy(rules=[{'type':'required_status_checks','parameters':{'required_status_checks':[{'context':'ci-gate'},{'context':'security'}]}}])[0])
    def test_active_ruleset_accepts_complete_strict_gates(self):self.assertEqual(self.policy()[0],'SQUASH')
    def test_legacy_protection_is_supported(self):self.assertEqual(self.policy(rules=[],legacy={'requiresStatusChecks':True,'requiredStatusCheckContexts':['ci-gate','security'],'requiresStrictStatusChecks':True})[0],'SQUASH')
    def test_supported_merge_method_is_selected(self):self.assertEqual(self.policy(settings={'allow_squash_merge':False,'allow_merge_commit':True})[0],'MERGE')
    def test_missing_merge_method_withholds_merge(self):self.assertIsNone(self.policy(settings={'allow_squash_merge':False})[0])
    def test_native_merge_queue_is_not_bypassed(self):self.assertIsNone(self.policy(rules=[{'type':'merge_queue'}])[0])

    def test_unreadable_policy_never_calls_enable_mutation(self):
        with patch.object(m,'get',side_effect=m.ApiError(403,'denied')),patch.object(m,'graphql') as gql:
            with self.assertRaises(m.ApiError):m.enable_native_auto_merge(self.pr)
            gql.assert_not_called()

    def test_only_native_auto_merge_is_requested(self):
        with patch.object(m,'native_merge_policy',return_value=('SQUASH','ok')),patch.object(m,'graphql') as gql,patch.object(m,'put') as put:
            m.enable_native_auto_merge(self.pr)
            self.assertIn('enablePullRequestAutoMerge',gql.call_args.args[0]);put.assert_not_called()

    def test_existing_auto_merge_is_idempotent(self):
        self.pr['auto_merge']={'enabled_by':{}}
        with patch.object(m,'native_merge_policy',return_value=('SQUASH','ok')),patch.object(m,'graphql') as gql:
            m.enable_native_auto_merge(self.pr);gql.assert_not_called()

    def test_all_holds_and_drafts_withhold_auto_merge(self):
        for label in m.BLOCKING_LABELS:
            p=copy.deepcopy(self.pr);p['labels'].append({'name':label.upper()});self.assertFalse(m.managed_pr(p))
        p=copy.deepcopy(self.pr);p['draft']=True;self.assertFalse(m.managed_pr(p))

    def test_hold_revokes_previously_armed_request(self):
        self.pr['auto_merge']={'enabled_by':{}};self.pr['labels'].append({'name':'hold'})
        with patch.object(m,'list_open_prs',return_value=[self.pr]),patch.object(m,'refresh_pr',return_value=self.pr),patch.object(m,'graphql') as gql:
            m.reconcile_managed_prs();self.assertIn('disablePullRequestAutoMerge',gql.call_args.args[0])

    def test_head_change_defers_merge(self):
        moved=copy.deepcopy(self.pr);moved['head']['sha']='c'*40
        with patch.object(m,'list_open_prs',return_value=[self.pr]),patch.object(m,'refresh_pr',side_effect=[self.pr,moved]),patch.object(m,'required_checks_green',return_value=(True,'ok')),patch.object(m,'enable_native_auto_merge') as merge:
            m.reconcile_managed_prs();merge.assert_not_called()

    def test_conflict_or_unknown_mergeability_defers(self):
        for value in [None,False]:
            self.pr['mergeable']=value
            with patch.object(m,'list_open_prs',return_value=[self.pr]),patch.object(m,'refresh_pr',return_value=self.pr),patch.object(m,'enable_native_auto_merge') as merge:
                m.reconcile_managed_prs();merge.assert_not_called()

    def test_latest_base_change_defers_merge(self):
        with patch.object(m,'list_open_prs',return_value=[self.pr]),patch.object(m,'refresh_pr',return_value=self.pr),patch.object(m,'required_checks_green',return_value=(True,'ok')),patch.object(m,'get',return_value={'commit':{'sha':'c'*40}}),patch.object(m,'enable_native_auto_merge') as merge:
            m.reconcile_managed_prs();merge.assert_not_called()

    def test_recovery_does_not_recreate_closed_pr(self):
        os.environ['GITHUB_EVENT_NAME']='schedule'
        with patch.object(m,'get',side_effect=[[{'name':'fix/example','commit':{'sha':'a'*40}}],[self.pr]]),patch.object(m,'list_open_prs',return_value=[]),patch.object(m,'create_or_reuse_pr') as create:
            m.recover_branch_signals();create.assert_not_called()

    def test_recovery_recovers_eligible_missing_signal(self):
        os.environ['GITHUB_EVENT_NAME']='schedule'
        with patch.object(m,'get',side_effect=[[{'name':'fix/example','commit':{'sha':'a'*40}}],[],{'ahead_by':1}]),patch.object(m,'list_open_prs',return_value=[]),patch.object(m,'create_or_reuse_pr') as create:
            m.recover_branch_signals();create.assert_called_once_with(('fix/example','a'*40))


if __name__=='__main__':unittest.main()
