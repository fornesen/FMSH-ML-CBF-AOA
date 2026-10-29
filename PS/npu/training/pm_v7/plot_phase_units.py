"""Plot the independently captured 90rad and pi/2rad phase laws."""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from signal_model import ROOT


def main():
    exp=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    t=np.arange(4032)/25e6;u=(t-t.mean())/np.ptp(t)
    A=np.column_stack([np.ones(4032),u,np.sin(2*np.pi*20000*t),np.cos(2*np.pi*20000*t)])
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
    fig,ax=plt.subplots(2,2,figsize=(12,7),constrained_layout=True)
    for c,folder in enumerate(('real_pm_20k_board','real_pm_90deg_verified')):
        a=np.fromfile(str(exp/folder/'batch_00/source_pha4.raw'),dtype='<i4').reshape(4,4096,2)[0,64:]
        x=a[:,0]+1j*a[:,1];p=np.unwrap(np.angle(x));w=np.linalg.lstsq(A,p,rcond=None)[0]
        mod=p-A[:,:2]@w[:2]
        ax[0,c].plot(t*1e6,np.rad2deg(mod),lw=.8,label='实际IQ去载波相位')
        ax[0,c].plot(t*1e6,np.rad2deg(A[:,2:]@w[2:]),lw=1,ls='--',label='20kHz正弦拟合')
        ax[0,c].set(xlabel='轮内时间 / μs',ylabel='相位 / °',title=('原设置90rad' if c==0 else '修正设置1.5708rad')+'；实测峰值约%.1f°'%np.rad2deg(np.hypot(w[2],w[3])))
        ax[0,c].legend(fontsize=8)
        carrier=w[1]/np.ptp(t)/(2*np.pi)
        z=x*np.exp(-2j*np.pi*carrier*t);h=.5-.5*np.cos(2*np.pi*np.arange(4032)/4032)
        power=abs(np.fft.fftshift(np.fft.fft(z*h)))**2;power=10*np.log10(np.maximum(power/power.max(),1e-12))
        freq=np.fft.fftshift(np.fft.fftfreq(4032,1/25e6))
        ax[1,c].plot(freq/(1e6 if c==0 else 1e3),power,lw=.8)
        ax[1,c].set(xlim=(-2.5,2.5) if c==0 else (-150,150),ylim=(-65,1),
            xlabel='相对载波频偏 / '+('MHz' if c==0 else 'kHz'),ylabel='相对峰值功率 / dB',title='仅为对比显示去载波；NPU预处理未作此变换')
    for a in ax.ravel():a.grid(alpha=.2)
    fig.savefig(str(exp/'phase_unit_comparison.png'),dpi=150)


if __name__=='__main__':main()
