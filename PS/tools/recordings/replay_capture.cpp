// Offline PS replay. No TCP request, SDK device, IPC publication or PL configuration.
#include "app.hpp"
#include "console_aoa.hpp"
#include "recording.hpp"
#include "pl_fft_input.hpp"
#include "pl_contract.hpp"
#include "beam_scan_verify.hpp"
#include <iostream>
#include <filesystem>
#include <stdexcept>

namespace aoa_app {
double bin_hz(std::size_t bin,double rate) {return pl_fft_bin_hz(bin,rate/4);}
std::uint32_t dds_word(const Options& o) {
    return static_cast<std::uint32_t>(std::llround(o.dds_hz/o.sample_rate*4294967296.0));
}
}
namespace {
std::filesystem::path artifact_path(const std::filesystem::path& root,const nlohmann::json& file) {
    const auto name=file.at("name").get<std::string>();
    if(name.empty() || name.find_first_of("/\\:")!=std::string::npos || name=="." || name=="..")
        throw std::runtime_error("Recording artifact needs a local filename");
    return root/name;
}
std::vector<std::uint8_t> recorded_bytes(const std::filesystem::path& path,std::size_t size) {
    std::ifstream in(path,std::ios::binary|std::ios::ate);
    if(!in || in.tellg()!=static_cast<std::streamoff>(size))
        throw std::runtime_error("Recorded artifact has the wrong byte count");
    std::vector<std::uint8_t> bytes(size);in.seekg(0);
    in.read(reinterpret_cast<char*>(bytes.data()),static_cast<std::streamsize>(size));
    if(!in) throw std::runtime_error("Cannot read recorded artifact");
    return bytes;
}
}
int replay_recording(const std::filesystem::path& source,const std::filesystem::path& destination,const std::string& mode={}) {
    using namespace aoa_app;using Json=nlohmann::json;
    try {
        Json record;std::ifstream input(source);if(!input) throw std::runtime_error("Cannot open recording");input>>record;
        if(record.at("schema")!="aoa.raw_capture.v1") throw std::runtime_error("Unsupported recording schema");
        auto o=recording_replay_options(record.at("options"));
        // Recording implies save_results in the producer; regenerate its score
        // grids without enabling filesystem export in this offline process.
        o.console_enabled=true;
        if(!mode.empty()) {
            if(mode!="angle6" && mode!="scan6" && mode!="position") throw std::runtime_error("Unknown replay mode override");
            o.processing_mode=mode;
        }
        if(record.at("completed_rounds")!=record.at("expected_rounds") || record.at("expected_rounds")!=4 ||
           record.at("rounds").size()!=4 || !record.at("result").value("complete",false))
            throw std::runtime_error("Incomplete recording cannot reproduce a complete measurement");
        o.fft_raw="recording";
        const auto root=std::filesystem::absolute(source).parent_path();
        CaptureBatch batch;batch.run_id=record.at("run_id");CaptureState final;FftData fft;
        Json adc_checks=Json::array();
        for(std::size_t index=0;index<4;++index) {
            const auto& round=record.at("rounds").at(index);
            if(round.at("run_id")!=batch.run_id || round.at("index")!=index || !round.at("complete").get<bool>())
                throw std::runtime_error("Round identity/order/completeness mismatch");
            const auto& files=round.at("files");
            if(!files.at("adc").at("bytes").is_null()) {
                const auto adc=recorded_bytes(artifact_path(root,files.at("adc")),adc_bytes);
                const auto expected=recorded_bytes(artifact_path(root,files.at("pl_input")),input_bytes);
                std::array<std::int32_t,channels> dc{};
                if(adc_to_slots(o,adc,&dc)!=expected || Json(dc)!=round.at("adc_dc_removed"))
                    throw std::runtime_error("ADC-to-PL input/DC replay differs from recorded bytes");
                adc_checks.push_back({{"round",index},{"byte_exact",true}});
            } else adc_checks.push_back({{"round",index},{"byte_exact",nullptr}});
            const auto slots=recorded_bytes(artifact_path(root,files.at("pl_output")),fft_bytes);
            CaptureState state;state.mode="offline_fft";state.pl_version=round.at("pl_version");
            o.fft_preprocessing=round.at("fft_preprocessing").get<std::string>();
            if(o.fft_preprocessing!="raw" && o.fft_preprocessing!="pl-hann64")
                throw std::runtime_error("Unknown recorded FFT preprocessing contract");
            if(round.at("mode")=="hardware" &&
               (!supported_pl_version(state.pl_version) ||
                (state.pl_version==windowed_fft_pl_version)!=(o.fft_preprocessing=="pl-hann64")))
                throw std::runtime_error("Recorded PL version and FFT payload contract disagree");
            auto decoded=decode_fft(o,slots,state);
            if(!state.complete) throw std::runtime_error("Invalid PL slots in recording");
            if(!files.at("pha").at("bytes").is_null()) {
                const auto pha=recorded_bytes(artifact_path(root,files.at("pha")),pha_bytes);
                for(std::size_t sample=0;sample<pha_samples;++sample)
                    if(!std::equal(pha.begin()+sample*8,pha.begin()+(sample+1)*8,slots.begin()+sample*slot_bytes+pha_offset))
                        throw std::runtime_error("PHA file differs from paired PL output");
            }
            batch.rounds.push_back(state);++batch.completed;fft.snapshots.push_back(decoded.corrected);
            fft.raw=std::move(decoded.raw);fft.corrected=std::move(decoded.corrected);fft.valid=std::move(decoded.valid);final=state;
        }
        Json checks=Json::array();
        if(record.contains("scan_checks")) for(const auto& check:record.at("scan_checks").at("checks")) {
            if(check.at("npu_eligible")!=false) throw std::runtime_error("Scan check entered NPU input");
            if(record.at("scan_checks").at("source_round")!=3 ||
               check.at("capture_id")!=Json(batch.run_id+"_scancheck"+std::to_string(check.at("candidate_id").get<int>())))
                throw std::runtime_error("Scan check parent/round identity mismatch");
            const auto output_name=check.at("fft_file").get<std::string>();
            const auto pha_name=check.at("pha_file").get<std::string>();
            if(output_name.empty() || pha_name.empty()) {
                if(check.at("valid").get<bool>()) throw std::runtime_error("Valid scan check is missing raw evidence");
                continue;
            }
            if(check.at("source_input_file")!=record.at("rounds").at(3).at("files").at("pl_input").at("name"))
                throw std::runtime_error("Scan check references a different ADC input");
            auto path=[&](const std::string& name){return artifact_path(root,Json{{"name",name}});};
            const auto slots=recorded_bytes(path(output_name),fft_bytes);
            const auto pha=recorded_bytes(path(pha_name),pha_bytes);
            for(std::size_t i=0;i<pha_samples;++i)
                if(!std::equal(pha.begin()+i*8,pha.begin()+(i+1)*8,slots.begin()+i*slot_bytes+pha_offset))
                    throw std::runtime_error("Scan check PHA differs from its PL output");
            CaptureState cs;cs.mode="offline_fft";
            const auto decoded=decode_fft(o,slots,cs);
            if(!cs.complete) throw std::runtime_error("Invalid scan check slots");
            double e=0,p=0;
            for(std::size_t i=0;i<fft_records;++i) {e+=std::norm(decoded.corrected[i]-fft.corrected[i]);p+=std::norm(fft.corrected[i]);}
            const auto bins=record.at("result").at("selected_bins").get<std::vector<std::size_t>>();
            const double angle=check.at("angle_deg");
            const double expected=scan_power(o,fft.corrected,bins,angle),observed=scan_pha_power(o,pha,bins);
            const double shape=std::sqrt(e/std::max(p,1e-300));
            const double relative=std::abs(observed-expected)/std::max(expected,1e-300);
            auto equal=[](double a,double b){return std::abs(a-b)<=1e-8*std::max({1.0,std::abs(a),std::abs(b)});};
            if(!equal(expected,check.at("expected_power")) || !equal(observed,check.at("observed_power")) ||
               !equal(shape,check.at("input_shape_error")) || !equal(relative,check.at("relative_error")))
                throw std::runtime_error("Scan check numeric replay mismatch");
            checks.push_back({{"capture_id",check.at("capture_id")},{"numeric_match",true},
                {"expected_power",expected},{"observed_power",observed},{"input_shape_error",shape},{"relative_error",relative}});
        }
        const auto analysis=analyze(o,fft,{});
        auto packet=console_packet(o,final,batch,analysis);packet["localization_result"]=recording_localization(analysis);
        Json snapshots=Json::array();
        for(std::size_t t=0;t<4;++t) {
            Json bins=Json::array();
            for(const auto bin:analysis.bins) {
                Json y=Json::array();for(std::size_t ch=0;ch<channels;++ch) {
                    const auto z=fft.snapshots[t][ch*fft_length+bin];y.push_back({z.real(),z.imag()});
                }
                bins.push_back({{"bin",bin},{"six_channel_iq",y}});
            }
            snapshots.push_back({{"round",t},{"selected",bins}});
        }
        Json output={{"schema","aoa.ps_replay.v1"},{"run_id",batch.run_id},{"adc_input_checks",adc_checks},
            {"replayed_result",packet},{"selected_snapshots",snapshots},{"scan_check_replays",checks},
            {"scope","replays recorded PL bytes through current PS; does not simulate PL fixed-point RTL or run NPU"}};
        std::ofstream out(destination);out.exceptions(std::ios::badbit|std::ios::failbit);out<<output.dump(2)<<'\n';out.close();
        std::cout<<"Replayed "<<batch.run_id<<" with "<<analysis.direction_frames<<" frames.\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<"Replay error: "<<e.what()<<'\n';return 1;}
}

#ifdef _WIN32
int wmain(int argc,wchar_t** argv) {
#else
int main(int argc,char** argv) {
#endif
    if(argc!=3 && argc!=4) {std::cerr<<"Usage: aoa_capture_replay CAPTURE.json OUTPUT.json\n";return 2;}
    return replay_recording(argv[1],argv[2],argc==4?std::filesystem::path(argv[3]).string():std::string{});
}
