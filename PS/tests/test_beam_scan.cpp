#include "beam_scan.hpp"
#include "beam_scan_verify.hpp"
#include <cassert>
#include <iostream>

namespace aoa_app {
double bin_hz(std::size_t b,double fs) {return (b>=fft_length/2?double(b)-fft_length:double(b))*fs/4/fft_length;}
bool slot_valid(const std::uint8_t* p) {
    return std::all_of(p+10,p+16,[](auto v){return v==0;}) && std::all_of(p+24,p+64,[](auto v){return v==0;});
}
}
int main() {
    using namespace aoa_app;
    Options o;o.processing_mode="scan6";o.rf_hz=445e6;o.band_min=-550e3;o.band_max=-450e3;
    o.ml_startup_samples=64;o.ml_fft_hann=true;
    const std::array<double,6> x{{-1.2823613348729794,-.9361806674364898,-.59,.59,.9361806674364898,1.2823613348729794}};
    constexpr std::size_t bin=4014;constexpr double angle=37;
    const double k=2*pi*o.rf_hz/light_speed;
    FftData f;f.valid.assign(fft_records,1);
    for(std::size_t m=0;m<channels;++m) {
        o.antenna[m].x=x[m];o.antenna[m].correction=std::polar(.5+m*.3,m*.17);
    }
    for(std::size_t t=0;t<4;++t) {
        std::vector<AoaComplex> frame(fft_records,{.01,0});
        for(std::size_t m=0;m<channels;++m) {
            // Corrected input contains the amplitude calibration. The real PL
            // phase rotator must ignore that magnitude and retain its phase.
            const auto raw=std::polar(100.0,k*x[m]*std::sin(angle*pi/180)-std::arg(o.antenna[m].correction));
            frame[m*fft_length+bin]=raw*o.antenna[m].correction*std::polar(1.0,t*1.3);
        }
        f.snapshots.push_back(frame);
    }
    f.corrected=f.snapshots.back();
    Analysis a;a.direction_frames=4;a.bins={bin};analyze_beam_scan(o,f,a,{});
    const auto& s=a.beam_scan;
    assert(s.valid && s.frame_count==4 && s.angles_deg.size()==161);
    assert(s.peak_angle_deg==angle && std::abs(s.peak_normalized_power-6)<3e-6);
    assert(s.candidates.front().angle_deg==angle && s.candidates.size()<=3);
    assert(beam_scan_packet(s).at("verification").at("checks").empty());
    // Four independent common phases must not cancel as they would in IQ averaging.
    auto single=f;single.snapshots.clear();Analysis b;b.direction_frames=1;b.bins={bin};analyze_beam_scan(o,single,b,{});
    assert(std::abs(b.beam_scan.peak_normalized_power-s.peak_normalized_power)<1e-12);
    // Equivalent scan has a Cauchy-Schwarz bound of six for unit phase weights.
    for(auto p:s.normalized_power) assert(p<=6.000001 && p>=0);
    // Constant per-bin power is noise-like; it must not produce a qualified bearing.
    for(auto& frame:f.snapshots) std::fill(frame.begin(),frame.end(),AoaComplex(1,0));
    Analysis noise;noise.direction_frames=4;noise.bins={bin};analyze_beam_scan(o,f,noise,{});
    assert(!noise.beam_scan.valid && noise.beam_scan.local_snr_db<o.scan_min_snr_db);
    // The wrong carrier is diagnosed explicitly rather than steering target-band noise.
    for(auto& frame:f.snapshots) for(std::size_t m=0;m<6;++m) frame[m*fft_length+3000]={10000,0};
    Analysis wrong;wrong.direction_frames=4;wrong.bins={bin};analyze_beam_scan(o,f,wrong,{});
    assert(wrong.beam_scan.status=="band_mismatch");
    std::vector<std::uint8_t> pha(pha_bytes);
    auto put=[&](std::size_t offset,std::int32_t value){for(unsigned j=0;j<4;++j) pha[offset+j]=std::uint32_t(value)>>(8*j);};
    for(std::size_t i=0;i<64;++i) {put(i*8,1000000000);put(i*8+4,-1000000000);}
    assert(scan_pha_power(o,pha,{0,bin})==0); // startup never enters check FFT
    for(std::size_t i=64;i<4096;++i) put(i*8,100);
    assert(std::abs(scan_pha_power(o,pha,{0})-10000)<1e-8);
    std::vector<std::uint8_t> slots(fft_bytes,0);
    CaptureState cs;cs.mode="hardware";cs.complete=true;
    inspect_scan_pha(slots,cs);
    assert(cs.pha_written_samples==4096 && cs.pha_format_errors==0);
    assert(pha_frame(slots,cs).size()==pha_bytes);
    slots[pha_samples*slot_bytes+pha_offset]=1;
    inspect_scan_pha(slots,cs);assert(cs.pha_format_errors==1);
    bool rejected=false;try {pha_frame(slots,cs);} catch(const std::runtime_error&) {rejected=true;}
    assert(rejected);
    std::cout<<"PASS beam scan: four-frame power, exact Q23, phase calibration, noise/carrier gates, startup\n";
}
