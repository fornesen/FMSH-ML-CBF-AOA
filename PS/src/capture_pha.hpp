#pragma once
#include "app.hpp"
#include <stdexcept>

namespace aoa_app {
inline std::vector<std::uint8_t> pha_frame(const std::vector<std::uint8_t>& slots,
                                           const CaptureState& round) {
    if(round.mode!="hardware" || !round.complete || slots.size()<fft_bytes ||
       round.pha_written_samples!=pha_samples || round.pha_format_errors)
        throw std::runtime_error("PHA requires a complete paired hardware capture");
    std::vector<std::uint8_t> frame;frame.reserve(pha_bytes);
    for(std::size_t sample=0;sample<pha_samples;++sample) {
        const auto* begin=slots.data()+sample*slot_bytes+pha_offset;
        frame.insert(frame.end(),begin,begin+pha_sample_bytes);
    }
    return frame;
}
} // namespace aoa_app
