#ifdef NDEBUG
#undef NDEBUG
#endif
#include "batch_covariance.hpp"
#include "capture_mode.hpp"
#include "joint_quality.hpp"
#include <cassert>
#include <iostream>

int main() {
    using namespace aoa_app;
    Options o;o.ipc_enabled=false;schedule_capture_rounds(o);assert(o.capture_rounds==4);
    o.ipc_enabled=true;schedule_capture_rounds(o);assert(o.capture_rounds==4);
    o.fft_sources={"legacy"};o.capture_rounds=1;schedule_capture_rounds(o);assert(o.capture_rounds==1);
    SpatialVector y{};for(std::size_t c=0;c<6;++c) y[c]=std::polar(1.0+.1*c,.3*c);
    std::vector<SpatialVector> frames(4,y);
    for(std::size_t t=0;t<4;++t) for(auto& z:frames[t]) z*=std::polar(1.0+t,2.0*t);
    double concentration=0;auto fused=covariance_observation(frames,concentration);
    assert(std::abs(concentration-1)<1e-12);
    for(std::size_t c=0;c<6;++c) assert(std::abs(fused[c]-frames.back()[c])<1e-11);
    bool rejected=false;
    try {covariance_observation(std::vector<SpatialVector>(4),concentration);} catch(...) {rejected=true;}
    assert(rejected);
    FftData fft;fft.corrected.resize(fft_records);fft.snapshots.assign(3,fft.corrected);
    rejected=false;try {fuse_selected_bins(fft,{0},concentration);} catch(...) {rejected=true;}
    assert(rejected);
    // Raw validation catches a moving/newest observation despite a plausible fused fit.
    o.processing_mode="angle6";o.rf_hz=445e6;
    for(std::size_t ch=0;ch<6;++ch) o.antenna[ch].x=(double(ch)-2.5)*.34;
    fft.snapshots.assign(4,std::vector<AoaComplex>(fft_records));
    for(std::size_t t=0;t<4;++t) for(std::size_t ch=0;ch<6;++ch)
        fft.snapshots[t][ch*fft_length]=std::polar(1.0,2*pi*o.rf_hz/light_speed*o.antenna[ch].x*std::sin((t==3?60:0)*pi/180));
    GroupEstimate fit;fit.has_best=true;fit.angle={0};
    const auto quality=validate_joint_observation(o,fft,{0},fit);
    assert(!quality.passed && quality.latest_residual>.1);
    fit.channel={3,4,5};
    const auto triad_quality=validate_joint_observation(o,fft,{0},fit);
    assert(!triad_quality.passed && triad_quality.latest_residual>.1);
    // Export a non-rank-one covariance fixture for independent NumPy verification.
    for(std::size_t t=0;t<4;++t) for(std::size_t ch=0;ch<6;++ch)
        frames[t][ch]=AoaComplex(std::sin(1+2*t+ch),std::cos(2+3*t-ch*.7));
    fused=covariance_observation(frames,concentration);
    std::cout.precision(17);std::cout<<concentration;
    for(auto z:fused) std::cout<<' '<<z.real()<<' '<<z.imag();
    std::cout<<"\nFour-frame covariance, raw validation and scheduling contracts passed\n";
}
