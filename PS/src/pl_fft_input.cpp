#include "pl_fft_input.hpp"

#include <cmath>
#include <limits>

////////////////////////////////////////////////////////////////////////////////
// PL 数据预处理：只负责 39 位解码、帧边界检查、公共频点提取、校准和分组。
// 核对基准：Project/ADC_DSP 中 xfft XCI、axis_fft_result.v、axis_fft_frame_mux.v。
// FFT 为正向、4096 点、自然序、未缩放；不在 PS 再重复 FIR->FFT 的右移 16 位。
////////////////////////////////////////////////////////////////////////////////
AoaComplex pl_fft_decode80(const std::uint8_t data[PL_FFT_BYTES])
{
    const double nan = std::numeric_limits<double>::quiet_NaN();
    if (!data) {
        return AoaComplex(nan, nan);
    }

    double component[2];
    for (std::size_t part = 0; part < 2; ++part) {
        std::uint64_t bits = 0;
        for (std::size_t byte = 0; byte < 5; ++byte) {
            bits |= static_cast<std::uint64_t>(data[part * 5 + byte]) << (8 * byte);
        }
        bits &= (std::uint64_t(1) << 39) - 1;
        // 先转入足够宽的有符号数，再减 2^39；不依赖无符号溢出转换行为。
        const std::int64_t value = static_cast<std::int64_t>(bits)
                                - ((bits & (std::uint64_t(1) << 38)) ? (std::int64_t(1) << 39) : 0);
        component[part] = static_cast<double>(value);  // binary64 精确保存所有 39 位整数。
    }
    return AoaComplex(component[0], component[1]);
}

PlFftStatus pl_fft_reset(PlFftCollector& collector, std::size_t target_bin)
{
    collector = PlFftCollector{};
    if (target_bin >= PL_FFT_LENGTH) {
        collector.failed = true;
        return PlFftStatus::InvalidArgument;
    }
    collector.target_bin = target_bin;
    return PlFftStatus::Wait;
}

PlFftStatus pl_fft_push(PlFftCollector& collector, const std::uint8_t data[PL_FFT_BYTES],
                       std::uint8_t channel, bool last)
{
    collector.batch_ready = false;
    if (collector.failed) {
        return PlFftStatus::StreamError;
    }
    if (!data || collector.target_bin >= PL_FFT_LENGTH || collector.next_bin >= PL_FFT_LENGTH ||
        collector.next_channel >= PL_FFT_CHANNELS || channel != collector.next_channel ||
        last != (collector.next_bin == PL_FFT_LENGTH - 1)) {
        collector.failed = true;
        return PlFftStatus::StreamError;
    }
    if (collector.next_bin == collector.target_bin) {
        collector.selected[channel] = pl_fft_decode80(data);
    }
    if (++collector.next_bin == PL_FFT_LENGTH) {
        collector.next_bin = 0;
        if (++collector.next_channel == PL_FFT_CHANNELS) {
            collector.next_channel = 0;
            collector.batch_ready  = true;
            return PlFftStatus::BatchReady;
        }
    }
    return PlFftStatus::Wait;
}

AoaStatus pl_fft_make_group(const PlFftCollector& collector, const std::size_t channel[AOA_ANTENNAS],
                            const PlFftCalibration& calibration, AoaInput& input)
{
    if (!collector.batch_ready || collector.failed || !channel ||
        !std::isfinite(calibration.common_scale) || calibration.common_scale <= 0.0 ||
        !std::isfinite(calibration.rf_frequency_hz) || calibration.rf_frequency_hz <= 0.0) {
        return AoaStatus::InvalidArgument;
    }
    AoaInput prepared;
    prepared.rf_frequency_hz = calibration.rf_frequency_hz;
    for (std::size_t antenna = 0; antenna < AOA_ANTENNAS; ++antenna) {
        const std::size_t selected = channel[antenna];
        if (selected >= PL_FFT_CHANNELS) {
            return AoaStatus::InvalidArgument;
        }
        for (std::size_t previous = 0; previous < antenna; ++previous) {
            if (selected == channel[previous]) {
                return AoaStatus::InvalidArgument;
            }
        }
        const AoaComplex gain = calibration.correction[selected];
        if (!std::isfinite(gain.real()) || !std::isfinite(gain.imag()) || gain == AoaComplex(0, 0)) {
            return AoaStatus::InvalidArgument;
        }
        const AoaComplex value = collector.selected[selected] * gain * calibration.common_scale;
        if (!std::isfinite(value.real()) || !std::isfinite(value.imag())) {
            return AoaStatus::InvalidArgument;
        }
        prepared.fft_real[antenna] = value.real();
        prepared.fft_imag[antenna] = value.imag();
    }
    input = prepared;
    return AoaStatus::Ok;
}

double pl_fft_bin_hz(std::size_t bin, double sample_rate_hz)
{
    if (bin >= PL_FFT_LENGTH || !std::isfinite(sample_rate_hz) || sample_rate_hz <= 0.0) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    const int signed_bin = bin < PL_FFT_LENGTH / 2 ? static_cast<int>(bin)
                                                : static_cast<int>(bin) - static_cast<int>(PL_FFT_LENGTH);
    return signed_bin * (sample_rate_hz / PL_FFT_LENGTH);
}
