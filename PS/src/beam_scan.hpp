#pragma once
#include "beam_weights.hpp"
#include "scan_fusion.hpp"
#include "console/console_transport.hpp"
#include <algorithm>
#include <chrono>
#include <numeric>

namespace aoa_app {
// Decode the very same signed Q23 coefficients sent to PL. The calibration
// magnitude is not implemented by the phase rotators and must not be added here.
inline AoaComplex scan_coefficient(std::uint64_t word) {
    auto component=[](std::uint64_t value) {
        const auto v=static_cast<std::int32_t>(value&0xffffffULL);
        return (v&0x800000?v-0x1000000:v)/8388608.0;
    };
    return {component(word),component(word>>24)};
}
inline double scan_power(const Options& o,const std::vector<AoaComplex>& frame,
                         const std::vector<std::size_t>& bins,double angle) {
    const auto w=beam_weights(o,"manual",angle);
    double power=0;
    for(const auto bin:bins) {
        AoaComplex sum{};
        for(std::size_t ch=0;ch<channels;++ch)
            sum+=frame.at(ch*fft_length+bin)/o.antenna[ch].correction*scan_coefficient(w.words[ch]);
        power+=std::norm(sum);
    }
    return power;
}
inline void analyze_beam_scan(const Options& o,const FftData& fft,Analysis& result,
                             const std::function<bool()>& cancelled) {
    const auto started=std::chrono::steady_clock::now();
    auto& s=result.beam_scan;s.frame_count=result.direction_frames;
    s.verification_enabled=o.scan_verify;s.verification_status=o.scan_verify?"not_run":"disabled";
    s.source_round=s.frame_count-1;
    if(result.bins.empty()) {s.status="no_signal";return;}
    for(const auto& a:o.antenna)
        if(!std::isfinite(std::norm(a.correction)) || std::abs(a.correction)<1e-12)
            throw std::runtime_error("Invalid scan calibration coefficient");
    auto frame_at=[&](std::size_t t)->const std::vector<AoaComplex>& {
        return fft.snapshots.empty()?fft.corrected:fft.snapshots.at(t);
    };
    std::vector<double> spectrum(fft_length);
    for(std::size_t t=0;t<s.frame_count;++t) for(std::size_t ch=0;ch<channels;++ch)
        for(std::size_t b=0;b<fft_length;++b)
            spectrum[b]+=std::norm(frame_at(t).at(ch*fft_length+b)/o.antenna[ch].correction)/s.frame_count;
    for(const auto b:result.bins) s.input_power+=spectrum[b];
    const auto global=std::size_t(std::max_element(spectrum.begin(),spectrum.end())-spectrum.begin());
    s.global_peak_frequency_hz=bin_hz(global,o.sample_rate);
    std::vector<double> noise;
    for(std::size_t b=0;b<fft_length;++b) {
        if(bin_hz(b,o.sample_rate)<o.band_min || bin_hz(b,o.sample_rate)>o.band_max) continue;
        bool excluded=false;
        for(const auto k:result.bins) {
            const auto d=b>k?b-k:k-b;
            if(std::min(d,fft_length-d)<=2) excluded=true;
        }
        if(!excluded) noise.push_back(spectrum[b]);
    }
    double floor=0;
    if(!noise.empty()) {
        auto middle=noise.begin()+noise.size()/2;std::nth_element(noise.begin(),middle,noise.end());floor=*middle;
    }
    const auto selected=*std::max_element(result.bins.begin(),result.bins.end(),
        [&](auto a,auto b){return spectrum[a]<spectrum[b];});
    const double peak=spectrum[selected];
    s.local_snr_db=noise.empty()?0:10*std::log10(std::max(peak,1e-300)/std::max(floor,1e-300));
    if(!(s.input_power>std::numeric_limits<double>::min())) {s.status="no_signal";return;}
    for(std::size_t i=0;i<o.angle_count;++i) {
        if(cancelled && cancelled()) throw std::runtime_error("Beam scan interrupted");
        const double angle=o.angle_start+o.angle_step*i;
        double power=0;
        for(std::size_t t=0;t<s.frame_count;++t) power+=scan_power(o,frame_at(t),result.bins,angle)/s.frame_count;
        s.angles_deg.push_back(angle);s.normalized_power.push_back(power/s.input_power);
    }
    const auto best=std::size_t(std::max_element(s.normalized_power.begin(),s.normalized_power.end())-s.normalized_power.begin());
    s.peak_angle_deg=s.angles_deg[best];s.peak_normalized_power=s.normalized_power[best];
    for(const auto p:s.normalized_power)
        s.relative_db.push_back(10*std::log10(std::max(p,1e-300)/std::max(s.peak_normalized_power,1e-300)));
    std::vector<std::size_t> peaks;
    for(std::size_t i=0;i<s.angles_deg.size();++i)
        if((i==0||s.normalized_power[i]>s.normalized_power[i-1]) &&
           (i+1==s.angles_deg.size()||s.normalized_power[i]>=s.normalized_power[i+1])) peaks.push_back(i);
    std::sort(peaks.begin(),peaks.end(),[&](auto a,auto b){return s.normalized_power[a]>s.normalized_power[b];});
    for(const auto index:peaks) {
        bool close=false;
        for(const auto& c:s.candidates) if(std::abs(c.angle_deg-s.angles_deg[index])<5) close=true;
        if(close) continue;
        s.candidates.push_back({s.candidates.size(),s.angles_deg[index],s.normalized_power[index],s.relative_db[index]});
        if(s.candidates.size()==3) break;
    }
    s.peak_gap_db=s.candidates.size()>1?-s.candidates[1].relative_db:120;
    s.ambiguous=s.candidates.size()>1 && s.peak_gap_db<=o.scan_candidate_gap_db;
    // This diagnostic is a local peak/noise estimate, not calibrated receiver SNR.
    s.valid=s.local_snr_db>=o.scan_min_snr_db;
    s.status=s.valid?"ok":(s.global_peak_frequency_hz<o.band_min||s.global_peak_frequency_hz>o.band_max)?"band_mismatch":"no_signal";
    s.elapsed_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
}
inline aoa_console::Json beam_scan_packet(const BeamScan& s) {
    using aoa_console::Json;
    Json candidates=Json::array(),checks=Json::array();
    for(const auto& c:s.candidates) candidates.push_back({{"id",c.id},{"angle_deg",c.angle_deg},
        {"normalized_power",c.normalized_power},{"relative_db",c.relative_db}});
    for(const auto& c:s.checks) checks.push_back({{"candidate_id",c.candidate_id},{"capture_id",c.capture_id},
        {"angle_deg",c.angle_deg},{"expected_power",c.expected_power},{"observed_power",c.observed_power},
        {"relative_error",c.relative_error},{"input_shape_error",c.input_shape_error},
        {"registers_verified",c.registers_verified},{"valid",c.valid},{"error",c.error},
        {"fft_file",c.fft_file},{"pha_file",c.pha_file},{"source_input_file",c.source_input_file},{"npu_eligible",false}});
    return {{"model","phase_only_beam_power_scan"},{"source","pl_independent_fft_same_batch"},
        {"status",s.status},{"valid",s.valid},{"angles_deg",s.angles_deg},{"normalized_power",s.normalized_power},
        {"relative_db",s.relative_db},{"peak_angle_deg",s.peak_angle_deg},{"peak_normalized_power",s.peak_normalized_power},
        {"peak_gap_db",s.peak_gap_db},{"ambiguous",s.ambiguous},{"input_power",s.input_power},
        {"frame_count",s.frame_count},{"elapsed_ms",s.elapsed_ms},{"local_snr_db",s.local_snr_db},
        {"global_peak_frequency_hz",s.global_peak_frequency_hz},{"candidates",candidates},
        {"fusion",scan_fusion_packet(s.fusion)},
        {"verification",{{"enabled",s.verification_enabled},{"status",s.verification_status},
            {"source_round",s.source_round},{"restore_verified",s.restore_verified},{"elapsed_ms",s.verification_ms},{"checks",checks}}}};
}
} // namespace aoa_app
