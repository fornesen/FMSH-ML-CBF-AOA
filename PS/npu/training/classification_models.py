"""Explicit classification logits and independent modality encoders."""
from copy import deepcopy
from pathlib import Path
import os
import sys

import torch
from torch import nn

SOURCE = Path(os.environ.get('YOLOV5_ICRAFT_ROOT', str(Path(__file__).resolve().parents[3] / 'dependencies/yolov5_icraft')))


def install_source():
    # The supplied package's os.path.relpath needs CWD on its own drive.
    os.chdir(SOURCE)
    if str(SOURCE) not in sys.path:
        sys.path.insert(0,str(SOURCE))


class ThinEncoder(nn.Module):
    def __init__(self,channels,pool_width):
        super().__init__()
        self.layers=nn.Sequential(
            nn.Conv2d(channels,16,(1,7),padding=(0,3)),nn.BatchNorm2d(16),nn.ReLU(),
            nn.Conv2d(16,32,(1,5),stride=(1,2),padding=(0,2)),nn.BatchNorm2d(32),nn.ReLU(),
            nn.Conv2d(32,64,(1,3),stride=(1,2),padding=(0,1)),nn.BatchNorm2d(64),nn.ReLU(),
            nn.AdaptiveAvgPool2d((1,pool_width)),nn.Flatten(),
            nn.Linear(64*pool_width,64),nn.ReLU())

    def forward(self,x):
        return self.layers(x)


class MultiBranchClassifier(nn.Module):
    def __init__(self,detector,with_phase,num_classes=4):
        super().__init__()
        self.with_phase=with_phase
        self.tf_encoder=nn.Sequential(*deepcopy(list(detector.model)[:9]))
        original=self.tf_encoder[0].conv
        replacement=nn.Conv2d(1,original.out_channels,original.kernel_size,
                              original.stride,original.padding,bias=original.bias is not None)
        with torch.no_grad():
            # Exact response to gray duplicated into RGB uses SUM, not mean.
            replacement.weight.copy_(original.weight.sum(1,keepdim=True))
            if original.bias is not None:
                replacement.bias.copy_(original.bias)
        self.tf_encoder[0].conv=replacement
        channel=detector.model[9].cv1.conv.in_channels
        self.tf_project=nn.Sequential(nn.AdaptiveAvgPool2d(1),nn.Flatten(),nn.Linear(channel,128),nn.ReLU())
        self.spectrum_encoder=ThinEncoder(1,16)
        self.sequence_encoder=ThinEncoder(2 if with_phase else 1,8)
        self.head=nn.Sequential(nn.Linear(4*256,128),nn.ReLU(),nn.Dropout(0.1),nn.Linear(128,num_classes))

    def forward(self,tf,spectrum,sequence):
        batch=tf.shape[0]
        tf=tf.reshape(batch*4,1,64,640)
        spectrum=spectrum.reshape(batch*4,1,1,640)
        sequence=sequence.reshape(batch*4,2 if self.with_phase else 1,1,48)
        a=self.tf_project(self.tf_encoder(tf))
        b=self.spectrum_encoder(spectrum)
        c=self.sequence_encoder(sequence)
        joined=torch.cat([a,b,c],1).reshape(batch,4*256)
        return self.head(joined)


def build_model(variant,num_classes=4):
    install_source()
    from models.experimental import attempt_load
    from models.yolo import ClassificationModel
    detector=attempt_load(str(SOURCE/"weights/yolov5s.pt"),device=torch.device("cpu"),fuse=False)
    if variant=="rgb_classifier":
        model=ClassificationModel(model=detector,nc=num_classes,cutoff=10)
        model.model[-1].drop.p=0.1
        return model
    if variant in ("branches_amp","branches_phase"):
        return MultiBranchClassifier(detector,with_phase=variant=="branches_phase",num_classes=num_classes)
    raise ValueError(variant)
