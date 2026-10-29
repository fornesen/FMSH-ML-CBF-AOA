#pragma once
// [上位机新增] 复用原模型预处理数据，仅显示第四个时间片的一路 PHA 合成信号。
#include "pha_npu.hpp"
#include "console/console_transport.hpp"
#include <cstdlib>
namespace pha_npu {
inline Json console_packet(const Options& o,const Batch& batch,const Features& features,const Json& result) {
    Json packet={{"type","npu"},{"run_id",batch.run_id},{"status",result.at("status")},
        {"backend",o.backend},{"npu_executed",result.at("npu_executed")},
        {"total_ms",result.at("total_ms")},{"source_raw_sha256",batch.raw_sha256},
        {"model_family",result.at("model_family")},{"scope","six_channel_sum_four_round_batch"}};
    if(result.contains("model_identity")) packet["model_identity"]=result["model_identity"];
    packet["amplitude_preprocessing"]=batch.amplitude_preprocessing;
    if(result.contains("classification")) packet["classification"]=result.at("classification");
    // [合成IQ观测新增] 新PS独立上报完整PHA图；可关闭旧裁剪预览，保留分类与模型计算。
    const auto* preview=std::getenv("AOA_LEGACY_SIGNAL_PREVIEW");
    if(preview && std::string(preview)=="0")return packet;
    // 原 TF 已按训练流程从 48 帧插值至 64 行；这里如实标注，不伪装成原始 STFT 帧。
    const std::size_t rows=64,cols=kColumns,round=kRounds-1;
    std::vector<std::uint8_t> tf(features[0].u8.begin()+round*rows*cols,features[0].u8.end());
    std::vector<std::uint8_t> spectrum(features[1].u8.begin()+round*cols,features[1].u8.end());
    Json wave=Json::array();
    // 连续 512 点；数值已包含固定增益及四轮统一补偿，再除 2^20。
    for(std::size_t i=0;i<std::min<std::size_t>(512,batch.iq[round].size());++i)
        wave.push_back({batch.iq[round][i].real(),batch.iq[round][i].imag()});
    packet["signal"]={{"channel","PHA"},{"round_index",round},{"sample_rate_hz",kFs},
        {"wave_start_sample",kDiscard},{"wave_iq",wave},{"iq_divisor",kIqScale},
        {"input_gain",batch.amplitude_preprocessing.at("effective_input_gain")},
        {"fixed_input_gain",o.input_gain},
        {"freq_min_hz",-2e6},{"freq_max_hz",2e6},{"columns",cols},{"rows",rows},
        {"db_min",-75},{"db_max",0},{"spectrum_u8",aoa_console::base64(spectrum)},
        {"tf_u8",aoa_console::base64(tf)},{"native_stft_frames",kFrames},{"window",kWindow},{"hop",kHop},
        {"continuous_between_batches",false},{"units","preprocess_relative_dB_not_dBm"}};
    return packet;
}
} // namespace pha_npu
