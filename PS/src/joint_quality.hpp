#pragma once
#include "app.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>

namespace aoa_app {
// Refit independent complex amplitudes in each original snapshot. No phase
// continuity between captures is assumed. Geometry/angles remain fixed.
inline JointQuality validate_joint_observation(const Options& o,const FftData& fft,
    const std::vector<std::size_t>& bins,const GroupEstimate& fit) {
    JointQuality out;out.paths=static_cast<int>(fit.angle.size());out.angles=fit.angle;
    if(!fit.has_best || bins.empty() || out.paths<1 || out.paths>3) return out;
    using Vec=std::array<AoaComplex,6>;
    const auto channel=fit.channel.empty()?std::vector<std::size_t>{0,1,2,3,4,5}:fit.channel;
    const auto count_channels=channel.size();
    if(count_channels<static_cast<std::size_t>(out.paths) || count_channels>6 ||
       std::any_of(channel.begin(),channel.end(),[](auto ch){return ch>=6;})) return out;
    std::array<Vec,3> a{},q{};
    std::array<std::array<AoaComplex,3>,3> r{};
    double cx=0,cy=0;
    for(auto ch:channel) {cx+=o.antenna[ch].x/count_channels;cy+=o.antenna[ch].y/count_channels;}
    for(int p=0;p<out.paths;++p) {
        const double theta=fit.angle[p]*pi/180;
        for(std::size_t m=0;m<count_channels;++m) {
            const auto ch=channel[m];
            a[p][m]=std::polar(1.0,o.phase_sign*2*pi*o.rf_hz/light_speed*
                ((o.antenna[ch].x-cx)*std::sin(theta)+(o.antenna[ch].y-cy)*std::cos(theta)));
        }
        q[p]=a[p];
        for(int pass=0;pass<2;++pass) for(int j=0;j<p;++j) {
            AoaComplex dot{};
            for(std::size_t ch=0;ch<6;++ch) dot+=std::conj(q[j][ch])*q[p][ch];
            r[j][p]+=dot;
            for(std::size_t ch=0;ch<6;++ch) q[p][ch]-=q[j][ch]*dot;
        }
        double norm=0;for(auto z:q[p]) norm+=std::norm(z);
        if(norm<=6e-12) {out.reason="singular";return out;}
        r[p][p]=std::sqrt(norm);for(auto& z:q[p]) z/=r[p][p];
    }
    const std::size_t count=fft.snapshots.empty()?1:fft.snapshots.size();
    double energy=0,loss=0;
    out.amplitudes.assign(out.paths,0);
    for(std::size_t t=0;t<count;++t) {
        const auto& frame=fft.snapshots.empty()?fft.corrected:fft.snapshots[t];
        double frame_energy=0,frame_loss=0;
        for(auto bin:bins) {
            std::array<AoaComplex,3> alpha{};
            for(int p=0;p<out.paths;++p) for(std::size_t m=0;m<count_channels;++m)
                alpha[p]+=std::conj(q[p][m])*frame[channel[m]*fft_length+bin];
            for(int p=out.paths-1;p>=0;--p) {
                for(int j=p+1;j<out.paths;++j) alpha[p]-=r[p][j]*alpha[j];
                alpha[p]/=r[p][p];out.amplitudes[p]+=std::norm(alpha[p]);
            }
            for(std::size_t m=0;m<count_channels;++m) {
                const auto y=frame[channel[m]*fft_length+bin];auto residual=y;
                for(int p=0;p<out.paths;++p) residual-=a[p][m]*alpha[p];
                frame_energy+=std::norm(y);frame_loss+=std::norm(residual);
            }
        }
        if(!std::isfinite(frame_energy) || !std::isfinite(frame_loss) || frame_energy<=0) {
            out.reason="invalid_snapshot";return out;
        }
        energy+=frame_energy;loss+=frame_loss;
        out.latest_residual=frame_loss/frame_energy;
    }
    for(auto& amp:out.amplitudes) amp=std::sqrt(amp/(count*bins.size()));
    out.raw_residual=loss/energy;
    const auto peak=*std::max_element(out.amplitudes.begin(),out.amplitudes.end());
    if(out.raw_residual>o.max_bearing_residual) out.reason="raw_residual";
    else if(out.latest_residual>o.max_bearing_residual) out.reason="latest_residual";
    else if(fit.ambiguous) out.reason="ambiguous";
    else if(std::any_of(out.amplitudes.begin(),out.amplitudes.end(),[&](double amp){
                return !(amp>0) || amp<o.min_path_ratio*peak;})) out.reason="weak_path";
    else {out.passed=true;out.reason="qualified";}
    return out;
}

} // namespace aoa_app
