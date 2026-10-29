"""Current-batch scan/ML blend contract, including unavailable/error packets."""
import copy
import json
import math
from pathlib import Path
import sys
import shutil
import subprocess
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import decode, encode, validate


def empty_model():
    return dict(angles_deg=[], amplitudes=[], condition=None, fused_residual_ratio=None,
                raw_residual_ratio=None, latest_residual_ratio=None, ambiguous=False,
                selection_accepted=False, selection_status="not_run", path_index=-1, model_paths=0)


def packet(status="blended"):
    p = json.loads((Path(__file__).parent / 'fixtures/direct_scan_cpp.json').read_text(encoding='utf-8'))
    scan = p['beam_scan']
    angle = scan['peak_angle_deg']
    model = dict(angles_deg=[-34., -18., 12.], amplitudes=[.3, 1., .4], condition=4.5,
                 fused_residual_ratio=.03, raw_residual_ratio=.04, latest_residual_ratio=.045,
                 ambiguous=False, selection_accepted=True, selection_status="ok", path_index=1, model_paths=3)
    f = dict(enabled=True, status="blended", domain="sine", requested_ml_weight=.2,
             effective_ml_weight=.2, valid=True, ml_available=True, ml_qualified=True,
             scan_angle_deg=angle, ml_angle_deg=-18.,
             angle_deg=math.degrees(math.asin(.8 * math.sin(math.radians(angle)) + .2 * math.sin(math.radians(-18.)))),
             ml_elapsed_ms=17., ml_model=model)
    if status == 'scan_only':
        f.update(status=status, effective_ml_weight=0., ml_available=False, ml_qualified=False,
                 ml_angle_deg=None, angle_deg=angle, ml_model=empty_model())
    elif status == 'disabled':
        f.update(enabled=False, status=status, requested_ml_weight=0., effective_ml_weight=0.,
                 valid=False, ml_available=False, ml_qualified=False, scan_angle_deg=None,
                 ml_angle_deg=None, angle_deg=None, ml_elapsed_ms=0., ml_model=empty_model())
    elif status == 'unavailable':
        p['status'] = 'no_signal'
        scan.update(valid=False, status='no_signal')
        f.update(status=status, effective_ml_weight=0., valid=False, ml_available=False,
                 ml_qualified=False, scan_angle_deg=None, ml_angle_deg=None, angle_deg=None,
                 ml_model=empty_model())
    scan['fusion'] = f
    return p


class ScanFusionProtocolTests(unittest.TestCase):
    def reject(self, transform):
        p = packet()
        transform(p, p['beam_scan']['fusion'])
        with self.assertRaises(ValueError):
            validate(p)

    def test_optional_legacy_scan_packet(self):
        p = packet()
        p['beam_scan'].pop('fusion')
        validate(p)

    def test_blended_round_trip(self):
        p = packet()
        validate(p)
        self.assertEqual(decode(encode(p)[8:]), p)

    def test_available_ml_need_not_be_quality_qualified(self):
        p = packet()
        p['beam_scan']['fusion']['ml_qualified'] = False
        p['beam_scan']['fusion']['ml_model']['ambiguous'] = True
        validate(p)

    def test_scan_only_fallback(self):
        validate(packet('scan_only'))

    def test_disabled_fusion(self):
        validate(packet('disabled'))

    def test_no_signal_fusion_is_not_transport_failure(self):
        validate(packet('unavailable'))

    def test_partial_capture_has_no_stale_fusion_angle(self):
        p = packet('unavailable')
        p.update(status='error', complete=False, direction_frames=0, direction_rounds=[])
        p['beam_scan'].update(status='not_run', frame_count=0,
            angles_deg=[], normalized_power=[], relative_db=[], candidates=[])
        p['beam_scan']['verification'].update(enabled=False, status='not_run', checks=[])
        validate(p)

    def test_rejects_wrong_formula_and_source_angle(self):
        for key in ('angle_deg', 'scan_angle_deg', 'ml_angle_deg'):
            with self.subTest(key=key):
                self.reject(lambda p, f: f.update({key: f[key] + .1}))

    def test_rejects_invalid_weights_and_nonfinite_values(self):
        for key in ('requested_ml_weight', 'effective_ml_weight'):
            for value in (-.1, 1.1, float('nan'), float('inf'), True):
                with self.subTest(key=key, value=value):
                    self.reject(lambda p, f: f.update({key: value}))
        self.reject(lambda p, f: f.update(effective_ml_weight=.3))
        self.reject(lambda p, f: f.update(ml_elapsed_ms=-1))

    def test_rejects_nonfinite_or_out_of_range_angles(self):
        for key in ('scan_angle_deg', 'ml_angle_deg', 'angle_deg'):
            for value in (float('nan'), float('inf'), 91, -91, None):
                with self.subTest(key=key, value=value):
                    self.reject(lambda p, f: f.update({key: value}))

    def test_rejects_unavailable_or_disabled_success_claims(self):
        self.reject(lambda p, f: p['beam_scan'].update(valid=False, status='no_signal'))
        self.reject(lambda p, f: f.update(enabled=False))
        self.reject(lambda p, f: f.update(valid=False))
        self.reject(lambda p, f: f.update(status='scan_only'))
        self.reject(lambda p, f: f.update(ml_available=False))
        p = packet('disabled'); p['beam_scan']['fusion']['angle_deg'] = 0
        with self.assertRaises(ValueError):validate(p)

    def test_rejects_inconsistent_ml_model(self):
        changes = [dict(path_index=3), dict(path_index=-1), dict(model_paths=2),
                   dict(selection_accepted=False), dict(amplitudes=[1.]),
                   dict(amplitudes=[-.1, 1., .4]), dict(condition=float('nan')),
                   dict(raw_residual_ratio=-.1), dict(latest_residual_ratio=True),
                   dict(selection_status='x' * 513)]
        for change in changes:
            with self.subTest(change=change):
                self.reject(lambda p, f: f['ml_model'].update(change))

    def test_only_qualified_ml_cannot_be_ambiguous(self):
        self.reject(lambda p, f: f['ml_model'].update(ambiguous=True))

    def test_endpoint_weights_are_valid(self):
        for w in (0., 1.):
            p = packet(); f = p['beam_scan']['fusion']
            f.update(requested_ml_weight=w, effective_ml_weight=w,
                     angle_deg=f['scan_angle_deg'] if w == 0 else f['ml_angle_deg'])
            validate(p)

    @unittest.skipUnless(shutil.which('node'), 'UI helper check requires Node.js')
    def test_automatic_labels_and_manual_peak_semantics(self):
        app = Path(__file__).resolve().parents[1] / 'web' / 'app.js'
        script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const app=fs.readFileSync(process.argv[1],'utf8');
function code(name){const start=app.indexOf('function '+name+'(');assert.ok(start>=0);
  const end=app.indexOf('\nfunction ',start+1);return app.slice(start,end<0?app.length:end);}
const controls={'scan6-selection':{},'beam-mode':{},'beam-angle':{}};
const c={scanCandidates:()=>[{angle_deg:-41},{angle_deg:12}],$:(id)=>controls[id],
  renderScan6:()=>{},renderBeamControls:()=>{}};
vm.createContext(c);
vm.runInContext(code('scanAutomaticLabel')+'\n'+code('selectScanCandidate'),c);
assert.equal(c.scanAutomaticLabel({}),'自动：最高扫描峰');
assert.equal(c.scanAutomaticLabel({fusion:{enabled:false}}),'自动：最高扫描峰');
assert.equal(c.scanAutomaticLabel({fusion:{enabled:true,valid:true,ml_available:true}}),'自动：融合方向');
assert.equal(c.scanAutomaticLabel({fusion:{enabled:true,valid:true,ml_available:false}}),'自动：扫描方向（ML不可用）');
assert.equal(c.scanAutomaticLabel({fusion:{enabled:true,valid:false,ml_available:false}}),'自动：融合方向（等待有效数据）');
c.selectScanCandidate(0);
assert.equal(controls['beam-angle'].value,'-41');
assert.equal(controls['beam-mode'].value,'manual');
c.selectScanCandidate(1);
assert.equal(controls['beam-angle'].value,'12');
"""
        result = subprocess.run([shutil.which('node'), '-e', script, str(app)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
