#pragma once
#include "app.hpp"

namespace aoa_app {
// Both online modes collect one four-frame batch. Explicit offline files stay explicit.
inline void schedule_capture_rounds(Options& o) {
    if(!o.dry_run && o.adc_sources.empty() && o.fft_sources.empty())
        o.capture_rounds=npu_rounds;
}
} // namespace aoa_app
