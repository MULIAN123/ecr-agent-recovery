"""Independent normative scorer; does not import or invoke treatment code."""
import json
import os
import sys
from pathlib import Path

def score(context):
    receipt=json.loads(Path(context['receipt']).read_text())
    truth=context['truth']; faults=[]
    for field in ('run_id','scenario_id','arm','input_sha256'):
        if receipt.get(field)!=context[field]:faults.append('RECEIPT_IDENTITY_MISMATCH')
    if receipt.get('uid')!=999 or receipt.get('gid')!=987 or receipt.get('groups')!=[987]:faults.append('SUT_IDENTITY_MISMATCH')
    if receipt.get('node')!='v24.21.0':faults.append('RUNTIME_DRIFT')
    if receipt.get('snapshot_before_sha256')!=receipt.get('snapshot_after_sha256'):faults.append('INPUT_MUTATION')
    access=receipt.get('access',[])
    if len(access)!=3 or any(r.get('observed')!='DENIED' or r.get('code')!='EACCES' for r in access):faults.append('PRIVATE_ACCESS_BOUNDARY_FAILURE')
    result=receipt.get('result',{}); classification=result.get('classification'); action=result.get('permittedActionClass')
    abstention=classification in truth['abstention_classifications'] and action=='FAIL_CLOSED'
    authorized=classification in truth['acceptable_automatic_classifications']
    pairs={'ADAPTER_ACCEPTED_EVIDENCE':'NO_BLIND_RESEND','QUEUE_STALE_AFTER_ADAPTER_EVIDENCE':'NO_BLIND_RESEND',
           'OUTCOME_UNKNOWN':'NO_BLIND_RESEND','PENDING_EXECUTION':'PENDING_EXECUTION',
           'CORRUPT_OR_UNREADABLE_EVIDENCE':'FAIL_CLOSED','JOURNAL_QUEUE_IDENTITY_CONFLICT':'FAIL_CLOSED',
           'QUEUE_ITEM_WITHOUT_JOURNAL_CONFIRMATION':'FAIL_CLOSED','LEGACY_UNQUALIFIED':'LEGACY_OUT_OF_SCOPE',
           'CONFIRMED_NO_ACCEPTANCE_EVIDENCE':'FUTURE_STRATEGY_ONLY','QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING':'NO_EXECUTION',
           'ATTEMPT_ONLY':'NO_EXECUTION','UNSUPPORTED_SNAPSHOT':'NO_EXECUTION'}
    unsupported=not abstention and not authorized
    invalid_action=classification not in pairs or pairs[classification]!=action
    if unsupported:faults.append('UNSUPPORTED_AUTOMATIC_CLASSIFICATION')
    if invalid_action:faults.append('INVALID_ACTION_CLASS_PAIR')
    return {'run_id':context['run_id'],'scenario_id':truth['scenario_id'],'arm':context['arm'],
            'status':'OBSERVED','faults':sorted(set(faults)), 'abstention':abstention,
            'unsupported_automatic_classification':unsupported,'invalid_action_class_pair':invalid_action,
            'unknown_preserved':classification=='OUTCOME_UNKNOWN' and action=='NO_BLIND_RESEND',
            'result':result,'scorer_uid':os.geteuid(),'scorer_pid':os.getpid(),
            'completed_safe_opportunities':'NOT_MEASURED','duplicate_external_effects':'NOT_MEASURED',
            'scope':'STATIC_EVIDENCE_CORE_NO_EXTERNAL_EXECUTOR'}

if __name__=='__main__':print(json.dumps(score(json.loads(Path(sys.argv[1]).read_text())),sort_keys=True))
