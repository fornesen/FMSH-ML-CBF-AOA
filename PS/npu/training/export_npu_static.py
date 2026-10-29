"""Equivalent static batch1 graph; compiler-friendly candidate, not ZG binary."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import torch
from torch import nn

from classification_models import build_model
from classification_runtime import CaptureDataset


class FixedTwelveToEight(nn.Module):
    """Exact adaptive12->8 bins: starts0,1,3,4,6,7,9,10, length2."""
    def __init__(self):
        super().__init__()
        self.pool=nn.AvgPool2d((1,2),stride=(1,1))

    def forward(self,x):
        x=self.pool(x)
        return torch.cat([x[:,:,:,i:i+1] for i in (0,1,3,4,6,7,9,10)],dim=3)


class StaticFourGroup(nn.Module):
    def __init__(self,base):
        super().__init__()
        self.base=base

    def forward(self,tf,spectrum,sequence):
        groups=[]
        for k in range(4):
            a=self.base.tf_project(self.base.tf_encoder(tf[:,k:k+1,:,:]))
            b=self.base.spectrum_encoder(spectrum[:,k:k+1,:,:])
            c=self.base.sequence_encoder(sequence[:,k:k+1,:,:])
            groups.append(torch.cat((a,b,c),dim=1))
        return self.base.head(torch.cat(groups,dim=1))


def main():
    root=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment",type=Path,default=root/"experiments/classifier_array_v3")
    parser.add_argument("--validation-records",type=int,default=24)
    args=parser.parse_args();exp=args.experiment.resolve()
    output=exp/"deployment";output.mkdir(exist_ok=True)
    target=output/"npu_static.pt"
    if target.exists():raise SystemExit("Preserve existing deployment candidate")
    os.environ.update(YOLO_OFFLINE="true",YOLOV5_CONFIG_DIR=str(root/"config/runtime"))
    torch.set_num_threads(8)
    checkpoint_path=exp/"runs/branches_amp/weights/best.pt"
    checkpoint=torch.load(checkpoint_path,map_location="cpu")
    assert checkpoint["variant"]=="branches_amp"
    nc=len(checkpoint["classes"])
    original=build_model("branches_amp",num_classes=nc).eval()
    original.load_state_dict(checkpoint["state_dict"],strict=True)
    base=deepcopy(original)
    with torch.inference_mode():
        assert base.tf_encoder(torch.zeros(1,1,64,640)).shape[-2:]==(2,20)
        assert base.spectrum_encoder.layers[:9](torch.zeros(1,1,1,640)).shape[-2:]==(1,160)
        assert base.sequence_encoder.layers[:9](torch.zeros(1,1,1,48)).shape[-2:]==(1,12)
        x=torch.randn(2,64,1,12)
        torch.testing.assert_close(FixedTwelveToEight()(x),nn.AdaptiveAvgPool2d((1,8))(x),rtol=1e-6,atol=1e-6)
    base.tf_project[0]=nn.AvgPool2d((2,20),stride=(2,20))
    base.spectrum_encoder.layers[9]=nn.AvgPool2d((1,10),stride=(1,10))
    base.sequence_encoder.layers[9]=FixedTwelveToEight()
    static=StaticFourGroup(base).eval()
    rows=[json.loads(x) for x in (exp/"manifest.jsonl").read_text().splitlines()]
    selected=[]
    # Select every validation kind, including newly appended modulation classes.
    kinds=sorted({x["kind"] for x in rows if x["split"]=="val"})
    for kind in kinds:
        candidates=[x for x in rows if x["split"]=="val" and x["kind"]==kind]
        if kind=="am":candidates.sort(key=lambda x:x.get("message_frequency_hz",x.get('station_scenes',[{}])[0].get('message_frequency_hz',0)))
        count=max(1,args.validation_records//len(kinds))
        if kind=="pm":
            candidates.sort(key=lambda x:x['station_scenes'][0]['pm_frequency_hz'])
            selected.extend(candidates[::max(1,len(candidates)//count)][:count])
        else:selected.extend(candidates[:count])
    assert {x['class_index'] for x in selected}==set(range(nc)),"Export equivalence must cover every class"
    data=CaptureDataset(selected,"branches_amp")
    sample=tuple(x.unsqueeze(0).squeeze(2) for x in data[0][0])
    with torch.inference_mode():traced=torch.jit.trace(static,sample,strict=False)
    torch.jit.save(traced,str(target))
    loaded=torch.jit.load(str(target),map_location="cpu").eval()
    error=0.;probability_error=0.;agree=0
    with torch.inference_mode():
        for inputs,_ in data:
            old_inputs=tuple(x.unsqueeze(0) for x in inputs)
            new_inputs=tuple(x.squeeze(2) for x in old_inputs)
            expected=original(*old_inputs);actual=loaded(*new_inputs)
            # The original encoder batches four rounds; the static wrapper runs
            # four batch1 encoders. Different FP32 convolution/GEMM reductions
            # can accumulate near-zero logit cancellation error. Bound absolute
            # logits and softmax error, and also require identical predictions.
            torch.testing.assert_close(expected,actual,rtol=2e-5,atol=1e-3)
            probability_error=max(probability_error,float((expected.softmax(1)-actual.softmax(1)).abs().max()))
            error=max(error,float((expected-actual).abs().max()))
            agree+=int(expected.argmax(1).item()==actual.argmax(1).item())
    assert agree==len(selected)
    assert probability_error<=1e-4,probability_error
    graph=str(loaded.inlined_graph)
    assert "adaptive_avg_pool2d" not in graph
    metadata=dict(variant="branches_amp",classes=checkpoint["classes"],output_shape=[1,nc],
                  output="classification logits; no anchors/NMS",input_names=["tf","spectrum","sequence"],
                  input_shapes=[list(x.shape) for x in sample],input_layout="NCHW",logical_input_form="npu_static_4d",
                  group_axis=1,groups_are_sliced_before_encoding=True,internal_encoder_batch=1,
                  input_float_scale="uint8 feature values divided by255 ONCE before input",
                  preprocessing=json.loads((exp/"preprocess.json").read_text()),
                  weights_sha256=hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
                  export_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                  pytorch_equivalence_records=len(selected),maximum_absolute_export_error=error,
                  maximum_probability_export_error=probability_error,
                  logit_equivalence_tolerance=dict(rtol=2e-5,atol=1e-3),probability_equivalence_atol=1e-4,
                  class_predictions_identical=agree,no_adaptive_pool=True,
                  npu_compiler_validated=False,quantization_validated=False,npu_board_validated=False,
                  compile_notes="Slice/Concat/AvgPool/Linear/SiLU and 3-input graph still require actual Icraft zhuge compile")
    target.with_suffix(".json").write_text(json.dumps(metadata,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in metadata.items() if k!="preprocessing"},indent=2),flush=True)


if __name__=="__main__":main()
