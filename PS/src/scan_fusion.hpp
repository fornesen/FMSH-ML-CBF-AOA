#pragma once
#include "app.hpp"
#include "console/console_transport.hpp"

namespace aoa_app {
// Retain disabled legacy fields in telemetry; V2 selects ML candidates without angle blending.
inline aoa_console::Json scan_fusion_packet(const ScanFusion& f) {
    const auto& m=f.ml_model;
    using aoa_console::Json;
    return {{"enabled",f.enabled},{"valid",f.valid},{"status",f.status},{"domain","sine"},
        {"requested_ml_weight",f.requested_ml_weight},{"effective_ml_weight",f.effective_ml_weight},
        {"scan_angle_deg",f.scan_angle_deg},{"ml_angle_deg",f.ml_angle_deg},{"angle_deg",f.angle_deg},
        {"ml_available",f.ml_available},{"ml_qualified",f.ml_qualified},{"ml_elapsed_ms",f.ml_elapsed_ms},
        {"ml_model",{{"angles_deg",m.angle},{"amplitudes",m.amplitude_rms},{"condition",m.steering_condition},
            {"fused_residual_ratio",m.has_best && m.energy>0?Json(m.residual/m.energy):Json(nullptr)},
            {"raw_residual_ratio",m.raw_validated?Json(m.raw_residual_ratio):Json(nullptr)},
            {"latest_residual_ratio",m.raw_validated?Json(m.latest_residual_ratio):Json(nullptr)},
            {"ambiguous",m.ambiguous},{"selection_accepted",f.ml_selection_accepted},
            {"selection_status",f.ml_selection_status},{"path_index",f.ml_path_index},{"model_paths",f.ml_model_paths}}}};
}
} // namespace aoa_app
