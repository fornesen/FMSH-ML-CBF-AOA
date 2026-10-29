"""Scientific diagnostics of the real captured IQ, retaining measured phase."""
from pathlib import Path
import numpy as np
from scipy.ndimage import uniform_filter1d
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from signal_model import ROOT


def main():
    root=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz/real_pm_board'
    raw=np.fromfile(str(root/'batch_00/source_pha4.raw'),dtype='<i4').reshape(4,4096,2)[:,64:]
    x=raw[:,:,0]+1j*raw[:,:,1]
    t=np.arange(4032)/25e6
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
    fig,ax=plt.subplots(4,1,figsize=(11,10),constrained_layout=True)
    for k in range(4):
        ax[0].plot(t*1e6,abs(x[k]),lw=.6,label='轮次%d'%(k+1))
        phase=np.unwrap(np.angle(x[k]));trend=np.polyval(np.polyfit(t,phase,1),t)
        ax[1].plot(t*1e6,np.rad2deg(phase-trend),lw=.8)
        inst=np.angle(x[k,1:]*x[k,:-1].conj())*25e6/(2*np.pi)
        ax[2].plot(t[1:]*1e6,uniform_filter1d(inst,64)/1e3,lw=.8)
        w=.5-.5*np.cos(2*np.pi*np.arange(4032)/4032)
        spectrum=abs(np.fft.fftshift(np.fft.fft(x[k]*w)))**2
        f=np.fft.fftshift(np.fft.fftfreq(4032,1/25e6))
        ax[3].plot(f/1e3,10*np.log10(np.maximum(spectrum/spectrum.max(),1e-12)),lw=.8)
    ax[0].set(ylabel='包络 / 原始码值',title='用户标记 PM 的实际 PHA：同一批次四轮，各丢弃前64点')
    ax[0].legend(ncol=4,fontsize=9)
    ax[1].set(ylabel='去线性趋势相位 / °',title='相位保留实测变化；不能据标签假定为理想正弦')
    ax[2].set(ylabel='瞬时频率 / kHz',xlabel='轮内时间 / μs',title='64点平滑；理想3.333kHz/90° PM的峰值频偏约5.24kHz')
    ax[3].set(xlim=(-650,-350),ylim=(-65,1),xlabel='基带频率 / kHz',ylabel='相对峰值功率 / dB',title='4032点 Hann 频谱，频率分辨率约6.2kHz')
    for a in ax:a.grid(alpha=.2)
    fig.savefig(str(root/'iq_diagnostics.png'),dpi=150)


if __name__=='__main__':main()
