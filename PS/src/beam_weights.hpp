#pragma once
#include "app.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>
namespace aoa_app {
inline std::uint64_t beam_word(std::size_t ch,double phase) {
    if(ch>=channels || !std::isfinite(phase)) throw std::invalid_argument("Invalid phase/channel");
    auto q23=[](double v) {
        const auto q=std::llround(v*8388608.0);
        return static_cast<std::uint64_t>(std::max(-8388608LL,std::min(8388607LL,q)))&0xffffffULL;
    };
    return (std::uint64_t(ch)<<61)|(q23(std::sin(phase))<<24)|q23(std::cos(phase));
}
inline BeamWeights beam_weights(const Options& o,const std::string& mode,double angle=0,
                                 double target_x=0,double target_y=0) {
    if(!std::isfinite(o.rf_hz)||o.rf_hz<=0 || (o.phase_sign!=1&&o.phase_sign!=-1))
        throw std::invalid_argument("Invalid carrier/phase sign");
    BeamWeights w;w.enabled=true;w.mode=mode;w.angle_deg=angle;
    const double k=2*pi*o.rf_hz/light_speed;
    const auto& reference=o.antenna[0];
    const double r0=std::hypot(target_x-reference.x,target_y-reference.y);
    for(std::size_t ch=0;ch<channels;++ch) {
        const auto& a=o.antenna[ch]; double phase=0;
        if(mode=="manual") {
            const double t=angle*pi/180;
            phase=-o.phase_sign*k*((a.x-reference.x)*std::sin(t)+(a.y-reference.y)*std::cos(t));
        } else if(mode=="follow" || mode=="auto") {
            const double distance=std::hypot(target_x-a.x,target_y-a.y);
            if(distance<.001 || !std::isfinite(distance)) throw std::invalid_argument("Target too close to antenna");
            // 球面波距离差；远场极限与上面的平面波公式一致。
            phase=o.phase_sign*k*(distance-r0);
        } else if(mode!="equal") throw std::invalid_argument("Unknown beam mode");
        // FFT 使用 raw*correction。移相器仅实现相位校准，保留单位增益。
        if(mode!="equal") phase+=std::arg(a.correction)-std::arg(reference.correction);
        phase=std::remainder(phase,2*pi);
        w.phase_rad[ch]=phase;w.words[ch]=beam_word(ch,phase);
    }
    return w;
}

}
