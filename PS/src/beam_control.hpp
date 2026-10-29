#pragma once
// [波束控制新增] PS 计算权值；网络线程只收命令，所有状态更新在四轮批次边界。
#include "app.hpp"
#include "beam_weights.hpp"
#include "beam_registers.hpp"
#include "pl_contract.hpp"
#include "processing_control.hpp"
#include "observation.hpp"
#include "archive_control.hpp"
#include "joint_result.hpp"
#include "console/console_transport.hpp"
#include <algorithm>
#include <cmath>
#include <deque>
#include <cstdlib>
#include <stdexcept>

namespace aoa_app {
// [自动波束新增] 与上位机相同的组内幅度平方份额；保留无效组合的能量，
// 只在通过原 localize 几何/拟合门限的组合中取最大值。不是校准的直达概率。
struct BeamChoice {
    Localization fix;
    std::array<int,2> paths{{0,0}};
    double energy_share=-1;
};
inline BeamChoice strongest_beam_candidate(const Options& o,const Analysis& a) {
    BeamChoice best;
    if(!a.complete || a.status!="ok" || a.groups.size()!=2) return best;
    std::array<const GroupEstimate*,2> groups{};
    for(const auto& g:a.groups) {
        if(g.id<0 || g.id>1 || groups[g.id]) return best;
        groups[g.id]=&g;
    }
    std::array<std::array<double,2>,2> shares{};
    for(int g=0;g<2;++g) {
        if(!groups[g] || groups[g]->angle.size()!=2 || groups[g]->active.size()!=2 ||
           groups[g]->amplitude_rms.size()!=2) return best;
        const auto& amp=groups[g]->amplitude_rms;
        if(!std::isfinite(amp[0]) || !std::isfinite(amp[1]) || amp[0]<0 || amp[1]<0) return best;
        const double peak=std::max(amp[0],amp[1]);
        if(!(peak>0)) return best;
        const double p0=std::pow(amp[0]/peak,2),p1=std::pow(amp[1]/peak,2);
        shares[g]={{p0/(p0+p1),p1/(p0+p1)}};
    }
    Options one=o;one.paths=1;
    for(int p=0;p<2;++p) for(int q=0;q<2;++q) {
        std::vector<GroupEstimate> pair{*groups[0],*groups[1]};
        for(int g=0;g<2;++g) {
            const int index=g?q:p;
            pair[g].angle={groups[g]->angle[index]};
            pair[g].active={groups[g]->active[index]};
        }
        const auto fix=localize(one,pair);
        const double score=shares[0][p]*shares[1][q];
        if(fix.valid && fix.y>=0 && score>best.energy_share) best={fix,{{p,q}},score};
    }
    return best;
}

// Automatic position mode consumes the board-selected four-intersection result.
inline BeamChoice automatic_position_candidate(const Options& o,const Analysis& a) {
    if(!a.v12.enabled) return strongest_beam_candidate(o,a);
    BeamChoice chosen;
    if(!a.complete || a.status!="ok" || !a.localization.valid || a.localization.y<0)
        return chosen;
    for(const auto& candidate:a.v12.intersections)
        if(candidate.id==a.v12.selected_id && candidate.fix.valid) {
            chosen.fix=a.localization;chosen.paths=candidate.paths;
            chosen.energy_share=candidate.energy_share;return chosen;
        }
    return chosen;
}

// Automatic selection is independent for each batch; no association or hysteresis.
inline int strongest_joint_path(const GroupEstimate& group) {
    int index=-1;
    double amplitude=0;
    for(std::size_t i=0;i<group.angle.size();++i) {
        if(i>=group.active.size() || !group.active[i] || i>=group.amplitude_rms.size()) continue;
        const auto value=group.amplitude_rms[i];
        if(std::isfinite(value) && value>amplitude) {amplitude=value;index=static_cast<int>(i);}
    }
    return index;
}

inline int automatic_joint_path(const Analysis& analysis) {
    const auto& selection=analysis.selection;
    if(selection.enabled) return selection.accepted?selection.path_index:-1;
    return strongest_joint_path(analysis.joint);
}

class BeamController {
    using Json=aoa_console::Json;
    bool available_,tracking_=false,verified_=false;
    BeamWeights desired_;
    std::array<int,2> paths_{{0,0}};
    std::array<double,2> bearings_{};
    Analysis last_;
    ProcessingControl processing_;
    ObservationControl observation_;
    ArchiveControl archive_;
    int joint_path_=0;
    bool auto_start_requested_=false;
    // [六天线测角新增] 角度模式用已有平面波权值入口，绝不伪造 XY 焦点。
    BeamWeights track_joint(const Options& o,bool strongest,bool starting=false) {
        if(!joint_eligible(o,last_) ||
           std::chrono::steady_clock::now()-last_time_>std::chrono::seconds(acquisition_mode_=="identify"?30:6))
            throw std::runtime_error("No fresh qualified joint ML estimate");
        const auto& g=last_.joint;
        int index=joint_path_;
        if(strongest) {
            index=automatic_joint_path(last_);
        } else if(!starting) {
            double first=1e9,second=1e9;
            for(std::size_t i=0;i<g.angle.size();++i) {
                const double delta=std::abs(g.angle[i]-desired_.angle_deg);
                if(delta<first) {second=first;first=delta;index=int(i);}
                else second=std::min(second,delta);
            }
            if(first>10 || second-first<1) throw std::runtime_error("Joint path association lost");
        }
        if(index<0 || std::size_t(index)>=g.angle.size() || !g.active[index])
            throw std::runtime_error("Invalid/inactive joint path");
        auto w=beam_weights(o,"manual",g.angle[index]);
        w.mode=strongest?"auto":"follow";joint_path_=index;
        target_={{"kind","angle"},{"angle_deg",g.angle[index]},{"path_id",index},
            {"source_run_id",last_run_},{"energy_share",joint_energy_share(g,index)}};
        return w;
    }
    BeamWeights track_scan(const Options& o) {
        const auto& s=last_.beam_scan;
        if(!last_.complete || !s.valid || last_.status!="ok" ||
           std::chrono::steady_clock::now()-last_time_>std::chrono::seconds(acquisition_mode_=="identify"?30:6))
            throw std::runtime_error("No fresh qualified beam scan");
        if(s.verification_enabled && s.verification_status!="matched")
            throw std::runtime_error("Beam scan PL verification unavailable");
        if(last_.v12.enabled) {
            if(!last_.v12.valid) throw std::runtime_error("Current V1.2 joint selection is unavailable");
            const double angle=last_.v12.angle_deg;
            auto w=beam_weights(o,"manual",angle);w.mode="auto";
            target_={{"kind","angle"},{"angle_deg",angle},{"path_id",last_.selection.path_index},
                {"source_method","original_ml_top30_softmatch_v1.2"},
                {"source_run_id",last_run_},{"energy_share",nullptr}};
            return w;
        }
        if(s.fusion.enabled && !s.fusion.valid)
            throw std::runtime_error("Current scan fusion is unavailable");
        const double angle=s.fusion.enabled?s.fusion.angle_deg:s.peak_angle_deg;
        auto w=beam_weights(o,"manual",angle);w.mode="auto";
        target_={{"kind","angle"},{"angle_deg",angle},{"path_id",0},
            {"source_method",s.fusion.enabled?"scan_ml_sine_average":"beam_power_scan"},
            {"source_run_id",last_run_},{"energy_share",nullptr}};
        return w;
    }
    // [自动波束新增] 显示权值采用哪一批测向，不能拿当前批次的 C 编号冒充目标身份。
    Json target_=nullptr;
    std::string last_run_;
    // [双模式新增] 与波束控制共用有界命令通道，采集模式回执独立，不改写波束状态。
    std::string acquisition_mode_="identify",acquisition_applied_,acquisition_id_,acquisition_status_="idle",acquisition_error_;
    bool acquisition_initialized_=false,acquisition_override_=false;
    std::chrono::steady_clock::time_point last_time_{};
    std::string status_="idle",error_,command_id_,applied_run_;
    // 命令结果保留到下一条命令，不能被后续自动跟踪状态覆盖成“成功”。
    std::string command_status_="idle",command_error_;
    std::uint64_t disconnect_epoch_=0;
    std::deque<std::string> seen_;
    std::uint32_t version_=0;
    static double number(const Json& p,const char* name) {
        const auto& v=p.at(name);
        if(!v.is_number()) throw std::invalid_argument("Expected numeric beam argument");
        const auto d=v.get<double>();
        if(!std::isfinite(d)) throw std::invalid_argument("Nonfinite beam argument");
        return d;
    }
    BeamWeights track(const Options& o,bool starting) {
        if(o.processing_mode=="scan6") throw std::runtime_error("Scan follow is unavailable; use auto or manual");
        if(o.processing_mode=="angle6") return track_joint(o,false,starting);
        if(!last_.complete || last_.status!="ok" ||
           std::chrono::steady_clock::now()-last_time_>std::chrono::seconds(acquisition_mode_=="identify"?30:6))
            throw std::runtime_error("No fresh direction estimate");
        Options one=o;one.paths=1;
        std::vector<GroupEstimate> groups=last_.groups;
        if(groups.size()!=2) throw std::runtime_error("Missing bearing group");
        std::array<double,2> next=bearings_;
        for(auto& g:groups) {
            if(g.id<0 || g.id>1) throw std::runtime_error("Invalid group");
            int index=paths_[g.id];
            if(!starting) {
                // 最近角度关联，遇到跳变/歧义即暂停，不按能量自动换目标。
                double best=1e9,second=1e9;
                for(std::size_t i=0;i<g.angle.size();++i) {
                    const auto delta=std::abs(g.angle[i]-bearings_[g.id]);
                    if(delta<best) {second=best;best=delta;index=static_cast<int>(i);}
                    else second=std::min(second,delta);
                }
                if(second-best<1) throw std::runtime_error("Ambiguous path association");
            }
            if(index<0 || std::size_t(index)>=g.angle.size() || std::size_t(index)>=g.active.size() ||
               std::abs(g.angle[index]-bearings_[g.id])>10)
                throw std::runtime_error("Candidate changed or moved over 10 degrees");
            next[g.id]=g.angle[index];paths_[g.id]=index;
            g.angle={g.angle[index]};g.active={g.active[index]};
        }
        auto fix=localize(one,groups);
        if(!fix.valid || fix.y<0) throw std::runtime_error("Candidate rejected: "+fix.status);
        bearings_=next;
        double cx=0,cy=0;
        for(const auto& a:o.antenna) {cx+=a.x/6;cy+=a.y/6;}
        auto w=beam_weights(o,"follow",std::atan2(fix.x-cx,fix.y-cy)*180/pi,fix.x,fix.y);
        target_={{"x",fix.x},{"y",fix.y},{"source_run_id",last_run_},{"energy_share",nullptr}};
        return w;
    }
    BeamWeights track_strongest(const Options& o) {
        if(o.processing_mode=="scan6") return track_scan(o);
        if(o.processing_mode=="angle6") return track_joint(o,true);
        if(std::chrono::steady_clock::now()-last_time_>std::chrono::seconds(acquisition_mode_=="identify"?30:6))
            throw std::runtime_error("No fresh direction estimate");
        const auto best=automatic_position_candidate(o,last_);
        if(!best.fix.valid) throw std::runtime_error("No qualified candidate; holding previous phases");
        double cx=0,cy=0;
        for(const auto& a:o.antenna) {cx+=a.x/6;cy+=a.y/6;}
        auto w=beam_weights(o,"auto",std::atan2(best.fix.x-cx,best.fix.y-cy)*180/pi,best.fix.x,best.fix.y);
        paths_=best.paths;bearings_=best.fix.angle;
        target_={{"x",best.fix.x},{"y",best.fix.y},{"source_run_id",last_run_},{"energy_share",best.energy_share}};
        return w;
    }
public:
    explicit BeamController(bool available):available_(available) {
        const auto* value=std::getenv("AOA_BEAM_AUTO_START");
        auto_start_requested_=available && value && std::string(value)=="1";
    }
    Json state() const {
        // Older consoles compare these two fields directly, before enabling controls.
        Json p={{"available",available_},
            {"required_pl_version",supported_pl_version(version_)?version_:current_pl_version},{"pl_version",version_},
            {"supported_pl_versions",{legacy_pl_version,windowed_fft_pl_version}},
            {"mode",desired_.mode},{"tracking",tracking_},{"status",status_},{"error",error_},
            {"command_id",command_id_},{"applied_run_id",applied_run_},{"angle_deg",desired_.angle_deg},
            {"command_status",command_status_},{"command_error",command_error_},
            {"registers_verified",verified_},{"hardware_response_verified",false},
            {"phase_rad",desired_.phase_rad},{"paths",paths_}};
        p["auto_supported"]=true;p["target"]=target_;
        p["path_tracking"]={{"enabled",false},{"active",false},{"scope","none"},
            {"reason","disabled"},{"tracks",Json::array()},{"angle_smoothing",false}};
        p["auto_selection"]=last_.v12.position_energy?"triad_ml_energy":last_.v12.enabled?"original_ml_top30_softmatch_v1.2":!last_.beam_scan.angles_deg.empty()?"direct_beam_power_peak":last_.selection.quality_cascade?"quality_cascade_energy_fallback":
            last_.selection.enabled?"single_dual_triple_cascade":"current_batch_strongest";
        p["processing"]=processing_.state(available_);
        p["observation"]=observation_.state(available_);
        p["archive"]=archive_.state(available_);
        p["acquisition"]={{"available",available_},{"mode",acquisition_mode_},{"applied_mode",acquisition_applied_},
            {"command_id",acquisition_id_},{"command_status",acquisition_status_},{"error",acquisition_error_}};
        return p;
    }
    void prepare(Options& o,aoa_console::Publisher& publisher) {
        Json command; bool received=publisher.take_command(command);
        const bool archive_command=received && command.value("mode",std::string{})=="archive";
        archive_.prepare(o,command,archive_command,available_);
        if(archive_command)received=false;
        const bool observation_command=received && command.value("mode",std::string{})=="observation";
        observation_.prepare(o,command,observation_command,available_);
        if(observation_command)received=false;
        const bool processing_command=received && command.value("mode",std::string{})=="processing";
        if(processing_.prepare(o,command,processing_command,available_)) {
            // 模式变化后旧 XY/旧角度不可关联。保留 auto 意图，等新模式第一帧后再跟踪。
            const bool automatic=tracking_ && desired_.mode=="auto";
            desired_=beam_weights(o,"equal");tracking_=automatic;
            if(automatic) desired_.mode="auto";
            last_=Analysis{};target_=nullptr;verified_=false;
            joint_path_=0;paths_={{0,0}};bearings_={};applied_run_.clear();
            status_=automatic?"holding":"pending";error_.clear();
        }
        if(processing_command)received=false;
        if(!acquisition_initialized_) {acquisition_mode_=o.ipc_enabled?"identify":"locate";acquisition_initialized_=true;}
        if(received && command.value("mode",std::string{})=="acquisition") {
            try {
                const auto id=command.at("id").get<std::string>();
                if(id.empty()||id.size()>64) throw std::invalid_argument("Invalid acquisition command id");
                acquisition_id_=id;acquisition_status_="pending";acquisition_error_.clear();
                if(!available_||command.value("expired",false)||command.at("schema")!=1)
                    throw std::invalid_argument("Acquisition control unavailable/expired");
                if(std::find(seen_.begin(),seen_.end(),id)!=seen_.end())throw std::invalid_argument("Duplicate command id");
                const auto mode=command.at("acquisition_mode").get<std::string>();
                if(mode!="locate"&&mode!="identify")throw std::invalid_argument("Invalid acquisition mode");
                // [模式校验新增] 非模型采样率仍可定位；拒绝切换识别，保持当前采集模式。
                if(mode=="identify" && o.sample_rate!=100e6)
                    throw std::invalid_argument("Current v6 NPU requires ADC 100 MS/s");
                seen_.push_back(id);if(seen_.size()>32)seen_.pop_front();
                acquisition_mode_=mode;acquisition_override_=true;
            } catch(const std::exception& e) {acquisition_status_="rejected";acquisition_error_=e.what();}
            received=false;
        }
        if(acquisition_override_) {o.ipc_enabled=acquisition_mode_=="identify";o.npu_every=1;}
        const auto epoch=publisher.disconnect_epoch();
        // 短暂断线即使在计算期间已经重连，也必须暂停；需用户重新选择跟踪。
        if((!publisher.connected()||epoch!=disconnect_epoch_)&&tracking_) {
            tracking_=false;status_="paused";error_="PC disconnected";
            target_=nullptr;
        }
        disconnect_epoch_=epoch;
        // Explicit deployment setting, consumed once after PL and control are ready.
        // A later disconnect still requires the normal user resume command.
        if(!received && auto_start_requested_ && publisher.connected() && supported_pl_version(version_) &&
           (o.processing_mode=="angle6" || o.processing_mode=="scan6")) {
            command={{"schema",1},{"mode","auto"},{"id","startup-auto-tracking"}};
            received=true;auto_start_requested_=false;
        }
        if(received) {
            const int old_joint_path=joint_path_;
            const auto old_paths=paths_;const auto old_bearings=bearings_;const auto old_target=target_;
            // 命令在改变权值前完整校验；非法命令不撤销已设置权值。
            try {
                const auto id=command.at("id").get<std::string>();
                if(id.empty() || id.size()>64) throw std::invalid_argument("Invalid command id");
                if(std::find(seen_.begin(),seen_.end(),id)!=seen_.end()) throw std::invalid_argument("Duplicate command id");
                seen_.push_back(id);if(seen_.size()>32) seen_.pop_front();
                command_id_=id;
                command_status_="pending";command_error_.clear();
                if(!available_ || command.value("expired",false)) throw std::invalid_argument("Control unavailable/expired");
                if(command.at("schema")!=1) throw std::invalid_argument("Control schema");
                const auto mode=command.at("mode").get<std::string>();
                BeamWeights next=desired_;
                if(mode=="pause") {
                    tracking_=false;status_="paused";command_status_="paused";error_.clear();
                } else {
                    bool holding=false;
                    if(mode=="auto") {
                        // 无可靠候选仍接受自动模式；保持相位，后续有效批次自动恢复。
                        try {next=track_strongest(o);}
                        catch(const std::exception& e) {
                            if(!next.enabled) next=beam_weights(o,"equal");
                            next.mode="auto";holding=true;error_=e.what();
                        }
                    } else if(mode=="manual") {
                        const double angle=number(command,"angle_deg");
                        const double end=o.angle_start+(o.angle_count-1)*o.angle_step;
                        if(angle<o.angle_start || angle>end) throw std::invalid_argument("Angle outside search coverage");
                        next=beam_weights(o,mode,angle);
                    } else if(mode=="equal") next=beam_weights(o,mode);
                    else if(mode=="follow" && o.processing_mode=="angle6") {
                        if(command.at("run_id")!=last_run_ || !command.at("path_index").is_number_integer())
                            throw std::invalid_argument("Joint batch/path changed");
                        const int index=command.at("path_index").get<int>();
                        if(index<0 || std::size_t(index)>=last_.joint.angle.size() ||
                           std::abs(number(command,"angle_deg")-last_.joint.angle[index])>1e-8)
                            throw std::invalid_argument("Joint angle changed");
                        joint_path_=index;next=track_joint(o,false,true);
                    } else if(mode=="follow" && o.processing_mode=="scan6") {
                        throw std::invalid_argument("Scan follow is unavailable; use auto or manual");
                    } else if(mode=="follow") {
                        auto p=command.at("paths"),angles=command.at("bearing_deg");
                        if(!p.is_array()||p.size()!=2||!angles.is_array()||angles.size()!=2)
                            throw std::invalid_argument("Expected two paths/bearings");
                        for(int i=0;i<2;++i) {
                            if(!p[i].is_number_integer() || p[i].get<int>()<0 || p[i].get<int>()>1 ||
                               !angles[i].is_number() || !std::isfinite(angles[i].get<double>()))
                                throw std::invalid_argument("Invalid selected path");
                            paths_[i]=p[i].get<int>();bearings_[i]=angles[i].get<double>();
                        }
                        next=track(o,true);
                    } else throw std::invalid_argument("Unknown mode");
                    next.command_id=command_id_;desired_=next;
                    tracking_=mode=="follow" || mode=="auto";
                    status_=holding?"holding":"pending";verified_=false;
                    if(!holding) error_.clear();
                    if(mode=="equal" || mode=="manual") {target_=nullptr;}
                }
            } catch(const std::exception& e) {
                joint_path_=old_joint_path;
                paths_=old_paths;bearings_=old_bearings;target_=old_target;status_="rejected";error_=e.what();
                command_status_="rejected";command_error_=e.what();
            }
        } else if(tracking_) {
            const auto old_paths=paths_;const auto old_bearings=bearings_;
            try {
                auto next=desired_.mode=="auto"?track_strongest(o):track(o,false);
                next.command_id=desired_.command_id;desired_=next;
                status_="pending";verified_=false;error_.clear();
            } catch(const std::exception& e) {
                paths_=old_paths;bearings_=old_bearings;
                if(desired_.mode=="auto") status_="holding";
                else {tracking_=false;status_="paused";}
                error_=e.what();
            }
        }
        o.beam=desired_; publisher.control_status(state());
    }
    void finish(const Options& o,const CaptureBatch& batch,const CaptureState& capture,const Analysis& a,
                aoa_console::Publisher& publisher) {
        last_=a;last_run_=batch.run_id;last_time_=std::chrono::steady_clock::now();
        processing_.finish(o,a,capture);
        observation_.finish();
        // ADC 接收在读 PL 之前失败时不把上次已知版本改成 0；写入前仍重读版本。
        if(capture.pl_version) version_=capture.pl_version;
        if(batch.completed==o.capture_rounds && capture.complete) {
            acquisition_applied_=acquisition_mode_;
            if(acquisition_status_=="pending")acquisition_status_="applied";
        } else if(acquisition_status_=="pending") {
            acquisition_status_="error";acquisition_error_=capture.error.empty()?"Incomplete capture":capture.error;
        }
        if(o.beam.enabled) {
            verified_=batch.completed==o.capture_rounds && batch.rounds.size()==o.capture_rounds &&
                std::all_of(batch.rounds.begin(),batch.rounds.end(),[](const auto& r){return r.beam_registers_verified&&r.complete;});
            if(o.processing_mode=="scan6" && a.beam_scan.verification_status=="restore_failed") verified_=false;
            if(!verified_) {
                tracking_=false;status_="error";error_=capture.error.empty()?"Incomplete beam capture":capture.error;
                if(command_status_=="pending") {command_status_="error";command_error_=error_;}
                // 部分写入失败后下一批回到原复位行为，不循环发送错误权值。
                desired_=BeamWeights{};target_=nullptr;
            } else {
                applied_run_=batch.run_id;
                if(status_=="pending") status_=tracking_?"tracking":"applied";
                if(command_status_=="pending") command_status_=status_=="holding"?"applied":status_;
            }
        }
        publisher.control_status(state());
    }
};
} // namespace aoa_app
