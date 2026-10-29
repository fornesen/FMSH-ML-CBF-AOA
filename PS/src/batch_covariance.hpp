#pragma once
// Four within-batch snapshots; no angle averaging or cross-batch state.
#include "app.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace aoa_app {
using SpatialVector=std::array<AoaComplex,channels>;
using SpatialMatrix=std::array<SpatialVector,channels>;

inline SpatialVector covariance_observation(const std::vector<SpatialVector>& frames,
                                            double& concentration) {
    if(frames.empty() || frames.size()>npu_rounds)
        throw std::invalid_argument("Covariance requires 1..4 distinct snapshots");
    SpatialMatrix h{},v{};
    double latest_energy=0;
    for(const auto& y:frames) {
        double energy=0;
        for(auto z:y) energy+=std::norm(z);
        if(!std::isfinite(energy) || energy<=std::numeric_limits<double>::min())
            throw std::runtime_error("Nonfinite or zero covariance snapshot");
        latest_energy=energy;
        for(std::size_t i=0;i<channels;++i) for(std::size_t j=0;j<channels;++j)
            h[i][j]+=y[i]*std::conj(y[j])/(energy*frames.size());
    }
    for(std::size_t i=0;i<channels;++i) v[i][i]=1;
    // Complex Hermitian Jacobi; fixed 6x6 size, no new linear algebra dependency.
    for(int sweep=0;sweep<64;++sweep) {
        double maximum=0;
        for(std::size_t p=0;p<channels;++p) for(std::size_t q=p+1;q<channels;++q) {
            const double b=std::abs(h[p][q]);maximum=std::max(maximum,b);
            if(b<1e-14) continue;
            const auto phase=h[p][q]/b;
            const double tau=(h[q][q].real()-h[p][p].real())/(2*b);
            const double t=std::copysign(1.0,tau)/(std::abs(tau)+std::hypot(1.0,tau));
            const double c=1/std::sqrt(1+t*t),s=t*c;
            for(std::size_t i=0;i<channels;++i) {
                if(i!=p && i!=q) {
                    const auto a=h[i][p],d=h[i][q];
                    h[i][p]=c*a-s*std::conj(phase)*d;
                    h[i][q]=s*phase*a+c*d;
                    h[p][i]=std::conj(h[i][p]);h[q][i]=std::conj(h[i][q]);
                }
                const auto a=v[i][p],d=v[i][q];
                v[i][p]=c*a-s*std::conj(phase)*d;v[i][q]=s*phase*a+c*d;
            }
            h[p][p]=h[p][p].real()-t*b;h[q][q]=h[q][q].real()+t*b;
            h[p][q]=h[q][p]=0;
        }
        if(maximum<1e-13) break;
        if(sweep==63) throw std::runtime_error("Covariance eigensolver did not converge");
    }
    std::size_t best=0;
    for(std::size_t i=1;i<channels;++i) if(h[i][i].real()>h[best][best].real()) best=i;
    concentration=std::clamp(h[best][best].real(),0.0,1.0);
    SpatialVector output{};AoaComplex alignment{};
    for(std::size_t ch=0;ch<channels;++ch) alignment+=std::conj(v[ch][best])*frames.back()[ch];
    const auto phase=std::abs(alignment)>0?alignment/std::abs(alignment):AoaComplex(1,0);
    for(std::size_t ch=0;ch<channels;++ch) output[ch]=v[ch][best]*phase*std::sqrt(latest_energy);
    return output;
}

inline FftData fuse_selected_bins(const FftData& fft,const std::vector<std::size_t>& bins,
                                 double& concentration) {
    FftData fused=fft;concentration=1;
    if(fft.snapshots.size()<2) return fused;
    if(fft.snapshots.size()!=npu_rounds)
        throw std::runtime_error("Partial four-frame batch cannot estimate direction");
    for(const auto& frame:fft.snapshots) if(frame.size()!=fft_records)
        throw std::runtime_error("Invalid FFT snapshot dimensions");
    for(auto bin:bins) {
        std::vector<SpatialVector> frames(fft.snapshots.size());
        for(std::size_t t=0;t<frames.size();++t) for(std::size_t ch=0;ch<channels;++ch)
            frames[t][ch]=fft.snapshots[t][ch*fft_length+bin];
        double fraction=0;const auto y=covariance_observation(frames,fraction);
        concentration=std::min(concentration,fraction);
        for(std::size_t ch=0;ch<channels;++ch) fused.corrected[ch*fft_length+bin]=y[ch];
    }
    return fused;
}
} // namespace aoa_app
