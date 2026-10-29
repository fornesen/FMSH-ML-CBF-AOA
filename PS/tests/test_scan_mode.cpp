#ifdef NDEBUG
#undef NDEBUG
#endif
#include "scan_fusion.hpp"
#include "console_aoa.hpp"
#include <cassert>
#include <iostream>
#include <limits>

namespace aoa_app {
double bin_hz(std::size_t bin,double rate) {
    return (bin>=fft_length/2?double(bin)-fft_length:double(bin))*rate/4/fft_length;
}
std::uint32_t dds_word(const Options&) {return 0;}
}
namespace {
template<class Action> void rejects(Action action) {
    bool rejected=false;
    try {action();} catch(const std::invalid_argument&) {rejected=true;}
    assert(rejected);
}
}
int main(int argc,char** argv) {
    using namespace aoa_app;
    const double nan=std::numeric_limits<double>::quiet_NaN();
    Options o;o.scan_ml_weight=.2;
    // V1.2 integration: scan weight controls remain accepted, while the result
    // is always the selected existing ML path from the same four snapshots.
    o.processing_mode="scan6";o.rf_hz=445e6;o.bins={4014};o.scan_verify=false;
    o.band_min=-550e3;o.band_max=-450e3;
    const std::array<double,6> x{{-1.2823613348729794,-.9361806674364898,-.59,.59,.9361806674364898,1.2823613348729794}};
    for(std::size_t ch=0;ch<6;++ch)o.antenna[ch].x=x[ch];
    FftData fft;fft.valid.assign(fft_records,1);
    for(std::size_t t=0;t<4;++t) {
        std::vector<AoaComplex> frame(fft_records,{.001,0});
        for(std::size_t ch=0;ch<6;++ch) {
            AoaComplex y{};
            for(const auto path:std::array<std::pair<double,double>,3>{{{37,100},{-20,65},{65,35}}})
                y+=std::polar(path.second,2*pi*o.rf_hz/light_speed*x[ch]*std::sin(path.first*pi/180)+t*1.3);
            frame[ch*fft_length+4014]=y+AoaComplex(.001*(ch+1),.001*(t+1));
        }
        fft.snapshots.push_back(std::move(frame));
    }
    fft.corrected=fft.snapshots.back();fft.raw=fft.corrected;
    const auto before=fft;
    const auto fused=analyze(o,fft,{});
    auto original_options=o;original_options.processing_mode="angle6";
    const auto original=analyze(original_options,fft,{});
    auto scan_options=o;scan_options.scan_ml_weight=0;
    const auto scan_only=analyze(scan_options,fft,{});
    assert(original.selection.accepted && original.selection.path_index>=0);
    assert(fused.v12.valid && fused.v12.angle_deg==original.v12.angle_deg);
    assert(fused.joint.angle==original.joint.angle);
    assert(!fused.beam_scan.fusion.enabled);
    assert(fused.v12.angle_deg==scan_only.v12.angle_deg);
    assert(fused.beam_scan.peak_angle_deg==scan_only.beam_scan.peak_angle_deg);
    assert(fused.beam_scan.normalized_power==scan_only.beam_scan.normalized_power);
    assert(fused.beam_scan.relative_db==scan_only.beam_scan.relative_db);
    assert(fft.snapshots==before.snapshots && fft.corrected==before.corrected &&
           fft.raw==before.raw && fft.valid==before.valid);
    for(const auto bad:std::array<double,3>{{-.01,1.01,nan}}) {
        auto invalid=o;invalid.scan_ml_weight=bad;
        rejects([&]{analyze(invalid,fft,{});});
    }
    // Frozen four-frame regression with different ML and scan directions. This
    // prevents an accidental scan-only implementation from passing integration.
    const std::array<std::array<AoaComplex,6>,4> distinct{{
        {{{-.5515726940102774,.46144897308476535},{.7485087557579962,.20975107769626672},
          {-.28289761193279334,-.8500956611870334},{-1.1156522426353495,-.29476861429162265},
          {.2926666389219868,1.1184475126154776},{.9568564200111843,-.8359329128818406}}},
        {{{.5007917986364474,.4398309660783856},{.04748889071866372,-.7436033616175326},
          {-.6784742737054681,.4437944691834976},{-.09534126337765021,1.1077415879891144},
          {1.1327614699015962,-.5058492925252744},{-.9921798730405216,-.7655037175767591}}},
        {{{.45044250610049696,.546691904503833},{.12806347059564333,-.681538156003615},
          {-.8105043168218007,.3765235444857604},{-.22909127119749956,1.1820171031774935},
          {1.0768378096673126,-.304866833464093},{-.9173830010373395,-.9124674203046707}}},
        {{{.40656506417299976,.6107283963144488},{.27769459592706297,-.7607617967957648},
          {-.812305093414625,.11436616376425067},{-.3503113197397319,1.027976891744024},
          {1.122817428622114,-.1429224540151735},{-.7225966520707392,-.9513284728238371}}}
    }};
    for(std::size_t t=0;t<4;++t)for(std::size_t ch=0;ch<6;++ch)
        fft.snapshots[t][ch*fft_length+4014]=distinct[t][ch];
    fft.raw=fft.corrected=fft.snapshots.back();
    const auto distinct_before=fft;
    const auto nontrivial=analyze(o,fft,{});
    const auto nontrivial_ml=analyze(original_options,fft,{});
    const auto nontrivial_scan=analyze(scan_options,fft,{});
    assert(nontrivial.v12.valid && nontrivial.v12.angle_deg==nontrivial_ml.v12.angle_deg);
    assert(nontrivial.v12.scan.peak_angle_deg==nontrivial_scan.v12.scan.peak_angle_deg);
    assert(std::abs(nontrivial.v12.angle_deg-nontrivial.v12.scan.peak_angle_deg)>.01);
    assert(!nontrivial.beam_scan.fusion.enabled);
    assert(nontrivial.beam_scan.normalized_power==nontrivial_scan.beam_scan.normalized_power);
    assert(fft.snapshots==distinct_before.snapshots && fft.corrected==distinct_before.corrected &&
           fft.raw==distinct_before.raw && fft.valid==distinct_before.valid);
    if(argc>1) {
        CaptureState capture;CaptureBatch batch;batch.run_id="offline_scan_mode_contract";batch.completed=4;
        capture.mode="offline";
        std::ofstream out(argv[1]);out<<console_packet(o,capture,batch,nontrivial).dump(2)<<'\n';
        if(!out)throw std::runtime_error("Cannot write scan-mode packet fixture");
    }
    std::cout<<"PASS selected-ML scan-mode compatibility, unchanged FFT/Q23 scan, no temporal state\n";
}
