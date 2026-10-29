#include "app.hpp"
#include "beam_scan.hpp"
#include "joint_ml.hpp"
#include "batch_covariance.hpp"
#include "v12_joint.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace aoa_app {
namespace {
double angle_at(const Options& o, std::size_t i) { return o.angle_start + static_cast<double>(i)*o.angle_step; }
void check_cancelled(const std::function<bool()>& cancelled) {
    if (cancelled && cancelled()) throw std::runtime_error("Direction calculation interrupted");
}
double group_energy(const FftData& fft, const std::vector<std::size_t>& bins,
                    const std::vector<std::size_t>& group) {
    double energy = 0;
    for (auto bin : bins) for (auto ch : group) energy += std::norm(fft.corrected[ch*fft_length+bin]);
    if (!std::isfinite(energy)) throw std::runtime_error("Selected FFT energy overflow");
    return energy;
}
void write_fit(std::ofstream& out, int group, std::size_t bin, std::size_t path,
               double angle, AoaComplex alpha) {
    if(!out.is_open()) return;
    out << group << ',' << bin << ',' << path << ',' << angle << ',' << alpha.real() << ','
        << alpha.imag() << ',' << std::abs(alpha) << ',' << std::arg(alpha)*180/pi << '\n';
}

GroupEstimate two_paths(const Options& o, const FftData& fft, const std::vector<std::size_t>& bins,
                        int id, const std::function<bool()>& cancelled) {
    GroupEstimate estimate; estimate.id=id;
    estimate.channel.assign(o.groups[id].begin(),o.groups[id].end());
    estimate.energy=group_energy(fft,bins,estimate.channel);
    if(estimate.energy<=std::numeric_limits<double>::min()) return estimate;
    const std::size_t n=o.angle_count, cells=n*n, k=bins.size();
    std::vector<AoaSteering> response(n);
    std::vector<AoaGridPoint> points(cells*k);
    std::vector<double> cost(cells,0), independence(cells,0);
    std::vector<AoaPointStatus> state(cells,AoaPointStatus::Valid);
    std::vector<bool> empty(k,false);
    AoaConfig config;
    config.angle_start_deg=o.angle_start; config.angle_step_deg=o.angle_step;
    config.angle_count=n; config.phase_sign=o.phase_sign;
    for(std::size_t p=0;p<3;++p) {
        // Swap physical X/Y so the unchanged core's angle is the README broadside angle.
        config.antenna_x_m[p]=o.antenna[estimate.channel[p]].y;
        config.antenna_y_m[p]=o.antenna[estimate.channel[p]].x;
    }
    AoaWorkspace workspace; workspace.steering=response.data(); workspace.angle_capacity=n;
    auto grid=result_file(o,"_ml3_grid.csv",true);
    for(std::size_t b=0;b<k;++b) {
        check_cancelled(cancelled);
        AoaInput input; input.rf_frequency_hz=o.rf_hz;
        double energy=0;
        for(std::size_t p=0;p<3;++p) {
            auto y=fft.corrected[estimate.channel[p]*fft_length+bins[b]];
            input.fft_real[p]=y.real(); input.fft_imag[p]=y.imag(); energy+=std::norm(y);
        }
        if(energy<=std::numeric_limits<double>::min()) { empty[b]=true; continue; }
        AoaResult result; result.grid=points.data()+b*cells; result.grid_capacity=cells;
        const auto status=aoa_ml_3ant(input,config,workspace,result);
        if(status!=AoaStatus::Ok && status!=AoaStatus::NoCandidate)
            throw std::runtime_error("ML3 core failed: "+std::to_string(static_cast<int>(status)));
        for(std::size_t i=0;i<n;++i) {
            check_cancelled(cancelled);
            for(std::size_t j=0;j<n;++j) {
                const auto index=i*n+j; const auto& point=points[b*cells+index];
                cost[index]+=point.residual; independence[index]=point.independence;
                if(point.status!=AoaPointStatus::Valid) state[index]=point.status;
                if(grid.is_open()) grid<<id<<','<<bins[b]<<','<<i<<','<<j<<','<<angle_at(o,i)<<','<<angle_at(o,j)<<','
                    <<point.alpha_real[0]<<','<<point.alpha_imag[0]<<','<<point.alpha_real[1]<<','
                    <<point.alpha_imag[1]<<','<<point.residual<<','<<point.independence<<','
                    <<static_cast<int>(point.status)<<'\n';
            }
        }
    }
    auto scores=result_file(o,"_ml3_scores.csv",true);
    double best_loss=std::numeric_limits<double>::infinity(); std::size_t best_i=0,best_j=0;
    for(std::size_t i=0;i<n;++i) for(std::size_t j=0;j<n;++j) {
        const auto index=i*n+j;
        if(scores.is_open()) scores<<id<<','<<i<<','<<j<<','<<angle_at(o,i)<<','<<angle_at(o,j)<<','<<cost[index]<<','
              <<cost[index]/estimate.energy<<','<<independence[index]<<','<<static_cast<int>(state[index])<<'\n';
        if(i<j && state[index]==AoaPointStatus::Valid && cost[index]<best_loss) {
            estimate.has_best=true; best_loss=cost[index]; best_i=i; best_j=j;
        }
    }
    // [上位机新增] 直接复用已求出的联合残差；只压缩显示网格，不重算、不改变搜索精度。
    // 评分=clamp(1-J/E,0,1)，并非概率；病态/退化单元单独带掩码，显示为灰色。
    if(o.console_enabled) {
        const std::size_t display_n=std::min<std::size_t>(n,161);
        std::vector<std::size_t> index(display_n);
        for(std::size_t i=0;i<display_n;++i) {
            index[i]=i*(n-1)/(display_n-1);
            estimate.display_angles.push_back(angle_at(o,index[i]));
        }
        for(auto i:index) for(auto j:index) {
            const auto cell=i*n+j;
            const bool invalid=state[cell]!=AoaPointStatus::Valid || !std::isfinite(cost[cell]);
            const double score=invalid?0:std::clamp(1-cost[cell]/estimate.energy,0.0,1.0);
            estimate.display_score.push_back(static_cast<std::uint8_t>(std::lround(score*255)));
            estimate.display_invalid.push_back(invalid?1:0);
        }
    }
    estimate.core_status=estimate.has_best?0:-4;
    if(estimate.has_best) {
        estimate.residual=best_loss;
        estimate.response_independence=independence[best_i*n+best_j];
        if(estimate.response_independence>0) {
            const double rho=std::sqrt(std::max(0.0,1-estimate.response_independence));
            estimate.steering_condition=(1+rho)/std::sqrt(estimate.response_independence);
        }
        estimate.angle={angle_at(o,best_i),angle_at(o,best_j)};
        estimate.amplitude_rms.resize(2); estimate.phase_first_bin_deg.resize(2);
        auto fit=result_file(o,"_fit.csv",true);
        for(std::size_t path=0;path<2;++path) {
            double power=0;
            for(std::size_t b=0;b<k;++b) {
                const auto& point=points[b*cells+best_i*n+best_j];
                const AoaComplex coefficient=empty[b]?AoaComplex(0,0):
                    AoaComplex(point.alpha_real[path],point.alpha_imag[path]);
                power+=std::norm(coefficient);
                if(b==0) estimate.phase_first_bin_deg[path]=std::arg(coefficient)*180/pi;
                write_fit(fit,id,bins[b],path,estimate.angle[path],coefficient);
            }
            estimate.amplitude_rms[path]=std::sqrt(power/static_cast<double>(k));
        }
        const double maximum=*std::max_element(estimate.amplitude_rms.begin(),estimate.amplitude_rms.end());
        for(auto amp:estimate.amplitude_rms) estimate.active.push_back(amp>0 && amp>=o.min_path_ratio*maximum);
        for(std::size_t i=0;i<n;++i) for(std::size_t j=i+1;j<n;++j) {
            const auto index=i*n+j;
            if(state[index]==AoaPointStatus::Valid && cost[index]/estimate.energy<=best_loss/estimate.energy+o.ambiguity_tolerance) {
                ++estimate.near_equal_candidates;
                if(std::max(std::abs(angle_at(o,i)-estimate.angle[0]),std::abs(angle_at(o,j)-estimate.angle[1]))>2*o.angle_step)
                    estimate.ambiguous=true;
            }
        }
        fit.flush();
    }
    grid.flush(); scores.flush();
    return estimate;
}
} // namespace

Analysis analyze(const Options& o, const FftData& fft, const std::function<bool()>& cancelled) {
    const auto started=std::chrono::steady_clock::now();
    Analysis result;
    if(!std::isfinite(o.scan_ml_weight) || o.scan_ml_weight<0 || o.scan_ml_weight>1)
        throw std::invalid_argument("Scan ML weight must be 0..1");
    result.direction_frames=fft.snapshots.empty()?1:fft.snapshots.size();
    if(result.direction_frames!=1 && result.direction_frames!=npu_rounds)
        throw std::runtime_error("Direction requires one legacy frame or four batch frames");
    if(o.processing_mode=="scan6" && result.direction_frames!=npu_rounds)
        throw std::runtime_error("Beam scan requires four independent frames");
    for(const auto& frame:fft.snapshots) if(frame.size()!=fft_records)
        throw std::runtime_error("Invalid direction snapshot dimensions");
    std::vector<double> power(fft_length,0);
    for(std::size_t bin=0;bin<fft_length;++bin) {
        for(std::size_t ch=0;ch<channels;++ch) {
            if(!fft.valid[ch*fft_length+bin]) throw std::runtime_error("Cannot estimate direction from incomplete FFT capture");
            if(fft.snapshots.empty()) power[bin]+=std::norm(fft.corrected[ch*fft_length+bin]);
            else for(const auto& frame:fft.snapshots)
                power[bin]+=std::norm(frame[ch*fft_length+bin])/fft.snapshots.size();
        }
        power[bin]/=static_cast<double>(channels);
        if(!std::isfinite(power[bin])) throw std::runtime_error("Common FFT power overflow");
    }
    if(!o.bins.empty()) {
        for(auto bin:o.bins) if(power[bin]>o.min_bin_power && power[bin]>std::numeric_limits<double>::min()) result.bins.push_back(bin);
    } else {
        std::vector<std::size_t> candidates;
        for(std::size_t bin=0;bin<fft_length;++bin)
            if(bin_hz(bin,o.sample_rate)>=o.band_min && bin_hz(bin,o.sample_rate)<=o.band_max &&
               power[bin]>o.min_bin_power && power[bin]>std::numeric_limits<double>::min()) candidates.push_back(bin);
        std::sort(candidates.begin(),candidates.end(),[&](auto a,auto b){return power[a]!=power[b]?power[a]>power[b]:a<b;});
        candidates.resize(std::min(o.auto_bins,candidates.size())); result.bins=std::move(candidates);
    }
    auto selected=result_file(o,"_selected.csv",true);
    for(auto bin:result.bins) for(std::size_t ch=0;ch<channels;++ch) {
        const auto index=ch*fft_length+bin;
        if(o.save_results) selected<<bin<<','<<ch<<','<<bin_hz(bin,o.sample_rate)<<','<<fft.raw[index].real()<<','
                <<fft.raw[index].imag()<<','<<fft.corrected[index].real()<<','<<fft.corrected[index].imag()<<','<<power[bin]<<'\n';
    }
    selected.flush();
    const auto observation=fuse_selected_bins(fft,result.bins,result.covariance_concentration);
    if(o.processing_mode=="position") {
        // Position mode always retains both original ML paths from each triad.
        for(int id=0;id<2;++id) {
            result.groups.push_back(two_paths(o,observation,result.bins,id,cancelled));
            if(fft.snapshots.size()==npu_rounds) {
                auto& group=result.groups.back();
                const auto raw=validate_joint_observation(o,observation,result.bins,group);
                group.raw_validated=true;group.raw_residual_ratio=raw.raw_residual;
                group.latest_residual_ratio=raw.latest_residual;
            }
        }
        localize_v12(o,result);
        result.status=result.bins.empty()?"no_signal":
            result.groups[0].has_best && result.groups[1].has_best?"ok":"no_candidate";
    } else {
        analyze_v12(o,fft,observation,result,cancelled);
        if(o.processing_mode=="scan6") {
            // Preserve the original explicitly enabled Q23 scan verification path.
            // The fine full-complex curve lives independently in v12.scan.
            result.beam_scan=BeamScan{};
            analyze_beam_scan(o,fft,result,cancelled);
        }
        if(result.bins.empty()) result.status="no_signal";
        else result.status=result.v12.valid?"ok":result.beam_scan.status=="no_signal"?"no_signal":"no_candidate";
        result.localization.status="angle_only";
    }
    result.complete=true;
    result.elapsed_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
    return result;
}
} // namespace aoa_app
