"""Dedicated-VM core bridge qualification and separate formal run, no overwrites."""
import argparse
import copy
import hashlib
import json
import os
import platform
import pwd
import subprocess
import sys
import time
from pathlib import Path

HERE=Path(__file__).resolve().parent
BASE=Path('/var/lib/ecr-followup-r1')
NODE=Path('/opt/ecr-followup-r1-runtime/node-v24.21.0/bin/node')
IDENTITIES={'ecr-sut':(999,987,[987]),'ecr-scorer':(995,985,[985,988])}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def directory(path,uid,gid,mode):
    if path.exists() or path.is_symlink():raise FileExistsError(path)
    path.mkdir();os.chown(path,uid,gid);os.chmod(path,mode)
def write(path,value,gid=985):
    with path.open('xb') as f:f.write(encoded(value));f.flush();os.fsync(f.fileno())
    os.chown(path,0,gid);os.chmod(path,0o640)

def guard(manifest):
    if os.geteuid()!=0 or platform.system()!='Linux':raise RuntimeError('sudo in dedicated Ubuntu required')
    if Path('/sys/class/dmi/id/product_uuid').read_text().strip().lower() not in manifest['vm_uuids']:raise RuntimeError('VM mismatch')
    if platform.release()!='6.8.0-146-generic' or sys.version_info[:2]!=(3,12):raise RuntimeError('Guest profile drift')
    if subprocess.check_output(['findmnt','-n','-o','FSTYPE','-T',str(BASE)],text=True).strip()!='ext4':raise RuntimeError('Native ext4 required')
    if sha(NODE)!=manifest['node_binary_sha256'] or sha(BASE/'evidence/node-runtime-v24.21.0.json')!=manifest['node_receipt_sha256']:raise RuntimeError('Node identity drift')
    owner=int(os.environ.get('SUDO_UID','0'))
    if not owner or HERE.stat().st_uid!=owner or HERE.stat().st_mode&0o7777!=0o700:raise RuntimeError('Private source directory must be personal admin owned0700')
    for path in (HERE,*HERE.parents):
        if path.is_symlink() or path.stat().st_mode&0o022:raise RuntimeError('Unsafe source ancestor')
    for name,(uid,gid,groups) in IDENTITIES.items():
        user=pwd.getpwnam(name)
        if (user.pw_uid,user.pw_gid,sorted(os.getgrouplist(name,user.pw_gid)))!=(uid,gid,groups):raise RuntimeError('Identity drift')
    for rel,expected in manifest['files'].items():
        path=HERE/rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts or path.is_symlink() or sha(path)!=expected:raise RuntimeError('Source/input drift '+rel)
    if sha(BASE/'qualification/q-platform-v2-20261011-r2/batch-summary.json')!=manifest['q_summary_sha256']:raise RuntimeError('Q prerequisite identity drift')
    for path,uid,gid,mode in [(BASE,0,0,0o755),(BASE/'evidence',0,985,0o750),(BASE/'sut-state',999,987,0o750)]:
        info=path.lstat()
        if path.is_symlink() or (info.st_uid,info.st_gid,info.st_mode&0o7777)!=(uid,gid,mode):raise RuntimeError('Directory permission drift')

def child(command,role,cwd,log):
    uid,gid,groups=IDENTITIES[role]
    with log.with_suffix('.stdout').open('xb') as out,log.with_suffix('.stderr').open('xb') as err:
        process=subprocess.Popen(command,user=uid,group=gid,extra_groups=groups,cwd=cwd,
                                 env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},stdout=out,stderr=err,start_new_session=True)
    write(log.with_suffix('.started.json'),{'pid':process.pid,'uid':uid,'utc_ns':time.time_ns()})
    try:code=process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:code=process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            write(log.with_suffix('.process.json'),{'pid':process.pid,'status':'RESIDUE_TIMEOUT'})
            raise RuntimeError('Worker residue PID '+str(process.pid))
        write(log.with_suffix('.process.json'),{'pid':process.pid,'status':'TIMEOUT','exit_status':code})
        raise RuntimeError('Worker timeout')
    write(log.with_suffix('.process.json'),{'pid':process.pid,'uid':uid,'exit_status':code})
    if code!=0:raise RuntimeError(log.with_suffix('.stderr').read_text())
    return read(log.with_suffix('.stdout')),process.pid

def prepare(manifest,batchid):
    public_parent=Path('/opt/ecr-followup-r1-core')
    if not public_parent.exists():directory(public_parent,0,0,0o755)
    info=public_parent.lstat()
    if public_parent.is_symlink() or (info.st_uid,info.st_gid,info.st_mode&0o7777)!=(0,0,0o755):raise RuntimeError('Unsafe public parent')
    public=public_parent/batchid;directory(public,0,0,0o755)
    for name in ('sut.mjs','scorer.py'):
        (public/name).write_bytes((HERE/name).read_bytes());os.chmod(public/name,0o644)
    for arm in manifest['arms']:
        directory(public/arm,0,0,0o755)
        target=public/arm/'delivery-reconciliation.ts'
        target.write_bytes((HERE/'inputs'/arm/'delivery-reconciliation.ts').read_bytes());os.chmod(target,0o644)
    parent=BASE/'core-ablation'
    if not parent.exists():directory(parent,0,985,0o750)
    info=parent.lstat()
    if parent.is_symlink() or (info.st_uid,info.st_gid,info.st_mode&0o7777)!=(0,985,0o750):raise RuntimeError('Unsafe evidence parent')
    batch=parent/batchid;directory(batch,0,985,0o750)
    sut=BASE/'sut-state'/batchid;directory(sut,0,987,0o750)
    write(batch/'execution-manifest.json',manifest)
    return public,batch,sut

def score(public,context,folder,label):
    config=folder/(label+'-context.json');write(config,context)
    result,pid=child([sys.executable,'-s','-E',str(public/'scorer.py'),str(config)],'ecr-scorer',public,folder/label)
    if result['scorer_uid']!=995 or result['scorer_pid']!=pid:raise RuntimeError('Scorer process identity mismatch')
    write(folder/(label+'-score.json'),result)
    return result

def cell(public,batch,sutroot,arm,scenario,truth,label):
    folder=batch/label;directory(folder,0,985,0o750)
    own=sutroot/label;directory(own,0,987,0o750)
    snapshot=own/'snapshot.json';write(snapshot,scenario['snapshot'],987)
    # Only opaque cell handles reach SUT: private scenario names can reveal
    # synthetic twin effect truth even when the snapshot itself is identical.
    config={'run_id':label,'arm':arm,'scenario_id':label,'input':str(snapshot),
            'module':str(public/arm/'delivery-reconciliation.ts'),
            'private_paths':[str(HERE/'inputs/truth-predeclared.json'),str(HERE/'inputs/scenarios-predeclared.json'),str(HERE/'run.py')]}
    write(own/'configuration.json',config,987)
    receipt,pid=child([str(NODE),str(public/'sut.mjs'),str(own/'configuration.json')],'ecr-sut',public,folder/'sut')
    if receipt['pid']!=pid:raise RuntimeError('SUT process identity mismatch')
    write(folder/'receipt.json',receipt)
    context={'run_id':label,'arm':arm,'scenario_id':label,'input_sha256':sha(snapshot),'truth':truth,'receipt':str(folder/'receipt.json')}
    scored=score(public,context,folder,'primary')
    infra=set(scored['faults'])-{'UNSUPPORTED_AUTOMATIC_CLASSIFICATION','INVALID_ACTION_CLASS_PAIR'}
    if infra:raise RuntimeError('Infrastructure/scorer fault '+str(infra))
    return scored,context,folder

def qualify(public,batch,sut,scenarios,truths):
    results=[]
    for index in (0,1,2):
        s=scenarios[index];scored,context,folder=cell(public,batch,sut,'FULL',s,truths[s['scenario_id']],f'BRIDGE-{index+1:02d}')
        if scored['faults']:raise RuntimeError('FULL bridge normative check failed')
        results.append(scored)
        if index==0:
            original=read(Path(context['receipt']))
            for label,fault in [('identity','RECEIPT_IDENTITY_MISMATCH'),('mutation','INPUT_MUTATION'),('unsupported','UNSUPPORTED_AUTOMATIC_CLASSIFICATION'),('action_pair','INVALID_ACTION_CLASS_PAIR')]:
                changed=copy.deepcopy(original)
                if label=='identity':changed['scenario_id']='PLANTED_WRONG_ID'
                elif label=='mutation':changed['snapshot_after_sha256']='PLANTED_MUTATION'
                elif label=='unsupported':changed['result'].update(classification='PENDING_EXECUTION',permittedActionClass='PENDING_EXECUTION')
                else:changed['result'].update(classification='PENDING_EXECUTION',permittedActionClass='NO_BLIND_RESEND')
                path=folder/(label+'-fixture.json');write(path,changed)
                altered={**context,'receipt':str(path)}
                negative=score(public,altered,folder,'negative-'+label)
                if fault not in negative['faults']:raise RuntimeError('Scorer missed negative fixture '+label)
    if results[1]['result']!=results[2]['result']:raise RuntimeError('Twin result differs')
    return {'status':'PASS_BRIDGE_ONLY','diagnostic_sut_runs':3,'formal_cells_executed':0,'results':results}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['qualify','formal'],required=True);args=parser.parse_args()
    manifest=read(HERE/'execution-manifest.json');guard(manifest);os.umask(0o027)
    batchid=manifest['qualification_id'] if args.mode=='qualify' else manifest['formal_id']
    if args.mode=='formal':
        audit=read(HERE/'bridge-authorization.json')
        if audit['status']!='PASS_HOST_AUDIT' or audit['manifest_sha256']!=sha(HERE/'execution-manifest.json'):raise RuntimeError('Host bridge audit authorization missing')
        if sha(BASE/'core-ablation'/manifest['qualification_id']/'batch-summary.json')!=audit['guest_summary_sha256']:raise RuntimeError('Bridge summary drift')
    public,batch,sut=prepare(manifest,batchid)
    scenarios=read(HERE/'inputs/scenarios-predeclared.json')['scenarios'];truths={r['scenario_id']:r for r in read(HERE/'inputs/truth-predeclared.json')['rows']}
    rows=[]
    try:
        if args.mode=='qualify':summary=qualify(public,batch,sut,scenarios,truths)
        else:
            for arm in manifest['arms']:
                for number,s in enumerate(scenarios,1):
                    row,_,_=cell(public,batch,sut,arm,s,truths[s['scenario_id']],arm+f'-{number:02d}')
                    rows.append(row);print(row['run_id']+'=OBSERVED',flush=True)
                paired=[r for r in rows if r['arm']==arm and r['scenario_id'] in ('C02_TWIN_NO_EFFECT','C03_TWIN_EFFECT')]
                if paired[0]['result']!=paired[1]['result']:raise RuntimeError('Twin result differs for '+arm)
            summary={'status':'COMPLETED_REQUIRES_HOST_AUDIT','formal_cells_executed':len(rows),'results':rows,'scope':'CORE_ONLY_NO_EXTERNAL_EXECUTOR'}
    except Exception as error:
        summary={'status':'FAIL_RETAIN_EVIDENCE','error_type':type(error).__name__,'error':str(error),'completed_scored_cells':len(rows),'results':rows,'execution_counts':'Inspect retained child receipts for interrupted cell; unstarted cells NOT_EXECUTED'}
    summary.update(batch_id=batchid,manifest_sha256=sha(HERE/'execution-manifest.json'),utc_ns=time.time_ns())
    labels=[f'BRIDGE-{i:02d}' for i in range(1,4)] if args.mode=='qualify' else [arm+f'-{i:02d}' for arm in manifest['arms'] for i in range(1,len(scenarios)+1)]
    table=[]
    for label in labels:
        folder=batch/label;start=folder/'sut.started.json';primary=folder/'primary-score.json'
        started=True if start.exists() else (None if (folder/'sut.stdout').exists() else False)
        if primary.exists():
            faults=set(read(primary)['faults'])
            infrastructure=faults-{'UNSUPPORTED_AUTOMATIC_CLASSIFICATION','INVALID_ACTION_CLASS_PAIR'}
            status=('FAIL' if infrastructure else 'OBSERVED') if args.mode=='formal' else ('PASS' if not faults else 'FAIL')
        elif started is True:status='FAIL'
        elif folder.exists():status='BLOCKED'
        else:status='NOT_EXECUTED'
        row={'run_id':label,'status':status,'sut_started':started}
        if start.exists():row['start_receipt']=read(start)
        if primary.exists():row['score']=read(primary)
        table.append(row)
    summary['cells']=table
    summary['completed_scored_cells']=sum('score' in r for r in table)
    summary['executed_known']=sum(r['sut_started'] is True for r in table)
    summary['execution_unknown']=sum(r['sut_started'] is None for r in table)
    summary['formal_cells_executed']=summary['executed_known'] if args.mode=='formal' else 0
    summary['diagnostic_sut_runs']=summary['executed_known'] if args.mode=='qualify' else 0
    write(batch/'batch-summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k not in ('results','cells')}),flush=True)
    raise SystemExit(1 if summary['status']=='FAIL_RETAIN_EVIDENCE' else 0)

if __name__=='__main__':main()
