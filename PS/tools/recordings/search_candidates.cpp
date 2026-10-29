// Independent experiment: retain candidates from the unchanged same-order QR objective.
#include "candidate_hook.hpp"
#include "batch_covariance.hpp"
#include "recording.hpp"
#include <fstream>
#include <iostream>

int run(const std::filesystem::path& capture,const std::filesystem::path& replay,
        const std::filesystem::path& output) {
    using namespace aoa_app;using Json=nlohmann::json;
    try {
        Json c,r;std::ifstream in(capture),ri(replay);in>>c;ri>>r;
        if(c.at("run_id")!=r.at("run_id")) throw std::runtime_error("Capture/replay run mismatch");
        auto o=recording_replay_options(c.at("options"));
        if(o.processing_mode!="angle6" || r.at("selected_snapshots").size()!=4)
            throw std::runtime_error("Need an angle6 four-frame recording");
        const auto& joint=r.at("replayed_result").at("joint");
        o.joint_paths=joint.at("path_count");o.console_enabled=false;o.save_results=false;
        o.joint_gated_selection=false;o.joint_quality_selection=false;
        const auto bins=r.at("replayed_result").at("selected_bins").get<std::vector<std::size_t>>();
        if(bins.empty()) throw std::runtime_error("No selected direction bins");
        FftData fft;fft.corrected.resize(fft_records);double concentration=1;
        for(std::size_t b=0;b<bins.size();++b) {
            std::vector<SpatialVector> frames(4);
            for(std::size_t t=0;t<4;++t) {
                const auto& row=r.at("selected_snapshots").at(t).at("selected").at(b);
                if(row.at("bin")!=bins[b]) throw std::runtime_error("Selected bin order mismatch");
                for(std::size_t ch=0;ch<channels;++ch) {
                    const auto& z=row.at("six_channel_iq").at(ch);
                    frames[t][ch]={z.at(0).get<double>(),z.at(1).get<double>()};
                }
            }
            double fraction=0;const auto y=covariance_observation(frames,fraction);
            concentration=std::min(concentration,fraction);
            for(std::size_t ch=0;ch<channels;++ch) fft.corrected[ch*fft_length+bins[b]]=y[ch];
        }
        Analysis result;result.bins=bins;std::vector<beam_test::RankedCandidate> pool;
        beam_test::retained_search(o,fft,result,{},pool);
        Analysis reference;reference.bins=bins;analyze_joint_search(o,fft,reference,{});
        if(reference.joint.angle!=result.joint.angle || reference.joint.residual!=result.joint.residual ||
           reference.joint.amplitude_rms!=result.joint.amplitude_rms)
            throw std::runtime_error("Candidate instrumentation differs from original search");
        Json candidates=Json::array();
        for(const auto& candidate:pool) {
            Json angles=Json::array();
            for(int k=0;k<o.joint_paths;++k) angles.push_back(o.angle_start+candidate.second[k]*o.angle_step);
            candidates.push_back({{"angles_deg",angles},{"residual_ratio",candidate.first/result.joint.energy}});
        }
        Json out={{"schema","aoa.beam_candidates.v1"},{"run_id",c.at("run_id")},
            {"original_search_matched",true},{"search_source_sha256",AOA_SEARCH_SOURCE_SHA},
            {"path_count",o.joint_paths},{"bins",bins},{"concentration",concentration},
            {"pool",candidates},{"residual_only_angles_deg",result.joint.angle},
            {"production_joint",joint}};
        std::ofstream stream(output);stream.exceptions(std::ios::badbit|std::ios::failbit);stream<<out.dump(2)<<'\n';
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
#ifdef _WIN32
int wmain(int argc,wchar_t** argv) {
#else
int main(int argc,char** argv) {
#endif
    if(argc!=4) {std::cerr<<"Usage: aoa_beam_candidates CAPTURE REPLAY OUTPUT\n";return 2;}
    return run(argv[1],argv[2],argv[3]);
}
