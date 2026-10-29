#include "app.hpp"
#include "pl_fft_input.hpp"
#include "ml_preprocessing.hpp"
#include "beam_registers.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <thread>
#ifndef ICRAFT_AOA_HOST_ONLY
#include "icraft_device_lock.hpp" // [板端联调修正] 同一 DMA 的跨进程互斥。
#include <icraft-xrt/core/device.h>
#include <icraft-xrt/dev/zg330_device.h>
#endif

namespace aoa_app {
bool slot_valid(const std::uint8_t* slot) {
    // Bytes 16..23 are PHA payload, validated/extracted separately for a batch.
    return std::all_of(slot + 10, slot + 16, [](auto v) { return v == 0; }) &&
           std::all_of(slot + 24, slot + 64, [](auto v) { return v == 0; });
}
std::vector<std::uint8_t> load_exact(const std::string& path, std::size_t size) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in || in.tellg() != static_cast<std::streamoff>(size))
        throw std::runtime_error("Raw file must contain exactly " + std::to_string(size) + " bytes: " + path);
    std::vector<std::uint8_t> data(size);
    in.seekg(0); in.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(size));
    if (!in) throw std::runtime_error("Cannot read raw file: " + path);
    return data;
}
std::vector<std::uint8_t> adc_to_slots(const Options& o, const std::vector<std::uint8_t>& data,
                                    std::array<std::int32_t,channels>* removed_dc) {
    if (data.size() != adc_bytes) throw std::runtime_error("Incomplete ADC frame cannot be submitted to PL");
    if(o.adc_bits!=12 && o.adc_bits!=14 && o.adc_bits!=16)
        throw std::invalid_argument("Invalid ADC significant width");
    const std::uint32_t mask=(1u<<o.adc_bits)-1,sign=1u<<(o.adc_bits-1);
    std::vector<std::uint8_t> result(input_bytes, 0);
    std::array<std::int64_t,channels> sums{};
    std::array<std::int32_t,channels> dc{};
    for (std::size_t sample = 0; sample < adc_samples; ++sample) {
        for (std::size_t ch = 0; ch < channels; ++ch) {
            const auto src = sample * adc_payload_bytes + ch * 2;
            const auto dest = sample * slot_bytes + ch * 2;
            std::uint32_t word=data[src+(o.big_endian?1:0)] |
                (std::uint32_t(data[src+(o.big_endian?0:1)])<<8);
            // [7100实采接口修正] 原始12位补码0xF98应为-104，不能当成+3992。
            // 检查填充位，再按实际位宽解码；16位默认仍与原字节转换完全一致。
            if(word&~mask) throw std::runtime_error("ADC payload has nonzero padding bits");
            if(o.offset_binary) word^=sign;
            if(word&sign) word|=(~mask)&0xffffu;
            sums[ch]+=word>=0x8000u?static_cast<std::int32_t>(word)-65536:static_cast<std::int32_t>(word);
            result[dest]=static_cast<std::uint8_t>(word);
            result[dest+1]=static_cast<std::uint8_t>(word>>8);
        }
    }
    if(o.adc_remove_dc) {
        for(std::size_t ch=0;ch<channels;++ch)
            dc[ch]=static_cast<std::int32_t>(std::llround(static_cast<double>(sums[ch])/adc_samples));
        for(std::size_t s=0;s<adc_samples;++s) for(std::size_t ch=0;ch<channels;++ch) {
            const auto dest=s*slot_bytes+ch*2;
            const std::uint32_t word=result[dest]|(std::uint32_t(result[dest+1])<<8);
            const auto value=(word>=0x8000u?static_cast<std::int32_t>(word)-65536:
                              static_cast<std::int32_t>(word))-dc[ch];
            if(value<-32768 || value>32767)
                throw std::runtime_error("ADC DC removal exceeds signed16 input range");
            const auto packed=static_cast<std::uint16_t>(value);
            result[dest]=static_cast<std::uint8_t>(packed);
            result[dest+1]=static_cast<std::uint8_t>(packed>>8);
        }
    }
    if(removed_dc) *removed_dc=dc;
    return result;
}
FftData decode_fft(const Options& o, const std::vector<std::uint8_t>& data, CaptureState& state) {
    if (data.size() < fft_bytes) throw std::runtime_error("FFT buffer is shorter than 24576 slots");
    const bool windowed_in_pl = ml_window_in_pl(o, state);
    state.fft_preprocessing = windowed_in_pl ? "pl-hann64" : "raw";
    FftData fft;
    fft.raw.resize(fft_records); fft.corrected.resize(fft_records); fft.valid.resize(fft_records);
    state.written_slots = 0;
    const double nan = std::numeric_limits<double>::quiet_NaN();
    for (std::size_t record = 0; record < fft_records; ++record) {
        const auto* slot = data.data() + record * slot_bytes;
        fft.valid[record] = slot_valid(slot);
        if (fft.valid[record]) {
            ++state.written_slots;
            fft.raw[record] = pl_fft_decode80(slot);
            fft.corrected[record] = fft.raw[record] * o.antenna[record/fft_length].correction * o.fft_scale;
            if (!std::isfinite(std::norm(fft.corrected[record])))
                throw std::runtime_error("Calibrated FFT power overflow");
        } else fft.raw[record] = fft.corrected[record] = {nan, nan};
    }
    // For offline raw this establishes structural validity only, not hardware frame ordering.
    if (state.mode == "offline_fft") state.complete = state.written_slots == fft_records;
    if(windowed_in_pl || o.ml_startup_samples || o.ml_fft_hann) {
        const auto started=std::chrono::steady_clock::now();
        preprocess_ml_fft(o,fft,windowed_in_pl);
        state.ml_preprocessing_applied=true;
        state.ml_preprocessing_backend=windowed_in_pl?"pl":"ps";
        state.ml_preprocessing_ms=std::chrono::duration<double,std::milli>(
            std::chrono::steady_clock::now()-started).count();
    }
    return fft;
}

#ifndef ICRAFT_AOA_HOST_ONLY
namespace {
struct ScopeExit {
    std::function<void()> action;
    ~ScopeExit() { try { action(); } catch (...) {} }
};
}
void capture_fft(const Options& o, const std::vector<std::uint8_t>& input,
                 std::vector<std::uint8_t>& output, CaptureState& state,
                 const std::function<bool()>& cancelled) {
    constexpr std::uint64_t reg_base = 0x400c0000;
    constexpr std::size_t output_offset = input_bytes + slot_bytes;
    constexpr std::size_t allocation_bytes = output_offset + fft_bytes + slot_bytes;
    if (input.size() != input_bytes || output.size() != fft_bytes + slot_bytes)
        throw std::invalid_argument("Invalid DMA buffer size");
    // [板端联调修正] 实测无互斥时采集/NPU并发触发 DMA status 0xA。
    // 必须先加锁再 Open，最后 Close 后释放；CPU 测角及网络不持锁。
    icraft_device::HardwareLock hardware_lock(cancelled);
    auto device = icraft::xrt::Device::Open("axi://zg330aiu?npu=0x40000000&dma=0x80000000");
    ScopeExit close_device{[&]() { icraft::xrt::Device::Close(device); }};
    auto fpai = device.cast<icraft::xrt::ZG330Device>();
    auto chunk = fpai.defaultMemRegion().malloc(allocation_bytes, true, slot_bytes);
    state.input_address = chunk->begin.addr();
    if (state.input_address % slot_bytes ||
        state.input_address > std::numeric_limits<std::uint32_t>::max() - (allocation_bytes - 1))
        throw std::runtime_error("DDR allocation exceeds aligned 32-bit DMA address range");
    state.output_address = state.input_address + output_offset;
    state.pl_version = fpai.defaultRegRegion().read(reg_base + 0xc0, false);
    validate_pl_fft_contract(o, state.pl_version);
    state.fft_preprocessing = state.pl_version == windowed_fft_pl_version ? "pl-hann64" : "raw";
    auto write_reg = [&](std::uint64_t offset, std::uint32_t value) {
        fpai.defaultRegRegion().write(reg_base + offset, value, false);
    };
    bool active = false;
    auto reset = [&]() {
        write_reg(0x1dc, 1); std::this_thread::sleep_for(std::chrono::milliseconds(2));
        write_reg(0x1dc, 0); active = false;
    };
    // Stop PL before freeing its DDR allocation on exception, timeout or cancellation.
    ScopeExit stop_dma{[&]() { if (active) reset(); }};
    reset(); std::this_thread::sleep_for(std::chrono::milliseconds(5));
    write_reg(0x18, 0); write_reg(0x1c, dds_word(o)); write_reg(0x18, 1);
    std::this_thread::sleep_for(std::chrono::milliseconds(1)); write_reg(0x18, 0);
    // [波束控制新增] 每轮复位都会清系数；四轮使用 Options 中同一份冻结权值。
    // 只在启动 DMA 前写，写入/回读异常由上层拒绝本批 PHA，不交付混合权值输入。
    if(o.beam.enabled) {
        auto read_reg=[&](std::uint64_t offset) {
            return static_cast<std::uint32_t>(fpai.defaultRegRegion().read(reg_base+offset,false));
        };
        write_beam_registers(o.beam,write_reg,read_reg,[] {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        });
        state.beam_registers_verified=true;
    }
    std::vector<std::uint8_t> guarded_input(input_bytes + slot_bytes, 0x3c);
    std::copy(input.begin(), input.end(), guarded_input.begin());
    chunk.write(0, reinterpret_cast<char*>(guarded_input.data()), guarded_input.size());
    chunk.write(output_offset, reinterpret_cast<char*>(output.data()), output.size());
    std::vector<std::uint8_t> check_input(guarded_input.size()), check_output(output.size());
    chunk.read(reinterpret_cast<char*>(check_input.data()), 0, check_input.size());
    chunk.read(reinterpret_cast<char*>(check_output.data()), output_offset, check_output.size());
    if (check_input != guarded_input || check_output != output)
        throw std::runtime_error("DDR input/sentinel readback mismatch");
    if (cancelled && cancelled()) throw std::runtime_error("Capture cancelled before DMA start");
    write_reg(0x04, static_cast<std::uint32_t>(state.input_address));
    write_reg(0x08, static_cast<std::uint32_t>(adc_samples));
    write_reg(0x0c, static_cast<std::uint32_t>(state.output_address));
    write_reg(0x10, static_cast<std::uint32_t>(fft_records));
    active = true; write_reg(0x00, 1);
    auto read_output = [&]() {
        chunk.read(reinterpret_cast<char*>(output.data()), output_offset, output.size());
        state.written_slots = 0;
        for (std::size_t r = 0; r < fft_records; ++r)
            state.written_slots += slot_valid(output.data() + r * slot_bytes);
        state.guard_checked = true;
        state.guard_ok = std::all_of(output.begin()+fft_bytes, output.end(), [](auto v) { return v == 0xa5; });
    };
    const auto deadline = std::chrono::steady_clock::now() +
        std::chrono::duration<double>(o.timeout);
    while (std::chrono::steady_clock::now() < deadline && !(cancelled && cancelled())) {
        state.ready = fpai.defaultRegRegion().read(reg_base + 0x80, false) != 0;
        if (state.ready) {
            read_output();
            if (!state.guard_ok) break;
            if (state.written_slots == fft_records) { state.complete = true; break; }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    if (state.complete) active = false;
    else {
        reset(); std::this_thread::sleep_for(std::chrono::milliseconds(1)); read_output();
        std::ostringstream reason;
        reason << "Capture incomplete: ready=" << state.ready << ", slots=" << state.written_slots
               << '/' << fft_records << ", guard_ok=" << state.guard_ok;
        throw std::runtime_error(reason.str());
    }
}
void restore_beam(const BeamWeights& weights,const std::function<bool()>& cancelled) {
    constexpr std::uint64_t base=0x400c0000;
    icraft_device::HardwareLock lock(cancelled);
    auto device=icraft::xrt::Device::Open("axi://zg330aiu?npu=0x40000000&dma=0x80000000");
    ScopeExit close{[&](){icraft::xrt::Device::Close(device);}};
    auto fpai=device.cast<icraft::xrt::ZG330Device>();
    write_beam_registers(weights,[&](auto offset,auto value){fpai.defaultRegRegion().write(base+offset,value,false);},
        [&](auto offset){return static_cast<std::uint32_t>(fpai.defaultRegRegion().read(base+offset,false));},
        []{std::this_thread::sleep_for(std::chrono::milliseconds(1));});
}
#else
void restore_beam(const BeamWeights&,const std::function<bool()>&) {
    throw std::runtime_error("Host-only build: no beam registers");
}
void capture_fft(const Options&, const std::vector<std::uint8_t>&,
                 std::vector<std::uint8_t>&, CaptureState&, const std::function<bool()>&) {
    throw std::runtime_error("Host-only build: use --fft-raw or --dry-run");
}
#endif
} // namespace aoa_app
