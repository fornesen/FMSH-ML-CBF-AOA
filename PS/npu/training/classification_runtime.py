"""Cached deterministic inputs and explicit four-class/folded-binary metrics."""
import cv2
import numpy as np
import torch
from torch.utils.data import Dataset


class CaptureDataset(Dataset):
    def __init__(self,records,variant):
        self.records=records
        self.variant=variant
        self.values=[]
        for item in records:
            if variant=="rgb_classifier":
                image=cv2.imread(item["rgb_path"])
                self.values.append(np.ascontiguousarray(image[:,:,::-1].transpose(2,0,1)))
            else:
                with np.load(item["feature_path"]) as z:
                    self.values.append({k:z[k].copy() for k in z.files})

    def __len__(self):
        return len(self.records)

    def __getitem__(self,index):
        value=self.values[index]
        if self.variant=="rgb_classifier":
            inputs=(torch.from_numpy(value.copy()).float()/255,)
        else:
            amplitude=value["amplitude"].astype(np.float32)/255
            sequence=np.concatenate([amplitude,value["phase"]],1) if self.variant=="branches_phase" else amplitude
            inputs=(torch.from_numpy(value["tf"].copy()).float()/255,
                    torch.from_numpy(value["spectrum"].copy()).float()/255,
                    torch.from_numpy(sequence.copy()).float())
        return inputs,int(self.records[index]["class_index"])


def class_metrics(labels,predictions,classes=None):
    classes=list(classes) if classes is not None else ["FM","AM","CW","noise"]
    labels=np.asarray(labels,dtype=int)
    predictions=np.asarray(predictions,dtype=int)
    confusion=np.zeros((len(classes),len(classes)),dtype=int)
    np.add.at(confusion,(labels,predictions),1)
    recalls=np.diag(confusion)/np.maximum(confusion.sum(1),1)
    precision=np.diag(confusion)/np.maximum(confusion.sum(0),1)
    f1=2*recalls*precision/np.maximum(recalls+precision,1e-12)
    return dict(accuracy=float(np.mean(labels==predictions)),balanced_accuracy=float(recalls.mean()),
                macro_f1=float(f1.mean()),confusion_matrix=confusion.tolist(),
                per_class_recall=recalls.tolist(),per_class_precision=precision.tolist(),
                per_class_f1=f1.tolist(),classes=classes)


def binary_metrics(records,scores,threshold):
    true=np.array([x["class_index"]==0 for x in records])
    predicted=np.array(scores)>=threshold
    tp,fn=int((true&predicted).sum()),int((true&~predicted).sum())
    fp,tn=int((~true&predicted).sum()),int((~true&~predicted).sum())
    recall,specificity=tp/max(tp+fn,1),tn/max(tn+fp,1)
    precision=tp/max(tp+fp,1)
    groups={}
    for kind in sorted({x["kind"] for x in records}):
        select=np.array([x["kind"]==kind for x in records])
        groups[kind]=dict(records=int(select.sum()),predicted_fm=int(predicted[select].sum()),
                          predicted_fm_rate=float(predicted[select].mean()))
    return dict(threshold=float(threshold),accuracy=float(np.mean(predicted==true)),
                balanced_accuracy=(recall+specificity)/2,recall=recall,specificity=specificity,
                precision=precision,f1=2*precision*recall/max(precision+recall,1e-12),
                tp=tp,fn=fn,fp=fp,tn=tn,groups=groups)


def calibrate_binary(records,scores):
    candidates=np.unique(np.r_[np.linspace(.001,.999,300),scores,np.nextafter(scores,np.inf)])
    candidates=candidates[(candidates>0)&(candidates<=1)]
    rows=[binary_metrics(records,scores,t) for t in candidates]
    # Prefer the conventional midpoint on an equally good validation plateau.
    return max(rows,key=lambda r:(r["balanced_accuracy"],r["f1"],-abs(r["threshold"]-.5),r["threshold"])),rows


def predict(model,loader,device,num_classes=4):
    model.eval()
    scores,labels=[],[]
    with torch.inference_mode():
        for inputs,target in loader:
            logits=model(*[x.to(device) for x in inputs])
            assert logits.ndim==2 and logits.shape[1]==num_classes and torch.isfinite(logits).all()
            scores.append(logits.cpu().numpy())
            labels.extend(target.tolist())
    return np.concatenate(scores),np.array(labels,dtype=np.int64)
