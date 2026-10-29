"""Export a static scientific training/holdout overview."""
import csv
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from signal_model import ROOT,CLASSES


def main():
    exp=ROOT/'experiments/classifier_sixsum_pm_v7b_3330hz'
    with (exp/'runs/branches_amp/results.csv').open() as f: rows=list(csv.DictReader(f))
    completion=json.loads((exp/'runs/branches_amp/completion.json').read_text())
    result=json.loads((exp/'evaluation/metrics.json').read_text())['results']['test_main']
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
    fig,ax=plt.subplots(1,2,figsize=(12,4.8),constrained_layout=True)
    epochs=[int(x['epoch']) for x in rows]
    ax[0].plot(epochs,[float(x['train_loss']) for x in rows],label='训练CE')
    ax[0].plot(epochs,[float(x['val_loss']) for x in rows],label='验证CE')
    ax[0].axvline(completion['best_epoch'],ls='--',color='grey',label='按验证宏F1选择的轮次')
    ax[0].set(xlabel='最终阶段训练轮次',ylabel='交叉熵',title='已出现过拟合；保留验证F1最佳权重')
    ax[0].grid(alpha=.2);ax[0].legend(fontsize=8)
    cm=np.array(result['confusion_matrix']);rate=cm/np.maximum(cm.sum(1,keepdims=True),1)
    ax[1].imshow(rate,vmin=0,vmax=1,cmap='Blues')
    for i in range(6):
        for j in range(6):ax[1].text(j,i,'%d\n%.1f%%'%(cm[i,j],rate[i,j]*100),ha='center',va='center',fontsize=8,color='white' if rate[i,j]>.6 else 'black')
    ax[1].set(xticks=range(6),xticklabels=CLASSES,yticks=range(6),yticklabels=CLASSES,
              xlabel='预测类别',ylabel='仿真标签',title='独立仿真主测试（浮点）：%.2f%%'%(result['accuracy']*100))
    fig.savefig(str(exp/'evaluation/training_and_confusion.png'),dpi=160)


if __name__=='__main__':main()
