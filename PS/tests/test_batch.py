"""Exercise four-round collection, PHA handoff and final-FFT-only DOA.

Synthetic inputs and loopback TCP only; no PL or NPU hardware is contacted.
"""
import argparse
import cmath
import csv
import json
import math
from pathlib import Path
import struct
import subprocess

parser=argparse.ArgumentParser()
parser.add_argument('--binary',required=True)
parser.add_argument('--wsl-distro')
args=parser.parse_args()
binary=args.binary if args.wsl_distro else str(Path(args.binary).resolve())
root=Path(__file__).resolve().parents[1]/'build'/'batch-tests'
root.mkdir(parents=True,exist_ok=True)
root_target=subprocess.check_output(['wsl.exe','-d',args.wsl_distro,'--exec','wslpath','-a','-u',str(root)],text=True).strip() if args.wsl_distro else str(root)

def target(value):
    return root_target+'/'+value.relative_to(root).as_posix() if isinstance(value,Path) else str(value)

def command(executable,*options):
    result=[executable,*(["--once","--save-results","--no-ipc"] if executable==binary else []),*map(target,options)]
    return ['wsl.exe','-d',args.wsl_distro,'--exec',*result] if args.wsl_distro else result

def read_json(prefix,suffix='_metadata.json'):
    return json.loads(Path(str(prefix)+suffix).read_text())

def read_rows(prefix,suffix):
    with Path(str(prefix)+suffix).open(newline='') as stream:
        return list(csv.DictReader(stream))

def invoke(name,*options,expected=0):
    prefix=root/name
    result=subprocess.run(command(binary,'--output-prefix',prefix,'--angle-start',-50,
                                  '--angle-step',2,'--angle-count',51,'--bins','4,4092',*options),
                          capture_output=True,text=True,timeout=45)
    assert result.returncode==expected,result.stdout+result.stderr
    return prefix,read_json(prefix),result.stdout

c,rf=299792458.,433e6
d=c/rf/2
positions=[0,d,2*d,2*d+.5,3*d+.5,4*d+.5]
positions=[x-positions[-1]/2 for x in positions]
payloads=[]; frames=[]
for index,angles in enumerate([(-30,10),(-20,20),(-10,30),(0,40)]):
    raw=bytearray(24576*64)
    for channel,x in enumerate(positions):
        for b,bin_index in enumerate((4,4092)):
            value=sum(alpha*cmath.exp(2j*math.pi*rf/c*x*math.sin(math.radians(theta)))
                      for alpha,theta in zip((1+.2j,.4-.3j),angles))*(1<<26)/(b+1)
            offset=(channel*4096+bin_index)*64
            raw[offset:offset+10]=b''.join((round(v)&((1<<39)-1)).to_bytes(5,'little') for v in (value.real,value.imag))
    pha=bytearray()
    for sample in range(4096):
        i=(index+1)*100000+sample; q=-(index+1)*200000-sample
        if sample==0: i=-(1<<31)
        if sample==1: i=(1<<31)-1
        if sample==4095: q=-(1<<31)
        if index==2: i=q=0  # A zero-valued PHA frame must remain valid.
        value=struct.pack('<ii',i,q); pha.extend(value)
        raw[sample*64+16:sample*64+24]=value
    path=root/('frame'+str(index)+'.raw'); path.write_bytes(raw)
    frames.append(path); payloads.append(bytes(pha))

def replay_options(paths):
    return [value for path in paths for value in ('--fft-raw',path)]

prefix,meta,output=invoke('success',*replay_options(frames))
assert meta['capture_round_count']==4 and meta['completed_capture_rounds']==4 and meta['batch_capture_complete']
assert meta['direction_round_index']==3 and meta['npu_input_ready'] and meta['pha_processed']
assert meta['pha_collected_samples']==16384 and len(meta['captures'])==4
assert Path(str(prefix)+'_pha4.raw').read_bytes()==b''.join(payloads)
for index,frame in enumerate(frames):
    round_prefix=str(prefix)+'_round'+str(index+1)
    assert Path(round_prefix+'_fft_slots.raw').read_bytes()==frame.read_bytes()
    assert Path(round_prefix+'_pha.raw').read_bytes()==payloads[index]
    assert not Path(round_prefix+'_ml3_grid.csv').exists()
    assert meta['captures'][index]['pha_saved'] and meta['captures'][index]['pha_written_samples']==4096
assert Path(str(prefix)+'_fft_slots.raw').read_bytes()==frames[-1].read_bytes()
directions=read_rows(prefix,'_directions.csv')
assert [float(row['angle_deg']) for row in directions]==[0,40,0,40]
assert len(read_rows(prefix,'_ml3_grid.csv'))==2*2*51*51
ready=read_json(prefix,'_npu_ready.json')
assert ready['ready'] and not ready['npu_started'] and ready['run_id']==meta['run_id']
assert ready['shape']==[4,4096,2] and ready['byte_count']==131072 and ready['component_order']==['I','Q']
assert ready['direction_round_index']==3 and not ready['continuous_between_rounds']
assert ready['raw_file']==root_target+'/success_pha4.raw'
assert output.index('NPU_READY:')<output.index('FFT slots=')
pha_rows=read_rows(prefix,'_pha4.csv')
assert len(pha_rows)==16384
for index,row in enumerate(pha_rows):
    r,s=divmod(index,4096); i,q=struct.unpack_from('<ii',payloads[r],s*8)
    assert [int(row[key]) for key in ('round_index','sample_index','concatenated_index','pha_i','pha_q')]==[r,s,index,i,q]
    assert math.isclose(float(row['time_within_round_s']),s/25e6,abs_tol=1e-18)
print('PASS four distinct PHA frames, signed32 boundaries/zero frame/order, atomic NPU marker and last FFT only')

for name,modify in [('partial',lambda raw:raw.__setitem__(slice(-64,None),b'\xa5'*64)),
                    ('bad_tail',lambda raw:raw.__setitem__(4096*64+16,1))]:
    raw=bytearray(frames[-1].read_bytes()); modify(raw)
    bad=root/(name+'.raw'); bad.write_bytes(raw)
    failed,failed_meta,text=invoke('failed_'+name,*replay_options([*frames[:3],bad]),expected=1)
    assert not failed_meta['npu_input_ready'] and not failed_meta['capture_complete'] and not failed_meta['processing_complete']
    assert failed_meta['completed_capture_rounds']==3 and failed_meta['pha_collected_samples']==12288
    assert not Path(str(failed)+'_npu_ready.json').exists() and not Path(str(failed)+'_pha4.raw').exists()
    assert not read_rows(failed,'_directions.csv') and 'NPU_READY:' not in text
    if name=='bad_tail': assert failed_meta['captures'][-1]['pha_format_errors']==1
    # Reusing a successful prefix must revoke its previous readiness/data.
    if name=='partial':
        _,reused,text=invoke('success',*replay_options([*frames[:3],bad]),expected=1)
        assert not reused['npu_input_ready'] and reused['run_id']!=meta['run_id']
        assert not Path(str(prefix)+'_npu_ready.json').exists() and not Path(str(prefix)+'_pha4.raw').exists()
        assert not Path(str(prefix)+'_round4_pha.raw').exists()
        assert 'NPU_READY:' not in text

short=root/'short.raw'; short.write_bytes(b'\x00'*123)
failed,failed_meta,_=invoke('short_third',*replay_options([*frames[:2],short,frames[3]]),expected=1)
assert failed_meta['completed_capture_rounds']==2 and len(failed_meta['captures'])==3 and not failed_meta['npu_input_ready']
assert not Path(str(failed)+'_npu_ready.json').exists()
# Restore the successful fixture after testing stale-prefix invalidation.
prefix,meta,_=invoke('success',*replay_options(frames))
single,single_meta,_=invoke('single_fallback','--fft-raw',frames[-1],'--capture-rounds',1)
assert single_meta['capture_round_count']==1 and not single_meta['npu_input_ready'] and not single_meta['pha_processed']
assert [float(row['angle_deg']) for row in read_rows(single,'_directions.csv')]==[0,40,0,40]
print('PASS failed fourth/short input/tail layout rejection, stale handoff revocation and one-round compatibility')

zero=root/'zero_fft.raw'; raw=bytearray(frames[-1].read_bytes())
for record in range(24576): raw[record*64:record*64+10]=bytes(10)
zero.write_bytes(raw)
no_signal,no_signal_meta,_=invoke('no_signal',*replay_options([zero]*4))
assert no_signal_meta['run_status']=='no_signal' and no_signal_meta['npu_input_ready']
assert not read_rows(no_signal,'_directions.csv') and read_json(no_signal,'_npu_ready.json')['ready']

adc_frames=[]
for index in range(4):
    codes=[32768+index*100+channel for channel in range(6)]
    adc=struct.pack('<6H',*codes)*16384
    path=root/('adc'+str(index)+'.raw'); path.write_bytes(adc); adc_frames.append(path)
adc_options=[value for path in adc_frames for value in ('--adc-raw',path)]
dry,dry_meta,_=invoke('adc_file_batch','--dry-run',*adc_options)
assert len(dry_meta['captures'])==4 and dry_meta['completed_capture_rounds']==0 and not dry_meta['npu_input_ready']
for index,path in enumerate(adc_frames):
    assert Path(str(dry)+'_round'+str(index+1)+'_adc.raw').read_bytes()==path.read_bytes()
assert Path(str(dry)+'_adc.raw').read_bytes()==adc_frames[-1].read_bytes()
print('PASS PHA readiness independent of DOA no-signal; four ADC files dry-run never signals NPU')

server_path=str(Path(binary).parent/'aoa_tcp_fixture').replace('\\','/')
tcp_payload=root/'four_tcp_frames.raw'; tcp_payload.write_bytes(b''.join(path.read_bytes() for path in adc_frames))
for name,mode,expected in [('tcp_four','full',0),('tcp_fourth_short','short-last',1)]:
    server=subprocess.Popen(command(server_path,tcp_payload,'5354415254',mode,4),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        port=int(server.stdout.readline().strip())
        tcp,tcp_meta,text=invoke(name,'--tcp-host','127.0.0.1','--tcp-port',port,'--dry-run',expected=expected)
        stdout,stderr=server.communicate(timeout=10)
        assert server.returncode==0,stdout+stderr
        assert len(tcp_meta['captures'])==4 and tcp_meta['capture_round_count']==4 and not tcp_meta['npu_input_ready']
        for index in range(3):
            assert Path(str(tcp)+'_round'+str(index+1)+'_adc.raw').read_bytes()==adc_frames[index].read_bytes()
        if expected==0:
            assert Path(str(tcp)+'_round4_adc.raw').read_bytes()==adc_frames[-1].read_bytes()
            assert struct.unpack('<6h',Path(str(tcp)+'_input_slots.raw').read_bytes()[:12])==tuple(300+i for i in range(6))
        else:
            assert tcp_meta['adc_received_bytes']==37 and Path(str(tcp)+'_round4_adc.raw').stat().st_size==37
            assert not Path(str(tcp)+'_round4_input_slots.raw').exists()
        assert not Path(str(tcp)+'_npu_ready.json').exists() and 'NPU_READY:' not in text
    finally:
        if server.poll() is None: server.kill(); server.wait()
print('PASS four separate TCP connections/START triggers, four distinct inputs and fourth-frame EOF refusal')

invalid_options=[('--capture-rounds',2),('--capture-rounds',4,'--fft-raw',frames[0]),
                 ('--capture-rounds',1,*replay_options(frames))]
invalid_options.extend(tuple(replay_options(frames[:count])) for count in (2,3))
for options in invalid_options:
    result=subprocess.run(command(binary,*options),capture_output=True,text=True)
    assert result.returncode==2,result.stdout+result.stderr
print('ALL PASS four-round software validation. Synthetic directory:',root)
