#ifndef PL_FFT_INPUT_HPP
#define PL_FFT_INPUT_HPP

#include "aoa_ml3.hpp"

constexpr std::size_t PL_FFT_CHANNELS = 6;
constexpr std::size_t PL_FFT_LENGTH   = 4096;
constexpr std::size_t PL_FFT_BYTES    = 10;

enum class PlFftStatus : int {
    Wait          =  0,
    BatchReady    =  1,
    InvalidArgument = -1,
    StreamError   = -2
};

struct PlFftCollector {
    std::size_t target_bin   = 0;
    std::size_t next_bin     = 0;
    std::size_t next_channel = 0;
    bool failed             = false;
    bool batch_ready        = false;
    AoaComplex selected[PL_FFT_CHANNELS] = {};
};

struct PlFftCalibration {
    // 这是接收通道的复校正系数（例如测得通道增益 h 后填 1/h），不是波束权重。
    AoaComplex correction[PL_FFT_CHANNELS] = {{1,0}, {1,0}, {1,0}, {1,0}, {1,0}, {1,0}};
    double common_scale    = 1.0;  // 原始 FFT 单位取 1；复数 FFT 归一化可取 1/4096。
    double rf_frequency_hz = 0.0;
};

// 逻辑字节序：data[0]=TDATA[7:0]，data[9]=TDATA[79:72]。
// I=有符号 TDATA[38:0]，Q=有符号 TDATA[78:40]，忽略填充位 39、79。
// 这不是 DDR/DMA 内存布局约定；TUSER、TLAST 必须由搬运层单独提供。
AoaComplex pl_fft_decode80(const std::uint8_t data[PL_FFT_BYTES]);

// 仅在已知 ch0/bin0 边界初始化。没有帧号时不能自动识别整批错配。
PlFftStatus pl_fft_reset(PlFftCollector& collector, std::size_t target_bin);

// 每个实际握手成功的 beat 调一次。依次接收 ch0 的 4096 点，再 ch1...ch5。
// 错帧后锁存错误，必须外层重新同步，再 reset；不可从中间位置直接 reset。
PlFftStatus pl_fft_push(PlFftCollector& collector, const std::uint8_t data[PL_FFT_BYTES],
                       std::uint8_t channel, bool last);

// 仅在 BatchReady 后、下一拍 push 前调用；通道顺序必须与本组坐标顺序一致。
// y[m] = common_scale * correction[channel[m]] * FFT[channel[m], target_bin]。
AoaStatus pl_fft_make_group(const PlFftCollector& collector, const std::size_t channel[AOA_ANTENNAS],
                            const PlFftCalibration& calibration, AoaInput& input);

// 自然序频点映射，bin>=2048 为负频率；输出为基带频率，不能代替实际 RF。
double pl_fft_bin_hz(std::size_t bin, double sample_rate_hz = 25e6);

#endif
