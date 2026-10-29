"""User-confirmed current PM check; never use these captures to fit weights."""
import argparse
import json
import os
from pathlib import Path
import numpy as np
import torch
from scipy.optimize import minimize_scalar
from signal_model import ROOT,CLASSES,features
from classification_models import build_model


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--message-hz',type=float,default=20000)
    a=p.parse_args();exp=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    os.environ.update(YOLO_OFFLINE='true',YOLOV5_CONFIG_DIR=str(ROOT/'config/runtime'))
    torch.set_num_threads(4)
    cp=torch.load(exp/'runs/branches_amp/weights/best.pt',map_location='cpu')
    model=build_model('branches_amp',num_classes=6).cuda().eval();model.load_state_dict(cp['state_dict'])
    result=[];t=np.arange(4032)/25e6;u=(t-t.mean())/np.ptp(t)
    for raw in sorted(a.folder.glob('batch_*/source_pha4.raw')):
        source=np.fromfile(str(raw),dtype='<i4').reshape(4,4096,2)
        fields,gain=features(source)
        with torch.inference_mode():
            inputs=[torch.from_numpy(fields[k].astype(np.float32)/255).unsqueeze(0).cuda() for k in ('tf','spectrum','amplitude')]
            logits=model(*inputs)[0].cpu().numpy()
        probability=np.exp(logits-logits.max());probability/=probability.sum()
        iq=source[:,64:,0]+1j*source[:,64:,1];phase=np.unwrap(np.angle(iq),axis=-1).T
        def fit(freq,full=False):
            A=np.column_stack([np.ones(4032),u,np.sin(2*np.pi*freq*t),np.cos(2*np.pi*freq*t)])
            w=np.linalg.lstsq(A,phase,rcond=None)[0];residual=phase-A@w
            return (w,residual) if full else float(np.mean(residual**2))
        opt=minimize_scalar(fit,bounds=(a.message_hz*.9,a.message_hz*1.1),method='bounded')
        w,res=fit(opt.x,True)
        row=dict(raw=str(raw),user_label='PM',user_message_hz=a.message_hz,logits=logits.tolist(),
            float_prediction=CLASSES[int(logits.argmax())],probabilities=probability.tolist(),amplitude=gain,
            fitted_message_hz=float(opt.x),fitted_phase_degrees=np.rad2deg(np.hypot(w[2],w[3])).tolist(),
            fitted_carrier_hz=(w[1]/np.ptp(t)/(2*np.pi)).tolist(),
            phase_fit_residual_degrees=float(np.rad2deg(np.sqrt(np.mean(res**2)))))
        result.append(row)
        print(row['float_prediction'],row['fitted_message_hz'],row['fitted_phase_degrees'],row['fitted_carrier_hz'],row['phase_fit_residual_degrees'],flush=True)
    (a.folder/'float_and_phase_check.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
