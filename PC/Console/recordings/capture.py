"""Verify, inspect and replay a complete recorded AOA batch. No board connection."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from common.archive_restore import restore
from common.archive_io import verify_archive

MATH_KEYS=('status','complete','processing_mode','selected_bins','direction_frames','direction_method',
           'covariance_concentration','raw_validation','groups','joint','localization_result')

def read_capture(manifest):
    manifest=Path(manifest).resolve();summary=json.loads(manifest.read_text())
    archive=manifest.parent/summary['archive'];verify_archive(archive,summary)
    name=summary.get('capture_file')
    if not name:raise ValueError('Archive has no V1.1 paired capture; raw historical data is not automatically relabeled')
    with tarfile.open(archive,'r:gz') as tar:
        capture=json.load(tar.extractfile(name))
    if capture.get('schema')!='aoa.raw_capture.v1' or capture.get('run_id')!=summary.get('run_id'):
        raise ValueError('Archive/capture identity mismatch')
    if capture['result'].get('run_id')!=capture['run_id']:
        raise ValueError('Capture/result identity mismatch')
    for key,value in {'adc_samples':16384,'channels':6,'adc_bytes':196608,'slot_bytes':64,
                      'pl_input_bytes':1048576,'pl_output_bytes':1572864,'fft_samples':4096,
                      'fft_component_bits':39,'pha_samples':4096,'pha_offset':16,'pha_sample_bytes':8}.items():
        if capture.get('layout',{}).get(key)!=value:raise ValueError('Unsupported recording byte layout: '+key)
    members={m['name']:m for m in summary['members']}
    indices=[];used_files=set()
    for row in capture['rounds']:
        indices.append(row['index'])
        if row['run_id']!=capture['run_id']:raise ValueError('Round identity mismatch')
        for role,file in row['files'].items():
            if file['bytes'] is not None and (file['name'] not in members or members[file['name']]['bytes']!=file['bytes']):
                raise ValueError('Round artifact missing or size mismatch: '+file['name'])
            if file['bytes'] is not None:
                if file['name'] in used_files:raise ValueError('Artifact reused across capture rounds')
                used_files.add(file['name'])
            if row['mode']=='hardware' and row['complete']:
                expected={'adc':196608,'pl_input':1048576,'pl_output':1572864,'pha':32768}[role]
                if file['bytes']!=expected:raise ValueError('Complete hardware round is missing full '+role+' evidence')
    if indices!=list(range(len(indices))):raise ValueError('Round order mismatch')
    if 'scan_checks' in capture:
        verification=capture['scan_checks']
        if verification['checks'] and verification['source_round'] != 3:
            raise ValueError('Scan check must reference the fourth original frame')
        for check in verification['checks']:
            if check.get('npu_eligible') is not False:
                raise ValueError('Scan check must not enter original NPU input')
            for field,size in [('fft_file',1572864),('pha_file',32768)]:
                name=check.get(field)
                if check.get('valid') and not name:
                    raise ValueError('Valid scan check is missing raw evidence')
                if name and (name not in members or members[name]['bytes'] != size):
                    raise ValueError('Scan check artifact missing or size mismatch')
            if check.get('valid') and check.get('source_input_file') != capture['rounds'][3]['files']['pl_input']['name']:
                raise ValueError('Scan check ADC input reference mismatch')
    return summary,capture

def numeric_diff(expected,actual,path='',differences=None):
    if differences is None:differences=[]
    if isinstance(expected,bool) or expected is None or isinstance(expected,str):
        if expected!=actual:differences.append(path)
    elif isinstance(expected,(int,float)):
        if not isinstance(actual,(int,float)) or not math.isclose(expected,actual,rel_tol=1e-8,abs_tol=1e-9):differences.append(path)
    elif isinstance(expected,list):
        if not isinstance(actual,list) or len(expected)!=len(actual):differences.append(path)
        else:
            for i,(a,b) in enumerate(zip(expected,actual)):numeric_diff(a,b,path+'/'+str(i),differences)
    elif isinstance(expected,dict):
        if not isinstance(actual,dict):differences.append(path)
        else:
            for key,value in expected.items():
                if key not in actual:differences.append(path+'/'+key)
                else:numeric_diff(value,actual[key],path+'/'+key,differences)
    return differences

def replay(manifest,destination,native=None,export=False):
    manifest=Path(manifest).resolve();summary,capture=read_capture(manifest)
    if summary.get('partial') or not capture['result'].get('complete'):
        raise ValueError('Partial batch preserved as evidence; complete-result replay is unavailable')
    destination=Path(destination).resolve()
    restore(manifest,destination)
    executable=Path(native) if native else HERE/'bin'/('aoa_capture_replay.exe' if os.name=='nt' else 'aoa_capture_replay')
    if not executable.is_file():raise FileNotFoundError('Build/install the offline PS replay executable: '+str(executable))
    output=destination/'ps_replay.json'
    subprocess.run([str(executable),str(destination/summary['capture_file']),str(output)],check=True)
    actual=json.loads(output.read_text());differences=[]
    for key in MATH_KEYS:
        if key in capture['result']:
            if key not in actual['replayed_result']:differences.append('/'+key)
            else:numeric_diff(capture['result'][key],actual['replayed_result'][key],'/'+key,differences)
    if 'beam_scan' in capture['result']:
        expected=dict(capture['result']['beam_scan'])
        computed=dict(actual['replayed_result'].get('beam_scan',{}))
        # Hardware captures and CPU timing are independent evidence. Their numeric
        # measurements are replayed separately from the deterministic scan curve.
        for item in (expected,computed):
            item.pop('elapsed_ms',None);item.pop('verification',None)
            if 'fusion' in item:
                item['fusion']=dict(item['fusion'])
                item['fusion'].pop('ml_elapsed_ms',None)
        numeric_diff(expected,computed,'/beam_scan',differences)
    related=[];npu_results=[]
    for path in (manifest.parent.parent/'npu').glob('*.archive.json'):
        row=json.loads(path.read_text())
        if row.get('run_id')==capture['run_id']:
            related.append(str(path));archive=path.parent/row['archive'];verify_archive(archive,row)
            with tarfile.open(archive,'r:gz') as tar:
                for name in tar.getnames():
                    if name.endswith(('_npu_result.json','_npu_error.json')):
                        npu=json.load(tar.extractfile(name))
                        if npu.get('run_id')!=capture['run_id']:raise ValueError('NPU/result identity mismatch')
                        if 'source_raw_sha256' in npu:
                            source=hashlib.sha256()
                            for frame in capture['rounds']:
                                source.update((destination/frame['files']['pha']['name']).read_bytes())
                            if source.hexdigest()!=npu['source_raw_sha256']:raise ValueError('NPU consumed different PHA samples')
                        npu_results.append(npu)
    comparison={'run_id':capture['run_id'],'math_matches_recorded_result':not differences,
                'scan_check_replays':actual.get('scan_check_replays',[]),
                'differences':differences,'absolute_tolerance':1e-9,'relative_tolerance':1e-8,
                'adc_input_checks':actual['adc_input_checks'],'npu_archive_manifests':related,
                'npu_results':npu_results,'npu_handoff':capture['npu'],
                'original_archive_sha256':summary['archive_sha256'],
                'replay_executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),
                'scope':actual['scope']}
    (destination/'comparison.json').write_text(json.dumps(comparison,indent=2),encoding='utf-8')
    if export:export_npz(capture,destination,actual)
    return comparison

def export_npz(capture,folder,replayed=None):
    # Optional analysis export; verify/replay/list use only the Python standard library.
    import numpy as np
    adc=[];words=[];pl_input=[];fft=[];pha=[]
    o=capture['options'];bits=o['adc_bits'];mask=(1<<bits)-1;sign=1<<(bits-1)
    for row in capture['rounds']:
        files=row['files'];read=lambda role:(folder/files[role]['name']).read_bytes()
        if files['adc']['bytes'] is None:raise ValueError('NPZ raw ADC export needs hardware ADC evidence')
        raw=np.frombuffer(read('adc'),dtype='>u2' if o['big_endian'] else '<u2').reshape(16384,6).T.copy()
        if np.any(raw & (~mask & 65535)):raise ValueError('ADC padding is not zero')
        value=(raw.astype(np.int32)^(sign if o['offset_binary'] else 0))&mask
        value=np.where(value&sign,value-(1<<bits),value).astype(np.int16)
        adc.append(value);words.append(raw)
        slots=np.frombuffer(read('pl_input'),dtype=np.uint8).reshape(16384,64)
        pl_input.append(slots[:,:12].copy().view('<i2').reshape(16384,6).T)
        slots=np.frombuffer(read('pl_output'),dtype=np.uint8).reshape(24576,64)
        parts=[]
        for offset in [0,5]:
            unsigned=sum(slots[:,offset+b].astype(np.int64)<<(8*b) for b in range(5))&((1<<39)-1)
            parts.append(np.where(unsigned&(1<<38),unsigned-(1<<39),unsigned))
        fft.append(np.stack(parts,axis=-1).reshape(6,4096,2))
        pha.append(slots[:4096,16:24].copy().view('<i4').reshape(4096,2))
    values=dict(adc_words=np.stack(words),adc_signed_codes=np.stack(adc),pl_input_signed_codes=np.stack(pl_input),
                pl_fft_unscaled_iq_s39=np.stack(fft),pl_pha_iq_s32=np.stack(pha),
                adc_time_s=np.arange(16384)/o['sample_rate'],baseband_time_s=np.arange(4096)/(o['sample_rate']/4),
                run_id=capture['run_id'],options_json=json.dumps(o),rounds_continuous=False)
    if replayed is not None:
        values['selected_snapshots_json']=json.dumps(replayed['selected_snapshots'])
    np.savez_compressed(folder/'signals.npz',**values)

def inventory(root):
    groups={}
    for path in sorted(Path(root).rglob('*.archive.json')):
        row=json.loads(path.read_text());run=row.get('run_id')
        if not run:continue
        group=groups.setdefault(run,{'run_id':run,'archives':[]})
        group['archives'].append({'manifest':str(path),'partial':row.get('partial'),
                                  'capture_file':row.get('capture_file'),'bytes':row.get('archive_bytes')})
    return list(groups.values())

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('list');p.add_argument('folder',type=Path)
    p=sub.add_parser('verify');p.add_argument('manifest',type=Path)
    p=sub.add_parser('replay');p.add_argument('manifest',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--native',type=Path);p.add_argument('--export-npz',action='store_true')
    args=parser.parse_args()
    if args.action=='list':result=inventory(args.folder)
    elif args.action=='verify':
        summary,capture=read_capture(args.manifest)
        result={'run_id':capture['run_id'],'verified':True,'rounds':len(capture['rounds']),
                'partial':summary.get('partial'),'result_status':capture['result'].get('status')}
    else:result=replay(args.manifest,args.output,args.native,args.export_npz)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if isinstance(result,dict) and result.get('math_matches_recorded_result') is False else 0

if __name__=='__main__':raise SystemExit(main())
