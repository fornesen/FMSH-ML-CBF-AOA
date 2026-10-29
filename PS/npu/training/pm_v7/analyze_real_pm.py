"""Fit a sine phase law to real PM; capture diagnostics, not generic FM/PM proof."""
import json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar
from signal_model import ROOT, SCALE


def main():
    exp=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    result=[]
    t=np.arange(4032)/25e6;u=(t-t.mean())/np.ptp(t)
    for path in sorted((exp/'real_pm_board').glob('batch_*/source_pha4.raw')):
        a=np.fromfile(str(path),dtype='<i4').reshape(4,4096,2)[:,64:]
        x=(a[:,:,0]+1j*a[:,:,1])/SCALE
        phase=np.unwrap(np.angle(x),axis=-1).T
        def fit(freq,full=False):
            design=np.column_stack([np.ones(4032),u,np.sin(2*np.pi*freq*t),np.cos(2*np.pi*freq*t)])
            weights=np.linalg.lstsq(design,phase,rcond=None)[0]
            residual=phase-design@weights
            return (weights,residual) if full else float(np.mean(residual**2))
        fit_result=minimize_scalar(fit,bounds=(2000,5000),method='bounded')
        coefficients,residual=fit(fit_result.x,True)
        result.append(dict(raw=str(path),message_fit_hz=float(fit_result.x),
            phase_deviation_deg_per_round=np.rad2deg(np.hypot(coefficients[2],coefficients[3])).tolist(),
            carrier_fit_hz_per_round=(coefficients[1]/np.ptp(t)/(2*np.pi)).tolist(),
            phase_residual_rms_deg=float(np.rad2deg(np.sqrt(np.mean(residual**2)))),
            raw_rms_codes_per_round=(np.sqrt(np.mean(abs(x)**2,axis=-1))*SCALE).tolist()))
        result[-1]['phase_fit_accepted']=result[-1]['phase_residual_rms_deg']<10
        result[-1]['frequency_search_hz']=[2000,5000]
        if not result[-1]['phase_fit_accepted']:
            result[-1]['reason']='Large phase residual; fitted frequency/amplitude are not reliable source-parameter estimates'
    assert result
    target=exp/'real_pm_board/sine_phase_fit.json';target.write_text(json.dumps(result,indent=2)+'\n')
    for row in result: print(row['message_fit_hz'],row['phase_deviation_deg_per_round'],row['phase_residual_rms_deg'])


if __name__=='__main__':main()
