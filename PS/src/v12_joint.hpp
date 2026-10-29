#pragma once
#include "joint_ml.hpp"
#include <tuple>

namespace aoa_app {
inline AoaComplex v12_response(const Options& o,std::size_t ch,double angle) {
    double cx=0,cy=0;
    for(const auto& a:o.antenna) {cx+=a.x/6;cy+=a.y/6;}
    const double t=angle*pi/180;
    return std::polar(1.0,o.phase_sign*2*pi*o.rf_hz/light_speed*
        ((o.antenna[ch].x-cx)*std::sin(t)+(o.antenna[ch].y-cy)*std::cos(t)));
}
// Uniform full-complex conventional receive power on the original calibrated
// snapshots. No phase normalization, PL coefficient quantization, or capture.
inline double v12_scan_power(const Options& o,const FftData& fft,
                              const std::vector<std::size_t>& bins,double angle) {
    std::array<AoaComplex,channels> weights{};
    for(std::size_t ch=0;ch<channels;++ch) weights[ch]=std::conj(v12_response(o,ch,angle));
    double power=0;
    const std::size_t count=fft.snapshots.empty()?1:fft.snapshots.size();
    for(std::size_t t=0;t<count;++t) {
        const auto& frame=fft.snapshots.empty()?fft.corrected:fft.snapshots[t];
        for(const auto b:bins) {
            AoaComplex sum{};
            for(std::size_t ch=0;ch<channels;++ch) sum+=frame[ch*fft_length+b]*weights[ch];
            power+=std::norm(sum)/(36*count);
        }
    }
    return power;
}
inline void v12_scan(const Options& o,const FftData& fft,Analysis& result,
                      const std::function<bool()>& cancelled) {
    auto& s=result.beam_scan;auto& v=result.v12;
    s.frame_count=result.direction_frames;s.source_round=s.frame_count-1;
    s.verification_enabled=false;s.verification_status="same_batch_software";
    if(result.bins.empty()) {s.status="no_signal";return;}
    const double first=o.angle_start,last=o.angle_start+(o.angle_count-1)*o.angle_step;
    if(!std::isfinite(first) || !std::isfinite(last) || first<-90 || last>90 || first>=last)
        throw std::invalid_argument("V1.2 scan requires an ascending domain within -90..90 degrees");
    std::vector<double> spectrum(fft_length);
    const auto count=fft.snapshots.empty()?1:fft.snapshots.size();
    for(std::size_t t=0;t<count;++t) {
        const auto& frame=fft.snapshots.empty()?fft.corrected:fft.snapshots[t];
        for(std::size_t ch=0;ch<channels;++ch) for(std::size_t b=0;b<fft_length;++b)
            spectrum[b]+=std::norm(frame[ch*fft_length+b])/count;
    }
    for(const auto b:result.bins) s.input_power+=spectrum[b]/6;
    if(!(s.input_power>std::numeric_limits<double>::min())) {s.status="no_signal";return;}
    const auto global=std::size_t(std::max_element(spectrum.begin(),spectrum.end())-spectrum.begin());
    s.global_peak_frequency_hz=bin_hz(global,o.sample_rate);
    std::vector<double> noise;
    for(std::size_t b=0;b<fft_length;++b) {
        if(bin_hz(b,o.sample_rate)<o.band_min || bin_hz(b,o.sample_rate)>o.band_max) continue;
        bool exclude=false;
        for(auto k:result.bins) {const auto d=b>k?b-k:k-b;if(std::min(d,fft_length-d)<=2) exclude=true;}
        if(!exclude) noise.push_back(spectrum[b]);
    }
    double floor=0;
    if(!noise.empty()) {auto mid=noise.begin()+noise.size()/2;std::nth_element(noise.begin(),mid,noise.end());floor=*mid;}
    double peak_bin=0;for(auto b:result.bins) peak_bin=std::max(peak_bin,spectrum[b]);
    s.local_snr_db=noise.empty()?0:10*std::log10(std::max(peak_bin,1e-300)/std::max(floor,1e-300));
    for(double angle=first;angle<=last+1e-9;angle+=1) {
        if(cancelled && cancelled()) throw std::runtime_error("V1.2 scan interrupted");
        s.angles_deg.push_back(std::min(angle,last));
        s.normalized_power.push_back(v12_scan_power(o,fft,result.bins,std::min(angle,last))/s.input_power);
    }
    if(s.angles_deg.back()<last-1e-9) {
        s.angles_deg.push_back(last);s.normalized_power.push_back(v12_scan_power(o,fft,result.bins,last)/s.input_power);
    }
    const auto best=std::size_t(std::max_element(s.normalized_power.begin(),s.normalized_power.end())-s.normalized_power.begin());
    v.coarse_angle_deg=s.angles_deg[best];v.local_angle_deg=v.coarse_angle_deg;
    const double lo=std::max(first,v.coarse_angle_deg-1),hi=std::min(last,v.coarse_angle_deg+1);
    double best_power=-1;
    for(int i=-10;i<=10;++i) {
        const double angle=std::clamp(v.coarse_angle_deg+i*.1,lo,hi);
        const double p=v12_scan_power(o,fft,result.bins,angle);
        if(p>best_power) {best_power=p;v.local_angle_deg=angle;}
    }
    double left=std::sin(lo*pi/180),right=std::sin(hi*pi/180);
    const double ratio=(std::sqrt(5.0)-1)/2;
    auto at=[&](double u) {return v12_scan_power(o,fft,result.bins,std::asin(std::clamp(u,-1.0,1.0))*180/pi);};
    for(int i=0;i<36;++i) {
        const double c=right-ratio*(right-left),d=left+ratio*(right-left);
        if(at(c)<at(d)) left=c;else right=d;
    }
    const double continuous=std::asin(std::clamp((left+right)/2,-1.0,1.0))*180/pi;
    s.peak_angle_deg=v.local_angle_deg;
    const double cp=v12_scan_power(o,fft,result.bins,continuous);
    if(cp>best_power) {s.peak_angle_deg=continuous;best_power=cp;}
    s.peak_normalized_power=best_power/s.input_power;
    for(auto p:s.normalized_power) s.relative_db.push_back(10*std::log10(std::max(p,1e-300)/std::max(s.peak_normalized_power,1e-300)));
    std::vector<std::size_t> peaks;
    for(std::size_t i=0;i<s.angles_deg.size();++i)
        if((i==0 || s.normalized_power[i]>s.normalized_power[i-1]) &&
           (i+1==s.angles_deg.size() || s.normalized_power[i]>=s.normalized_power[i+1])) peaks.push_back(i);
    std::sort(peaks.begin(),peaks.end(),[&](auto a,auto b){
        return s.normalized_power[a]!=s.normalized_power[b]?s.normalized_power[a]>s.normalized_power[b]:a<b;});
    s.candidates.push_back({0,s.peak_angle_deg,s.peak_normalized_power,0});
    for(auto i:peaks) {
        if(std::abs(s.angles_deg[i]-s.peak_angle_deg)<5) continue;
        if(!std::isfinite(v.secondary_angle_deg)) v.secondary_angle_deg=s.angles_deg[i];
        s.candidates.push_back({s.candidates.size(),s.angles_deg[i],s.normalized_power[i],s.relative_db[i]});
        if(s.candidates.size()==3) break;
    }
    s.peak_gap_db=s.candidates.size()>1?-s.candidates[1].relative_db:120;
    s.ambiguous=s.candidates.size()>1 && s.peak_gap_db<=o.scan_candidate_gap_db;
    s.valid=s.local_snr_db>=o.scan_min_snr_db;
    s.status=s.valid?"ok":(s.global_peak_frequency_hz<o.band_min || s.global_peak_frequency_hz>o.band_max)?"band_mismatch":"no_signal";
}
// Refit only the retained winning tuple using the original two-pass QR and
// explicit residual. Exhaustive pool objectives remain untouched by this step.
inline GroupEstimate v12_fit(const Options& o,const FftData& fft,const std::vector<std::size_t>& bins,
                             int paths,const JointPoolEntry& entry) {
    GroupEstimate fit;fit.id=0;fit.channel={0,1,2,3,4,5};
    using Vec=std::array<AoaComplex,channels>;
    std::array<Vec,3> a{},q{};
    std::array<std::array<AoaComplex,3>,3> r{};
    for(int p=0;p<paths;++p) {
        const double angle=o.angle_start+entry.index[p]*o.angle_step;fit.angle.push_back(angle);
        for(std::size_t ch=0;ch<channels;++ch) a[p][ch]=q[p][ch]=v12_response(o,ch,angle);
        for(int pass=0;pass<2;++pass) for(int j=0;j<p;++j) {
            AoaComplex dot{};for(std::size_t ch=0;ch<channels;++ch) dot+=std::conj(q[j][ch])*q[p][ch];
            r[j][p]+=dot;for(std::size_t ch=0;ch<channels;++ch) q[p][ch]-=q[j][ch]*dot;
        }
        double norm=0;for(auto z:q[p]) norm+=std::norm(z);
        if(norm<=6e-12) {fit.angle.clear();return fit;}
        r[p][p]=std::sqrt(norm);for(auto& z:q[p]) z/=r[p][p];
    }
    fit.amplitude_rms.assign(paths,0);fit.phase_first_bin_deg.assign(paths,0);
    for(std::size_t b=0;b<bins.size();++b) {
        std::array<AoaComplex,3> alpha{};
        for(int p=0;p<paths;++p) for(std::size_t ch=0;ch<channels;++ch)
            alpha[p]+=std::conj(q[p][ch])*fft.corrected[ch*fft_length+bins[b]];
        for(int p=paths-1;p>=0;--p) {
            for(int j=p+1;j<paths;++j) alpha[p]-=r[p][j]*alpha[j];
            alpha[p]/=r[p][p];fit.amplitude_rms[p]+=std::norm(alpha[p]);
            if(!b) fit.phase_first_bin_deg[p]=std::arg(alpha[p])*180/pi;
        }
        for(std::size_t ch=0;ch<channels;++ch) {
            const auto y=fft.corrected[ch*fft_length+bins[b]];fit.energy+=std::norm(y);
            auto e=y;for(int p=0;p<paths;++p) e-=a[p][ch]*alpha[p];fit.residual+=std::norm(e);
        }
    }
    double peak=0;
    for(auto& amp:fit.amplitude_rms) {amp=std::sqrt(amp/bins.size());peak=std::max(peak,amp);}
    if(!(peak>0)) return fit;
    for(auto amp:fit.amplitude_rms) fit.active.push_back(amp>0 && amp>=o.min_path_ratio*peak);
    double inverse=0;
    for(int col=0;col<paths;++col) {
        std::array<AoaComplex,3> z{};z[col]=1;
        for(int p=paths-1;p>=0;--p) {
            for(int j=p+1;j<paths;++j) z[p]-=r[p][j]*z[j];
            z[p]/=r[p][p];inverse+=std::norm(z[p]);
        }
    }
    fit.steering_condition=std::sqrt(6*paths*inverse);fit.has_best=true;fit.core_status=0;
    return fit;
}
inline double v12_match(double ml,double scan,double bandwidth) {
    const double du=std::sin(ml*pi/180)-std::sin(scan*pi/180);
    return std::exp(-.5*(du/bandwidth)*(du/bandwidth));
}
// Diagnostics are attached only after the main selection is immutable.
inline void v12_attach_secondary(Analysis& result) {
    auto& v=result.v12;
    v.secondary_ml_angle_deg=v.secondary_error_deg=v.secondary_score=std::numeric_limits<double>::quiet_NaN();
    if(std::isfinite(v.secondary_angle_deg)) {
        double closest=std::numeric_limits<double>::infinity();
        for(int p=0;p<int(result.joint.angle.size());++p) if(p!=result.selection.path_index) {
            const double angle=result.joint.angle[p];
            const double distance=std::abs(std::sin(angle*pi/180)-std::sin(v.secondary_angle_deg*pi/180));
            if(distance<closest) {closest=distance;v.secondary_ml_angle_deg=angle;}
        }
        if(std::isfinite(v.secondary_ml_angle_deg)) {
            v.secondary_error_deg=std::abs(v.secondary_ml_angle_deg-v.secondary_angle_deg);
            v.secondary_score=v12_match(v.secondary_ml_angle_deg,v.secondary_angle_deg,.1);
        }
    }
}
inline void analyze_v12(const Options& o,const FftData& raw,const FftData& fused,
                         Analysis& result,const std::function<bool()>& cancelled) {
    auto& v=result.v12;v.enabled=true;
    std::array<Analysis,3> models;std::array<JointPool,3> pools;
    for(int k=1;k<=3;++k) {
        auto config=o;config.processing_mode="angle6";config.joint_paths=k;
        models[k-1].bins=result.bins;
        analyze_joint_search(config,fused,models[k-1],cancelled,&pools[k-1]);
        v.pool_counts[k-1]=pools[k-1].top.size();
    }
    // Pools are completely fixed before the independent full-complex scan.
    v12_scan(o,raw,result,cancelled);
    v.scan=result.beam_scan;
    if(!result.beam_scan.valid) return;
    int chosen_k=0,chosen_path=-1,chosen_rank=0;
    JointPoolEntry chosen;double score=-1,main_match=0;
    for(int k=1;k<=3;++k) for(std::size_t rank=0;rank<pools[k-1].top.size();++rank) {
        const auto& entry=pools[k-1].top[rank];const double energy=models[k-1].joint.energy;
        if(!(energy>0)) continue;
        const double residual=entry.loss/energy;
        for(int p=0;p<k;++p) {
            const double angle=o.angle_start+entry.index[p]*o.angle_step;
            const double match=v12_match(angle,result.beam_scan.peak_angle_deg,.05);
            const double s=.25*(1-residual)+.75*match;
            const auto tie=std::make_tuple(entry.loss,k,entry.index,p);
            const auto old=std::make_tuple(chosen.loss,chosen_k,chosen.index,chosen_path);
            if(s>score || (s==score && tie<old)) {
                score=s;chosen=entry;chosen_k=k;chosen_path=p;chosen_rank=int(rank)+1;main_match=match;
            }
        }
    }
    if(!chosen_k) return;
    auto& model=models[chosen_k-1];
    result.joint=v12_fit(o,fused,result.bins,chosen_k,chosen);
    if(!result.joint.has_best) return;
    result.joint_profile_angles=std::move(model.joint_profile_angles);
    result.joint_profile_db=std::move(model.joint_profile_db);result.joint_score=std::move(model.joint_score);
    auto& selection=result.selection;selection.enabled=true;selection.accepted=true;
    selection.model_paths=chosen_k;selection.path_index=chosen_path;selection.selected_score=score;
    selection.status="v12_original_ml_top30_softmatch";
    selection.global_residual=chosen.loss/result.joint.energy;
    v.valid=true;v.angle_deg=result.joint.angle[chosen_path];v.score=score;v.main_match=main_match;
    v.residual_ratio=selection.global_residual;v.selected_rank=chosen_rank;
    v12_attach_secondary(result);
    if(fused.snapshots.size()==npu_rounds) {
        result.joint_raw_validated=true;
        result.joint_raw_quality=validate_joint_observation(o,fused,result.bins,result.joint);
    }

}
} // namespace aoa_app
