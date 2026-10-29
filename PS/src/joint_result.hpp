#pragma once
// [六天线测角新增] 遥测与落盘共享同一结果，不在上位机重算测角。
#include "app.hpp"
#include "console/console_transport.hpp"
#include <algorithm>
#include <cmath>
namespace aoa_app {
inline double joint_energy_share(const GroupEstimate& g,std::size_t p) {
    if(p>=g.amplitude_rms.size()) return 0;
    double peak=0;for(auto a:g.amplitude_rms) peak=std::max(peak,a);
    if(!(peak>0)) return 0;
    double total=0;for(auto a:g.amplitude_rms) total+=std::pow(a/peak,2);
    return std::pow(g.amplitude_rms[p]/peak,2)/total;
}
inline bool joint_eligible(const Options& o,const Analysis& a) {
    const auto& g=a.joint;
    if(a.v12.enabled) return a.complete && a.status=="ok" && a.v12.valid && g.has_best;
    if(a.joint_raw_validated && (!std::isfinite(a.joint_raw_quality.raw_residual) ||
       !std::isfinite(a.joint_raw_quality.latest_residual) ||
       a.joint_raw_quality.raw_residual>o.max_bearing_residual ||
       a.joint_raw_quality.latest_residual>o.max_bearing_residual)) return false;
    if(a.selection.quality_cascade) {
        const auto& s=a.selection;
        if(!s.accepted || s.fallback || s.model_paths<1 || s.model_paths>3 || s.models.size()!=3) return false;
        const auto& quality=s.models[s.model_paths-1];
        if(!quality.passed || quality.raw_residual>o.max_bearing_residual ||
           quality.latest_residual>o.max_bearing_residual) return false;
    }
    return a.complete && a.status=="ok" && g.has_best && !g.ambiguous &&
        (!a.selection.enabled || a.selection.accepted) &&
        g.energy>0 && g.residual/g.energy<=o.max_bearing_residual;
}
inline aoa_console::Json joint_packet(const Options& o,const Analysis& a) {
    using aoa_console::Json;
    const auto& g=a.joint;
    std::array<double,2> centre{};
    for(const auto& ant:o.antenna) {centre[0]+=ant.x/6;centre[1]+=ant.y/6;}
    const int paths=a.selection.enabled?a.selection.model_paths:o.joint_paths;
    Json p={{"model","far_field_ml"},{"path_count",paths},{"channels",{0,1,2,3,4,5}},
        {"reference_xy",centre},{"has_best",g.has_best},{"ambiguous",g.ambiguous},
        {"residual_ratio",g.has_best && g.energy>0?Json(g.residual/g.energy):Json(nullptr)},
        {"condition",g.steering_condition},{"condition_definition","Frobenius norm(A)*norm(pinv(A))"},
        {"paths",Json::array()},{"profile",{{"angles_deg",a.joint_profile_angles},
            {"relative_db",a.joint_profile_db},{"definition","profile likelihood (other angles optimized)"},
            {"scale_definition","10log10(min J / profiled J); numerical floor J/E=1e-12; clamp -80..0"}}}};
    if(a.joint_raw_validated) p["raw_validation"]={
        {"available",true},{"passed",a.joint_raw_quality.raw_residual<=o.max_bearing_residual &&
            a.joint_raw_quality.latest_residual<=o.max_bearing_residual},
        {"raw_residual_ratio",a.joint_raw_quality.raw_residual},
        {"latest_residual_ratio",a.joint_raw_quality.latest_residual},
        {"basis","original_snapshots_fixed_angles"}};
    if(a.selection.enabled) {
        const auto& s=a.selection;
        p["selection"]={{"method","single_dual_triple_cascade"},{"accepted",s.accepted},
            {"stages",s.stages},
            {"status",s.status},{"path_index",s.accepted?Json(s.path_index):Json(nullptr)},
            {"single_angle_deg",s.single_angle},{"range_deg",{s.lower,s.upper}},
            {"half_width_deg",s.half_width},{"dual_angles_deg",s.dual_angles},
            {"dual_amplitudes",s.dual_amplitudes},{"dual_tolerance_deg",s.dual_tolerance},
            {"amplitude_ratio",s.amplitude_ratio},{"residual_budget",s.residual_budget},
            {"global_residual_ratio",s.global_residual},{"residual_gap_ratio",s.residual_gap},
            {"dual_distance_deg",s.accepted && paths==3?Json(s.dual_distance):Json(nullptr)}};
        p["profile"]["definition"]="selected-model profile likelihood; score grid is same-order full search";
        if(s.quality_cascade) {
            auto& decision=p["selection"];
            decision["method"]="quality_cascade_energy_fallback";
            decision["quality_profile"]="raw_batch_residual_amplitude_ambiguity";
            decision["fallback"]=s.fallback;decision["has_candidate"]=s.path_index>=0;
            decision["path_index"]=s.path_index>=0?Json(s.path_index):Json(nullptr);
            decision["dual_distance_deg"]=nullptr;
            decision["selected_score"]=s.selected_score;
            decision["contribution_gate"]="numerical_positive_only; not statistically calibrated";
            decision["models"]=Json::array();decision["candidates"]=Json::array();
            for(const auto& q:s.models) decision["models"].push_back({
                {"path_count",q.paths},{"reason",q.reason},{"passed",q.passed},
                {"raw_residual_ratio",q.raw_residual},{"latest_residual_ratio",q.latest_residual},
                {"incremental_contribution",q.contribution},{"angles_deg",q.angles},{"amplitudes",q.amplitudes}});
            for(const auto& c:s.candidates) decision["candidates"].push_back({
                {"model_paths",c.model_paths},{"path_index",c.path_index},{"angle_deg",c.angle},
                {"score",c.score},{"support_models",c.support_models},{"support_paths",c.support_paths}});
            p["profile"]["definition"]="fused observation profile; quality is checked on original snapshots";
        }
    }
    if(a.v12.enabled) {
        p["selection"]["method"]="original_ml_top30_softmatch_v1.2";
        p["selection"]["accepted"]=a.v12.valid && a.selection.accepted;
        p["selection"]["path_index"]=a.v12.valid?Json(a.selection.path_index):Json(nullptr);
        p["selection"]["status"]=a.v12.valid?a.selection.status:"no_candidate";
        p["selection"]["selected_score"]=a.v12.score;
        p["selection"]["main_match"]=a.v12.main_match;
        p["profile"]["definition"]="selected-order original exhaustive ML profile; board computed";
    }
    for(std::size_t i=0;g.has_best && i<g.angle.size();++i) p["paths"].push_back({
        {"id",i},{"angle_deg",g.angle[i]},{"amplitude_rms",g.amplitude_rms[i]},
        {"relative_power_db",relative_path_power_db(g,i)},{"energy_share",joint_energy_share(g,i)},
        {"active",g.active[i]}});
    // 只传不重复的角度组合。161 点、三路径时约 0.91 MB，保持原 1 MiB 报文上限。
    if(o.processing_mode!="position" && !a.joint_score.empty()) p["score_grid"]={
        {"dimensions",paths},{"order","ascending_lexicographic"},
        {"score_u8",aoa_console::base64(a.joint_score)},{"invalid_value",255},
        {"scale",254},{"definition","1-J/E; display quantization only"}};
    return p;
}
}
