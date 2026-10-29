#ifdef NDEBUG
#undef NDEBUG
#endif
#include "v12_joint.hpp"
#include "batch_covariance.hpp"
#include "console_aoa.hpp"
#include "beam_control.hpp"
#include <cassert>
#include <iostream>
#include <random>
namespace aoa_app {
double bin_hz(std::size_t b,double rate) {
    return (b>=fft_length/2?double(b)-fft_length:double(b))*rate/4/fft_length;
}
std::uint32_t dds_word(const Options&) {return 0;}
}
using namespace aoa_app;
namespace {
Options options() {
    Options o;o.processing_mode="angle6";o.bins={4014};o.rf_hz=445e6;
    o.band_min=-550e3;o.band_max=-450e3;o.console_enabled=true;
    const std::array<double,6> x{{-1.2823613348729794,-.9361806674364898,-.59,.59,.9361806674364898,1.2823613348729794}};
    for(std::size_t ch=0;ch<6;++ch) o.antenna[ch].x=x[ch];
    return o;
}
FftData source(const Options& o,double angle) {
    FftData fft;fft.valid.assign(fft_records,1);
    for(std::size_t t=0;t<4;++t) {
        std::vector<AoaComplex> frame(fft_records,{1e-6,0});
        for(std::size_t ch=0;ch<6;++ch)
            frame[ch*fft_length+4014]=v12_response(o,ch,angle)*std::polar(10.0,t*1.3);
        fft.snapshots.push_back(std::move(frame));
    }
    fft.corrected=fft.raw=fft.snapshots.back();return fft;
}
void pool_contract() {
    auto o=options();o.angle_start=-60;o.angle_step=20;o.angle_count=7;
    std::mt19937 rng(773);std::normal_distribution<double> normal;
    auto fft=source(o,17.3);
    for(std::size_t ch=0;ch<6;++ch) fft.corrected[ch*fft_length+4014]={normal(rng),normal(rng)};
    for(int k=1;k<=3;++k) {
        o.joint_paths=k;Analysis a;a.bins=o.bins;JointPool pool;
        analyze_joint_search(o,fft,a,{},&pool);
        std::vector<JointPoolEntry> independent;
        for(std::size_t i=0;i<o.angle_count;++i) for(std::size_t j=(k>1?i+1:0);j<(k>1?o.angle_count:1);++j)
            for(std::size_t l=(k>2?j+1:0);l<(k>2?o.angle_count:1);++l) {
                JointPoolEntry entry;entry.index={{i,j,l}};
                const auto fit=v12_fit(o,fft,o.bins,k,entry);
                if(fit.has_best) {entry.loss=fit.residual;independent.push_back(entry);}
            }
        std::sort(independent.begin(),independent.end());
        independent.resize(std::min<std::size_t>(30,independent.size()));
        assert(pool.top.size()==independent.size());
        for(std::size_t i=0;i<pool.top.size();++i) {
            assert(pool.top[i].index==independent[i].index);
            assert(std::abs(pool.top[i].loss-independent[i].loss)<1e-10);
        }
        // A collector is observational: original objective/output stay exact.
        Analysis baseline;baseline.bins=o.bins;analyze_joint_search(o,fft,baseline,{});
        assert(a.joint.angle==baseline.joint.angle && a.joint.residual==baseline.joint.residual);
        assert(a.joint_score==baseline.joint_score && a.joint_profile_db==baseline.joint_profile_db);
    }
    // The original numerical degeneracy rule leaves all overlapping K>1
    // responses out of both the full grid and the retained pool.
    for(auto& ant:o.antenna) ant.x=ant.y=0;
    o.joint_paths=2;Analysis a;a.bins=o.bins;JointPool pool;
    analyze_joint_search(o,fft,a,{},&pool);assert(pool.top.empty());
}
void pipeline_contract() {
    auto o=options();o.phase_sign=-1;
    // Include Y offsets and nontrivial calibration metadata. The observations
    // are already corrected; applying calibration again is a regression.
    for(std::size_t ch=0;ch<6;++ch) {
        o.antenna[ch].y=.015*ch;
        o.antenna[ch].correction=std::polar(.4+.3*ch,.2*ch);
    }
    auto fft=source(o,17.36);const auto before=fft;
    const auto a=analyze(o,fft,{});
    assert(a.v12.valid && a.selection.accepted && a.joint.has_best);
    assert(std::abs(a.beam_scan.peak_angle_deg-17.36)<1e-5);
    assert(a.v12.coarse_angle_deg==17 && std::abs(a.v12.local_angle_deg-17.4)<1e-9);
    assert((a.v12.pool_counts==std::array<std::size_t,3>{{30,30,30}}));
    assert(a.v12.angle_deg==a.joint.angle[a.selection.path_index]);
    assert(a.v12.angle_deg==std::floor(a.v12.angle_deg));
    const double expected=.25*(1-a.v12.residual_ratio)+.75*v12_match(a.v12.angle_deg,a.beam_scan.peak_angle_deg,.05);
    assert(std::abs(a.v12.score-expected)<1e-14);
    assert(!a.beam_scan.fusion.enabled);
    assert(a.v12.angle_deg!=a.v12.scan.peak_angle_deg);
    assert(!a.beam_scan.verification_enabled);
    auto changed=o;changed.scan_ml_weight=.8;changed.max_bearing_residual=0;
    changed.joint_gated_selection=false;changed.joint_quality_selection=true;
    const auto b=analyze(changed,fft,{});
    assert(b.v12.valid && b.v12.angle_deg==a.v12.angle_deg && b.v12.score==a.v12.score);
    assert(b.joint.angle==a.joint.angle && b.joint_score==a.joint_score);
    assert(joint_eligible(changed,b));
    assert(fft.snapshots==before.snapshots && fft.corrected==before.corrected && fft.raw==before.raw && fft.valid==before.valid);
    const auto packet=console_packet(o,CaptureState{},CaptureBatch{},a);
    const auto parsed=aoa_console::Json::parse(packet.dump());
    assert(parsed["v12"]["version"]=="1.2");
    assert(parsed["v12"]["joint"]["secondary"]["display_only"]==true);
    assert(parsed["joint"]["score_grid"]["dimensions"]==a.selection.model_paths);
    assert(parsed["joint"]["selection"]["method"]=="original_ml_top30_softmatch_v1.2");
    Analysis invalid;invalid.v12.enabled=true;
    const auto invalid_packet=console_packet(o,CaptureState{},CaptureBatch{},invalid);
    assert(invalid_packet["joint"]["selection"]["accepted"]==false);
    assert(invalid_packet["joint"]["selection"]["path_index"].is_null());
    assert(invalid_packet["v12"]["joint"]["path_index"].is_null());
    changed.processing_mode="scan6";const auto scan=analyze(changed,fft,{});
    assert(scan.v12.angle_deg==a.v12.angle_deg && scan.v12.score==a.v12.score);
    bool interrupted=false;
    try {analyze(o,fft,[]{return true;});} catch(const std::runtime_error&) {interrupted=true;}
    assert(interrupted);
}
// Serialize the actual largest default ML grid plus all4096 raw IQ samples
// and all4096 spectrum bins, including main.cpp and Publisher wrapper fields.
// Fake finite grid values exercise payload capacity without another ML search.
void packet_size_contract() {
    using aoa_console::Json;
    auto o=options();o.observe_waveform=o.observe_spectrum=true;
    o.tcp.host="192.168.100.200";
    CaptureState capture;capture.complete=true;capture.mode="offline_fft";
    capture.ml_preprocessing_backend="pl_zero64_hann4032";capture.fft_preprocessing="pl_zero64_hann4032";
    CaptureBatch batch;batch.completed=4;batch.run_id=std::string(64,'r');batch.handoff_status="rate_limited";
    batch.rounds.resize(4);
    for(std::size_t p=0;p<6;++p) o.beam.phase_rad[p]=1.2345678901234567+p*.1234567890123456;
    std::vector<std::uint8_t> slots(fft_bytes,0);
    const auto observation=observation_packet(o,capture,batch,slots);
    assert(observation["valid"]==true);
    assert(observation["wave_iq_s32le"].get<std::string>().size()==4*((pha_bytes+2)/3));
    assert(observation["spectrum"]["db_f32le"].get<std::string>().size()==4*((pha_samples*4+2)/3));
    Analysis a;a.complete=true;a.status="ok";a.direction_frames=4;
    a.v12.enabled=a.v12.valid=a.v12.scan.valid=true;a.v12.angle_deg=17;
    a.v12.coarse_angle_deg=17;a.v12.local_angle_deg=17.2;
    a.v12.scan.peak_angle_deg=17.234567890123456;a.v12.secondary_angle_deg=-40;
    a.v12.secondary_ml_angle_deg=-39;a.v12.secondary_error_deg=1;
    a.v12.secondary_score=.12345678901234567;a.v12.residual_ratio=.012345678901234567;
    a.v12.score=.9876543210987654;a.v12.main_match=.8765432109876543;
    a.v12.pool_counts={{30,30,30}};a.v12.selected_rank=30;
    a.bins={4014};a.covariance_concentration=.9876543210987654;
    a.selection.enabled=a.selection.accepted=true;a.selection.model_paths=3;a.selection.path_index=1;
    a.selection.status="v12_original_ml_top30_softmatch";a.selection.selected_score=a.v12.score;
    a.joint.has_best=true;a.joint.channel={0,1,2,3,4,5};a.joint.energy=1234567890123.4567;
    a.joint.residual=1234567890.1234567;a.joint.steering_condition=123.45678901234567;
    a.joint.angle={-39,17,68};a.joint.amplitude_rms={123.45678901234567,234.56789012345678,345.67890123456789};
    a.joint.phase_first_bin_deg={-123.45678901234567,23.456789012345678,123.45678901234567};
    a.joint.active={true,true,true};a.joint_raw_validated=true;
    a.joint_raw_quality.raw_residual=a.joint_raw_quality.latest_residual=.12345678901234567;
    a.joint_score.assign(joint_combinations(o.angle_count,3),254);
    for(std::size_t i=0;i<o.angle_count;++i) {
        const double angle=o.angle_start+i*o.angle_step;
        a.joint_profile_angles.push_back(angle);a.joint_profile_db.push_back(-12.345678901234567);
        a.v12.scan.angles_deg.push_back(angle);a.v12.scan.relative_db.push_back(-12.345678901234567);
    }
    auto envelope=[&](const Options& config,const Analysis& result) {
        auto packet=console_packet(config,capture,batch,result);
        packet["observation"]=observation;
        packet["beam"]=BeamController(false).state();
        packet["beam"]["phase_rad"]=o.beam.phase_rad;
        packet["beam"]["target"]={{"kind","angle"},{"angle_deg",17.234567890123456},
            {"path_id",1},{"source_method","original_ml_top30_softmatch_v1.2"},{"source_run_id",batch.run_id},{"energy_share",nullptr}};
        const auto max=std::numeric_limits<std::uint64_t>::max();
        packet["counts"]={{"attempted",max},{"ipc_sent",max},{"ipc_busy",max},{"ipc_absent",max},{"ipc_errors",max}};
        packet["schema"]=1;packet["source"]="aoa";packet["session"]=std::string(64,'s');
        packet["seq"]=max;packet["sent_unix_ms"]=std::numeric_limits<std::int64_t>::max();
        packet["transport"]={{"sent",max},{"dropped",max},{"failures",max}};
        return packet;
    };
    const auto angle_packet=envelope(o,a);const auto angle_bytes=angle_packet.dump().size();
    assert(angle_packet["joint"]["score_grid"]["score_u8"].get<std::string>().size()==4*((joint_combinations(161,3)+2)/3));
    assert(angle_bytes<=1024*1024);
    o.processing_mode="position";
    for(int id=0;id<2;++id) {
        auto group=a.joint;group.id=id;group.channel.assign(o.groups[id].begin(),o.groups[id].end());
        group.angle=id?std::vector<double>{-40,0}:std::vector<double>{10,60};group.amplitude_rms={123.45678901234567,234.56789012345678};
        group.phase_first_bin_deg={23.456789012345678,-123.45678901234567};group.active={true,true};
        group.display_angles=a.joint_profile_angles;group.display_score.assign(161*161,254);group.display_invalid.assign(161*161,0);
        a.groups.push_back(std::move(group));
    }
    localize_v12(o,a);
    a.v12.scan.angles_deg.clear();a.v12.scan.relative_db.clear();
    const auto position_packet=envelope(o,a);const auto position_bytes=position_packet.dump().size();
    assert(!position_packet["joint"].contains("score_grid"));
    assert(position_packet["groups"].size()==2 && position_packet["groups"][0].contains("grid"));
    assert(position_packet["v12"]["localization"]["candidates"].size()==4);
    assert(position_bytes<=1024*1024);
    // Also exercise the real producer: position never retains the unused
    // all-array grid/curve while preserving the two original triad grids.
    const auto produced=analyze(o,source(o,17.36),{});
    assert(produced.joint_score.empty() && produced.v12.scan.angles_deg.empty() && produced.v12.scan.relative_db.empty());
    assert(produced.v12.position_energy && !produced.v12.valid && !produced.joint.has_best);
    const auto produced_packet=console_packet(o,CaptureState{},CaptureBatch{},produced);
    assert(produced_packet["raw_validation"]==true);
    assert((produced.v12.pool_counts==std::array<std::size_t,3>{{0,0,0}}));
    assert(produced.groups.size()==2 && !produced.groups[0].display_score.empty() && !produced.groups[1].display_score.empty());
    std::cout<<"Packet bytes: angle6 K3+wave4096+spectrum4096="<<angle_bytes
             <<", position two-triad-grids+wave4096+spectrum4096="<<position_bytes<<" (limit1048576)\n";
}
void location_contract() {
    auto o=options();o.min_crossing_angle_deg=1;
    Analysis a;a.v12.enabled=true;a.v12.valid=true;a.v12.angle_deg=0;
    for(int id=0;id<2;++id) {
        GroupEstimate group;group.id=id;group.has_best=true;group.energy=1;group.residual=.001;
        group.angle=id?std::vector<double>{-40,-10}:std::vector<double>{20,50};
        group.active={true,true};group.amplitude_rms={1,.5};a.groups.push_back(group);
    }
    localize_v12(o,a);assert(a.v12.intersections.size()==4 && a.localization.valid);
    double best=-1;std::string chosen;
    for(std::size_t i=0;i<4;++i) {
        const auto& c=a.v12.intersections[i];assert(c.id=="C"+std::to_string(i+1));
        assert(c.paths[0]==int(i/2) && c.paths[1]==int(i%2));assert(c.fix.valid);
        if(c.energy_share>best) {best=c.energy_share;chosen=c.id;}
        assert(std::isnan(c.angle_error_deg));
    }
    assert(a.v12.selected_id==chosen);
    // All four stay materialized when some geometry is invalid.
    a.groups[1].angle[0]=20;localize_v12(o,a);
    assert(a.v12.intersections.size()==4);
    assert(!a.v12.intersections[0].fix.valid && a.v12.intersections[0].fix.status=="near_parallel");
    // C2 has the strongest independent triad paths. The joint reference must
    // not pull POSITION or automatic beam control toward C3.
    a.complete=true;a.status="ok";a.v12.angle_deg=20;
    a.groups[0].angle={10,60};a.groups[0].amplitude_rms={10,1};
    a.groups[1].angle={-40,0};a.groups[1].amplitude_rms={1,10};
    localize_v12(o,a);assert(a.v12.selected_id=="C2");
    const auto old=strongest_beam_candidate(o,a);assert((old.paths==std::array<int,2>{{0,1}}));
    const auto automatic=automatic_position_candidate(o,a);
    assert(automatic.fix.valid && (automatic.paths==std::array<int,2>{{0,1}}));
    assert(automatic.energy_share==a.v12.intersections[1].energy_share);
    assert(automatic.fix.x==a.localization.x && automatic.fix.y==a.localization.y);
    a.v12.valid=false;localize_v12(o,a);
    assert(a.v12.intersections.size()==4 && a.v12.selected_id=="C2" && a.localization.valid);
    assert(automatic_position_candidate(o,a).fix.valid);
    const auto wire=aoa_console::Json::parse(v12_json(a));
    assert(wire["localization"]["selection_method"]=="triad_ml_energy");
    assert(wire["localization"]["reference_angle_deg"].is_null());
    assert(!wire["localization"].contains("residual_weight"));
    assert(!wire["localization"].contains("angle_sigma_deg"));
}
void location_diagnostic_contract() {
    auto o=options();o.processing_mode="position";
    Analysis a;a.complete=true;a.status="ok";a.v12.enabled=a.v12.valid=true;a.v12.angle_deg=0;
    for(int id=0;id<2;++id) {
        GroupEstimate group;group.id=id;group.has_best=true;group.energy=1;group.residual=.001;
        group.channel.assign(o.groups[id].begin(),o.groups[id].end());
        group.angle=id?std::vector<double>{-40,-10}:std::vector<double>{20,50};
        group.active={true,true};group.amplitude_rms={1,.5};group.phase_first_bin_deg={0,0};
        a.groups.push_back(std::move(group));
    }
    auto all_diagnostics=[&](const std::string& status) {
        localize_v12(o,a);
        assert(a.v12.intersections.size()==4 && !a.localization.valid && a.v12.selected_id.empty());
        for(const auto& c:a.v12.intersections) {
            assert(!c.fix.valid && c.fix.status==status);
            assert(std::isfinite(c.fix.x) && std::isfinite(c.fix.y));
            assert(std::isfinite(c.bearing_deg) && std::isnan(c.angle_error_deg));
        }
        const auto wire=aoa_console::Json::parse(v12_json(a));
        assert(wire["localization"]["selected_id"].is_null());
        for(const auto& c:wire["localization"]["candidates"]) {
            assert(c["valid"]==false && c["x"].is_number() && c["y"].is_number());
        }
        assert(!automatic_position_candidate(o,a).fix.valid);
    };
    a.groups[0].ambiguous=true;all_diagnostics("ambiguous_fit");
    a.groups[0].ambiguous=false;a.groups[0].raw_validated=true;
    a.groups[0].raw_residual_ratio=.2;a.groups[0].latest_residual_ratio=.01;all_diagnostics("poor_fit");
    a.groups[0].raw_residual_ratio=.01;a.groups[0].latest_residual_ratio=.2;all_diagnostics("poor_fit");
    a.groups[0].raw_validated=false;a.groups[0].residual=.2;all_diagnostics("poor_fit");
    a.groups[0].residual=.001;a.groups[0].active[0]=false;
    localize_v12(o,a);a.v12.angle_deg=a.v12.intersections[0].bearing_deg;localize_v12(o,a);
    for(int i=0;i<2;++i) {
        const auto& c=a.v12.intersections[i];
        assert(!c.fix.valid && c.fix.status=="weak_path" && std::isfinite(c.fix.x));
    }
    assert(a.localization.valid && (a.v12.selected_id=="C3" || a.v12.selected_id=="C4"));
    // Geometric status takes precedence over quality, as in the original PC.
    a.groups[0].active[0]=true;a.groups[0].ambiguous=true;a.groups[1].angle[0]=20;
    localize_v12(o,a);
    assert(a.v12.intersections[0].fix.status=="near_parallel" && !std::isfinite(a.v12.intersections[0].fix.x));
    a.groups[0].ambiguous=false;a.groups[1].angle[0]=-40;
    auto below=o;for(auto& ant:below.antenna) ant.y-=100;
    localize_v12(below,a);
    for(const auto& c:a.v12.intersections)
        assert(!c.fix.valid && c.fix.status=="behind_station" && !std::isfinite(c.fix.x));
    assert(!a.localization.valid && a.v12.selected_id.empty());
    // Preserve the PC's absolute determinant floor even at tiny configured
    // crossing angles; this is an existing geometry rule, not a new cutoff.
    o.min_crossing_angle_deg=1e-15;a.groups[1].angle[0]=20+1e-12;
    localize_v12(o,a);
    assert(a.v12.intersections[0].fix.crossing_angle_deg>o.min_crossing_angle_deg);
    assert(a.v12.intersections[0].fix.status=="near_parallel");
}

}
int main() {
    pool_contract();pipeline_contract();location_contract();location_diagnostic_contract();packet_size_contract();
    std::cout<<"PASS V1.2 exact residual Top30, full-complex precision scan, ML output, score, schema, all-four localization\n";
}
