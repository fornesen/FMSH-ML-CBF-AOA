#pragma once
#include <cstdint>
#include <cstddef>

namespace aoa_app {
// Register 0xc0 identifies the FFT payload contract as well as beam registers.
constexpr std::uint32_t legacy_pl_version = 0x20261020;
constexpr std::uint32_t windowed_fft_pl_version = 0x00010001;
constexpr std::uint32_t current_pl_version = windowed_fft_pl_version;
constexpr std::size_t pl_fft_startup_samples = 64;
constexpr double pl_fft_window_sum = 2016.0;

inline bool supported_pl_version(std::uint32_t version) {
    return version == legacy_pl_version || version == windowed_fft_pl_version;
}
} // namespace aoa_app
