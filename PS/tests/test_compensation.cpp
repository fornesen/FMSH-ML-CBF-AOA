#ifdef NDEBUG
#undef NDEBUG
#endif
#include "app.hpp"
#include "beam_weights.hpp"
#include "pl_contract.hpp"
#include "recording.hpp"
#include <cassert>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sstream>

using namespace aoa_app;
namespace {
namespace fs = std::filesystem;
const std::array<AoaComplex, channels> gains{{{1,0},{0,1},{-1,0},{0,-1},{2,0},{0,.5}}};
const std::array<double, channels> phase_center_dx{{0,0,0,.042,0,0}};

Options parse(std::vector<std::string> arguments) {
    arguments.insert(arguments.begin(), "compensation-test");
    std::vector<char*> argv;
    for (auto& argument : arguments) argv.push_back(argument.data());
    return parse_options(static_cast<int>(argv.size()), argv.data());
}
void write(const fs::path& path, const std::string& value) {
    std::ofstream output(path);
    output << value;
    if (!output) throw std::runtime_error("Cannot write compensation test fixture");
}
std::string csv(const std::array<AoaComplex, channels>& values = gains, double rf = 445e6,
                const std::array<double, channels>& dx = {}) {
    std::ostringstream out;
    out << std::setprecision(17) << "channel,rf_hz,correction_real,correction_imag,phase_center_dx_m\n";
    // Physical channel identity, rather than file order, determines calibration.
    for (int ch = static_cast<int>(channels)-1; ch >= 0; --ch)
        out << ch << ',' << rf << ',' << values[ch].real() << ',' << values[ch].imag() << ',' << dx[ch] << '\n';
    return out.str();
}
std::string array_csv(bool legacy) {
    std::ostringstream out;
    out << std::setprecision(17) << "channel,x_m,y_m,correction_real,correction_imag\n";
    for (std::size_t ch = 0; ch < channels; ++ch) {
        const double x = -.85 + .34*ch, y = .007*ch;
        out << ch << ',' << x << ',' << y << ',' << (legacy ? 1.3+.1*ch : 1)
            << ',' << (legacy ? -.2-.03*ch : 0) << '\n';
    }
    return out.str();
}
std::vector<std::string> arguments(const fs::path& array, const fs::path& compensation = {}) {
    std::vector<std::string> result{"--no-ipc", "--rf-hz", "445000000", "--array", array.string(),
        "--bins", "4014", "--band-min-hz", "-550000", "--band-max-hz", "-450000",
        "--angle-start", "-60", "--angle-step", "10", "--angle-count", "13"};
    if (!compensation.empty()) {
        result.push_back("--compensation"); result.push_back(compensation.string());
    }
    return result;
}
void rejected(const std::vector<std::string>& args) {
    bool failed = false;
    try { parse(args); } catch (const std::exception&) { failed = true; }
    assert(failed);
}
void configuration_contract(const fs::path& directory, const fs::path& legacy,
                            const fs::path& compensation) {
    const auto original = parse(arguments(legacy));
    assert(original.compensation_file.empty());
    assert(std::abs(original.antenna[4].correction-AoaComplex(1.7,-.32))<1e-14);
    const auto loaded = parse(arguments(legacy, compensation));
    const auto ordered = parse({"--no-ipc", "--compensation", compensation.string(),
                               "--array", legacy.string(), "--rf-hz", "445000000"});
    for (std::size_t ch = 0; ch < channels; ++ch) {
        assert(loaded.antenna[ch].correction == gains[ch]);
        assert(ordered.antenna[ch].correction == gains[ch]);
        assert(loaded.antenna[ch].x == original.antenna[ch].x);
        assert(loaded.antenna[ch].y == original.antenna[ch].y);
    }
    // Zero offsets preserve the classic layout; a calibrated effective centre is
    // applied once, after the physical layout, regardless of CLI argument order.
    write(compensation, csv(gains,445e6,phase_center_dx));
    const auto shifted = parse(arguments(legacy,compensation));
    const auto shifted_first = parse({"--compensation",compensation.string(),"--array",legacy.string(),
                                      "--rf-hz","445000000","--no-ipc"});
    auto repeated_args = arguments(legacy,compensation);
    repeated_args.insert(repeated_args.end(),{"--compensation",compensation.string()});
    const auto repeated = parse(repeated_args);
    for (std::size_t ch = 0; ch < channels; ++ch) {
        const double x = original.antenna[ch].x+phase_center_dx[ch];
        assert(shifted.antenna[ch].x == x && shifted_first.antenna[ch].x == x && repeated.antenna[ch].x == x);
        assert(shifted.antenna[ch].y == original.antenna[ch].y);
        assert(shifted.antenna[ch].correction == gains[ch]);
    }
    const auto centres=group_centres(shifted), physical_centres=group_centres(original);
    assert(std::abs(centres[1][0]-physical_centres[1][0]-.014)<1e-14);
    // Raw evidence freezes effective coordinates, rather than reapplying dx to
    // a later version of the physical array or compensation file during replay.
    const auto replay=recording_replay_options(recording_options(shifted));
    for (std::size_t ch = 0; ch < channels; ++ch) {
        assert(replay.antenna[ch].x == shifted.antenna[ch].x);
        assert(replay.antenna[ch].correction == shifted.antenna[ch].correction);
    }
    auto exported=shifted;exported.save_results=true;exported.prefix=(directory/"effective").string();
    export_metadata(exported,{}, {}, {});
    std::ifstream metadata_input(exported.prefix+"_metadata.json");
    RecordingJson metadata;metadata_input>>metadata;
    for (std::size_t ch = 0; ch < channels; ++ch) {
        assert(metadata.at("positions_x_m").at(ch).get<double>() == shifted.antenna[ch].x);
        assert(metadata.at("correction_real").at(ch).get<double>() == gains[ch].real());
        assert(metadata.at("correction_imag").at(ch).get<double>() == gains[ch].imag());
    }
    assert(parse(arguments(legacy)).antenna[3].x == original.antenna[3].x);
    // Updating the file changes the next startup without rebuilding the program.
    auto replacement = gains; replacement[3] = {1.25,.75};
    auto changed_dx=phase_center_dx;changed_dx[3]=.025;
    write(compensation, csv(replacement,445e6,changed_dx));
    const auto changed=parse(arguments(legacy,compensation));
    assert(changed.antenna[3].correction == replacement[3]);
    assert(changed.antenna[3].x == original.antenna[3].x+.025);
    write(compensation, csv(gains,445e6,phase_center_dx));
    assert(parse(arguments(legacy,compensation)).antenna[3].x == shifted.antenna[3].x);

    rejected(arguments(legacy, directory/"missing.csv"));
    auto empty_path = arguments(legacy);
    empty_path.insert(empty_path.end(), {"--compensation", ""}); rejected(empty_path);
    const auto invalid = directory/"invalid.csv";
    std::vector<std::string> failures{"", "channel,rf_hz,correction_real,correction_imag\n", csv(gains,433e6), csv(gains,0)};
    auto rows = csv();
    failures.push_back(rows.substr(0,rows.find("0,445000000"))); // missing CH0
    failures.push_back(rows+"0,445000000,1,0,0\n");
    for (const auto& row : {"6,445000000,1,0,0\n", "-1,445000000,1,0,0\n", "0.5,445000000,1,0,0\n",
                            "0,445000000,nan,0,0\n", "0,445000000,1,inf,0\n", "0,445000000,0,0,0\n",
                            "0,445000000,1e308,1e308,0\n", "0,inf,1,0,0\n",
                            "0,445000000,1,0,nan\n", "0,445000000,1,0,inf\n", "0,445000000,1,0,1e308\n",
                            "0,445000000,1,0\n", "0,445000000,1,0,\n", "0,445000000,1,0,0,extra\n"}) {
        const auto cut = rows.find("0,445000000");
        failures.push_back(rows.substr(0,cut)+row);
    }
    for (const auto& failure : failures) { write(invalid,failure); rejected(arguments(legacy,invalid)); }
}
void pack(std::vector<std::uint8_t>& data, std::size_t offset, std::int64_t value) {
    const auto word = static_cast<std::uint64_t>(value)&((1ULL<<39)-1);
    for (std::size_t i=0;i<5;++i) data[offset+i]=static_cast<std::uint8_t>(word>>(8*i));
}
FftData observations(const Options& options, const std::array<Antenna,channels>& truth,
                     bool distorted, bool windowed) {
    FftData result;
    const double theta = 17.3*pi/180, k = 2*pi*options.rf_hz/light_speed;
    const std::array<AoaComplex,4> rotations{{{1,0},{0,1},{-1,0},{0,-1}}};
    for (const auto rotation : rotations) {
        std::vector<std::uint8_t> data(fft_bytes,0);
        for (std::size_t ch=0;ch<channels;++ch) {
            const auto wave=std::polar(10000.0,k*(truth[ch].x*std::sin(theta)+truth[ch].y*std::cos(theta)));
            // Even Gaussian integers make the injected gains exactly reversible.
            const AoaComplex ideal{2*std::round(wave.real()/2),2*std::round(wave.imag()/2)};
            const auto raw=ideal*rotation/(distorted?gains[ch]:AoaComplex(1,0));
            const auto offset=(ch*fft_length+4014)*slot_bytes;
            pack(data,offset,std::llround(raw.real()));
            pack(data,offset+5,std::llround(raw.imag()));
        }
        CaptureState capture;capture.mode="hardware";
        capture.pl_version=windowed?windowed_fft_pl_version:legacy_pl_version;
        auto frame=decode_fft(options,data,capture);
        result.raw=std::move(frame.raw); result.corrected=std::move(frame.corrected);
        result.valid=std::move(frame.valid); result.snapshots.push_back(result.corrected);
    }
    return result;
}
void pipeline_contract(const fs::path& unity, const fs::path& legacy, const fs::path& compensation) {
    for (const int preprocessing : {0,1,2}) {
        auto ideal=parse(arguments(unity));
        auto corrected=parse(arguments(legacy,compensation));
        for (std::size_t ch=0;ch<channels;++ch) ideal.antenna[ch].x+=phase_center_dx[ch];
        if (preprocessing) {
            ideal.ml_startup_samples=corrected.ml_startup_samples=64;
            ideal.ml_fft_hann=corrected.ml_fft_hann=true;
        }
        const auto baseline=observations(ideal,ideal.antenna,false,preprocessing==2);
        const auto restored=observations(corrected,ideal.antenna,true,preprocessing==2);
        assert(restored.raw!=baseline.raw);
        for (std::size_t t=0;t<npu_rounds;++t) for (std::size_t record=0;record<fft_records;++record)
            assert(std::abs(restored.snapshots[t][record]-baseline.snapshots[t][record])<1e-10);
        for (const auto* mode : {"position","angle6"}) {
            ideal.processing_mode=corrected.processing_mode=mode;
            const auto expected=analyze(ideal,baseline,{}), actual=analyze(corrected,restored,{});
            assert(actual.complete && expected.complete && actual.bins==expected.bins);
            assert(actual.groups.size()==expected.groups.size());
            if (std::string(mode)=="position") {
                assert(actual.v12.position_energy && !actual.v12.valid &&
                       !actual.joint.has_best && actual.groups.size()==2);
                for (std::size_t g=0;g<2;++g) {
                    assert(actual.groups[g].angle==expected.groups[g].angle);
                    assert(std::abs(actual.groups[g].residual-expected.groups[g].residual)<1e-9);
                }
            } else {
                assert(actual.v12.enabled && actual.groups.empty());
                assert(actual.v12.valid && expected.v12.valid);
                assert(actual.v12.angle_deg==expected.v12.angle_deg);
                assert(std::abs(actual.v12.score-expected.v12.score)<1e-10);
                assert(std::abs(actual.beam_scan.peak_angle_deg-expected.beam_scan.peak_angle_deg)<1e-9);
            }
        }
    }
}
void beam_phase_contract(const fs::path& legacy, const fs::path& compensation) {
    const auto options=parse(arguments(legacy,compensation));
    auto truth=parse(arguments(legacy));
    for (std::size_t ch=0;ch<channels;++ch) truth.antenna[ch].x+=phase_center_dx[ch];
    const double angle=17.3,theta=angle*pi/180,k=2*pi*options.rf_hz/light_speed;
    const double x=.2,y=3;
    for (const auto* mode : {"manual","follow"}) {
        const auto weights=beam_weights(options,mode,angle,x,y);
        AoaComplex reference;
        for (std::size_t ch=0;ch<channels;++ch) {
            const auto& ant=truth.antenna[ch];
            const double phase=std::string(mode)=="manual"?
                k*(ant.x*std::sin(theta)+ant.y*std::cos(theta)):-k*std::hypot(x-ant.x,y-ant.y);
            const auto field=std::polar(1.0,phase)/gains[ch];
            const auto aligned=field*std::polar(1.0,weights.phase_rad[ch]);
            const auto normalized=aligned/std::abs(aligned);
            if (!ch) reference=normalized;
            assert(std::abs(normalized-reference)<1e-12);
        }
    }
}
}
int main(int argc,char** argv) {
    const auto base=argc>1?fs::path(argv[1]):fs::temp_directory_path()/"aoa-compensation-fixtures";
    const auto directory=base/std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count());
    fs::create_directories(directory);
    const auto legacy=directory/"legacy.csv", unity=directory/"unity.csv", compensation=directory/"channels.csv";
    write(legacy,array_csv(true));write(unity,array_csv(false));write(compensation,csv());
    configuration_contract(directory,legacy,compensation);
    pipeline_contract(unity,legacy,compensation);
    beam_phase_contract(legacy,compensation);
    std::cout<<"Compensation file: runtime reload, RF/channel validation, absolute gains, phase-centre dx, frozen replay, raw/PS-Hann/PL-Hann four-frame POSITION/ANGLE and beam phase OK\n";
}
