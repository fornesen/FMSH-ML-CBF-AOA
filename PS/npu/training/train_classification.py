"""No image augmentation; class-balanced CE directly trains four explicit labels."""
import argparse
from copy import deepcopy
import csv
import json
import math
import os
from pathlib import Path
import random
import time

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG",":4096:8")
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from classification_features import VARIANTS
from classification_models import build_model
from classification_runtime import CaptureDataset,class_metrics,predict


def main():
    root=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser()
    parser.add_argument("--experiment",type=Path,default=root/"experiments/classifier_multibranch_v1")
    parser.add_argument("--variant",choices=VARIANTS,required=True)
    parser.add_argument("--epochs",type=int,default=50)
    parser.add_argument("--init-checkpoint",type=Path)
    parser.add_argument("--expand-classifier",action="store_true")
    parser.add_argument("--seed",type=int,default=20261013)
    parser.add_argument("--batch-size",type=int,default=8)
    parser.add_argument("--backbone-lr",type=float,default=2e-4)
    parser.add_argument("--head-lr",type=float,default=1e-3)
    parser.add_argument("--patience",type=int,default=12)
    parser.add_argument("--amp-init-scale",type=float,default=1024)
    args=parser.parse_args()
    experiment=args.experiment.resolve()
    initial_path=args.init_checkpoint.resolve() if args.init_checkpoint else None
    output=experiment/"runs"/args.variant
    if output.exists():
        raise SystemExit("Preserve existing run; choose another experiment")
    output.mkdir(parents=True)
    (output/"weights").mkdir()
    seed=args.seed
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(8)
    torch.backends.cudnn.benchmark=False
    torch.backends.cudnn.deterministic=True
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    os.environ.update(YOLO_OFFLINE="true",MPLBACKEND="Agg",
                      YOLOV5_CONFIG_DIR=str(root/"config/runtime"))
    rows=[json.loads(x) for x in (experiment/"manifest.jsonl").read_text().splitlines()]
    training=[x for x in rows if x["split"]=="train"]
    validation=[x for x in rows if x["split"]=="val"]
    contract_path=experiment/"preprocess.json"
    classes=json.loads(contract_path.read_text())["classes"] if contract_path.exists() else ["FM","AM","CW","noise"]
    nc=len(classes)
    assert nc>=2 and sorted(set(x["class_index"] for x in training))==list(range(nc))
    train_data=CaptureDataset(training,args.variant)
    val_data=CaptureDataset(validation,args.variant)
    generator=torch.Generator().manual_seed(seed)
    train_loader=DataLoader(train_data,batch_size=args.batch_size,shuffle=True,num_workers=0,generator=generator)
    val_loader=DataLoader(val_data,batch_size=args.batch_size,shuffle=False,num_workers=0)
    model=build_model(args.variant,num_classes=nc).to("cuda:0")
    initial=None
    if args.init_checkpoint:
        import hashlib
        initial=torch.load(initial_path,map_location="cpu")
        assert initial["variant"]==args.variant
        old_classes=initial["classes"]
        state=initial["state_dict"]
        expanded=False
        if old_classes!=classes:
            assert args.expand_classifier and args.variant!="rgb_classifier"
            assert classes[:len(old_classes)]==old_classes and len(classes)>len(old_classes)
            state=dict(state)
            for key in ("head.3.weight","head.3.bias"):
                value=model.state_dict()[key].detach().cpu().clone()
                value[:len(old_classes)].copy_(state[key])
                state[key]=value
            expanded=True
        model.load_state_dict(state,strict=True)
        initial=dict(path=str(initial_path),sha256=hashlib.sha256(initial_path.read_bytes()).hexdigest(),epoch=initial["epoch"],
                     classes=old_classes,classifier_expanded=expanded)
    for parameter in model.parameters():
        parameter.requires_grad=True
    ema=deepcopy(model).eval()
    for p in ema.parameters():p.requires_grad=False
    backbone=list((model.model[:9] if args.variant=="rgb_classifier" else model.tf_encoder).parameters())
    backbone_ids={id(x) for x in backbone}
    new=[p for p in model.parameters() if id(p) not in backbone_ids]
    optimizer=torch.optim.AdamW([dict(params=backbone,lr=args.backbone_lr),dict(params=new,lr=args.head_lr)],weight_decay=1e-4)
    counts=np.bincount([x["class_index"] for x in training],minlength=nc)
    weights=len(training)/(nc*counts)
    criterion=nn.CrossEntropyLoss(weight=torch.tensor(weights,dtype=torch.float32,device="cuda:0"))
    scaler=torch.cuda.amp.GradScaler(init_scale=args.amp_init_scale)
    config=dict(variant=args.variant,classes=classes,training_records=len(training),
                validation_records=len(validation),class_counts=counts.tolist(),class_weights=weights.tolist(),
                optimizer="AdamW",backbone_lr=args.backbone_lr,new_layers_lr=args.head_lr,weight_decay=.0001,
                batch_size=args.batch_size,max_epochs=args.epochs,early_stop_patience=args.patience,seed=seed,
                augmentation=False,ema_decay=.99,best_selection="validation macro F1, then accuracy, then lower CE",
                pretrained="continued checkpoint with explicit class mapping" if initial else "local COCO YOLOv5s backbone",initial_checkpoint=initial,parameter_count=sum(p.numel() for p in model.parameters()),
                torch=torch.__version__,cuda=torch.version.cuda,gpu=torch.cuda.get_device_name(0),
                loss="explicit inverse-frequency weighted CrossEntropyLoss",amp_initial_scale=args.amp_init_scale)
    (output/"config.json").write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(config),flush=True)
    best=(-1,-1,-float("inf"));best_epoch=0;started=time.monotonic();gradient_verified=False
    with (output/"results.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=["epoch","train_loss","val_loss","accuracy","balanced_accuracy","macro_f1","elapsed_seconds"])
        writer.writeheader()
        for epoch in range(1,args.epochs+1):
            model.train()
            factor=.05+.95*(1+math.cos(math.pi*(epoch-1)/args.epochs))/2
            if epoch<=2:factor*=epoch/2
            optimizer.param_groups[0]["lr"]=args.backbone_lr*factor
            optimizer.param_groups[1]["lr"]=args.head_lr*factor
            total_loss=0.0
            for batch_index,(inputs,target) in enumerate(train_loader):
                inputs=[x.to("cuda:0") for x in inputs]
                target=target.to("cuda:0")
                optimizer.zero_grad(set_to_none=True)
                with torch.cuda.amp.autocast():
                    logits=model(*inputs)
                    loss=criterion(logits,target)
                assert torch.isfinite(loss)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                finite_gradients=all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in model.parameters()) if not gradient_verified else True
                torch.nn.utils.clip_grad_norm_(model.parameters(),5.0)
                if not gradient_verified and finite_gradients:
                    modules={"backbone":model.model[:9],"head":model.model[-1]} if args.variant=="rgb_classifier" else {
                        "tf":model.tf_encoder,"spectrum":model.spectrum_encoder,"sequence":model.sequence_encoder,"head":model.head}
                    gradient={name:any(p.grad is not None and torch.isfinite(p.grad).all() and float(p.grad.abs().sum())>0
                                       for p in module.parameters()) for name,module in modules.items()}
                    assert all(gradient.values()),gradient
                    (output/"gradient_checks.json").write_text(json.dumps(gradient,indent=2),encoding="utf-8")
                    gradient_verified=True
                elif not gradient_verified:
                    print("AMP initial overflow; GradScaler will skip update and reduce scale",float(scaler.get_scale()),flush=True)
                scaler.step(optimizer);scaler.update()
                with torch.no_grad():
                    current_state=model.state_dict()
                    for key,value in ema.state_dict().items():
                        source=current_state[key]
                        if value.is_floating_point():value.mul_(.99).add_(source,alpha=.01)
                        else:value.copy_(source)
                total_loss+=float(loss)*len(target)
            values,labels=predict(ema,val_loader,"cuda:0",num_classes=nc)
            measured=class_metrics(labels,values.argmax(1),classes=classes)
            val_loss=float(criterion(torch.from_numpy(values).to("cuda:0"),torch.from_numpy(labels).to("cuda:0")))
            score=(measured["macro_f1"],measured["accuracy"],-val_loss)
            if score>best:
                best=score;best_epoch=epoch
                state={key:value.detach().cpu().clone() for key,value in ema.state_dict().items()}
                torch.save(dict(variant=args.variant,epoch=epoch,classes=config["classes"],state_dict=state,
                                validation=measured,config=config),output/"weights/best.pt")
            row=dict(epoch=epoch,train_loss=total_loss/len(training),val_loss=val_loss,
                     accuracy=measured["accuracy"],balanced_accuracy=measured["balanced_accuracy"],
                     macro_f1=measured["macro_f1"],elapsed_seconds=time.monotonic()-started)
            writer.writerow(row);handle.flush()
            print("Epoch",epoch,"macroF1",round(measured["macro_f1"],4),"accuracy",round(measured["accuracy"],4),
                  "best",best_epoch,flush=True)
            if epoch-best_epoch>=args.patience:
                print("Validation early stop",flush=True);break
    assert gradient_verified,"No finite gradients verified in any branch"
    torch.save(dict(variant=args.variant,epoch=epoch,classes=config["classes"],
                    state_dict={k:v.detach().cpu().clone() for k,v in ema.state_dict().items()},config=config),output/"weights/last.pt")
    (output/"completion.json").write_text(json.dumps(dict(epochs=epoch,best_epoch=best_epoch,
        elapsed_seconds=time.monotonic()-started,best_validation_macro_f1=best[0]),indent=2),encoding="utf-8")
    print("Completed training",args.variant,flush=True)


if __name__=="__main__":main()
