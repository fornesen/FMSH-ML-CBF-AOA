#pragma once
// [六天线测角新增] 只在批次边界切换处理分支，与原采集/识别调度分开。
#include "joint_ml.hpp"
#include "console/console_transport.hpp"
#include <deque>
namespace aoa_app {
class ProcessingControl {
    using Json=aoa_console::Json;
    std::string mode_="position",applied_,id_,status_="idle",error_;
    int paths_=3,applied_paths_=0;
    bool initialized_=false;
    std::deque<std::string> seen_;
public:
    // 返回真正发生的模式/阶数变化；拒绝非法命令时不改变已选模式。
    bool prepare(Options& o,const Json& command,bool received,bool available) {
        if(!initialized_) {mode_=o.processing_mode;paths_=o.joint_paths;initialized_=true;}
        bool changed=false;
        if(received) {
            try {
                id_=command.at("id").get<std::string>();status_="pending";error_.clear();
                if(id_.empty() || id_.size()>64 || !available || command.value("expired",false) || command.at("schema")!=1)
                    throw std::invalid_argument("Processing control unavailable/invalid/expired");
                if(std::find(seen_.begin(),seen_.end(),id_)!=seen_.end())
                    throw std::invalid_argument("Duplicate processing command");
                Options next=o;next.processing_mode=command.at("processing_mode").get<std::string>();
                if(!command.at("joint_paths").is_number_integer())
                    throw std::invalid_argument("Expected integer joint_paths");
                next.joint_paths=command.at("joint_paths").get<int>();
                validate_joint_options(next);
                seen_.push_back(id_);if(seen_.size()>32)seen_.pop_front();
                changed=mode_!=next.processing_mode || paths_!=next.joint_paths;
                mode_=next.processing_mode;paths_=next.joint_paths;
            } catch(const std::exception& e) {status_="rejected";error_=e.what();}
        }
        o.processing_mode=mode_;o.joint_paths=paths_;
        return changed;
    }
    void finish(const Options& o,const Analysis& a,const CaptureState& capture) {
        if(a.complete) {
            applied_=o.processing_mode;applied_paths_=o.joint_paths;
            if(status_=="pending")status_="applied";
        } else if(status_=="pending") {
            status_="error";error_=capture.error.empty()?"Processing incomplete":capture.error;
        }
    }
    Json state(bool available) const {
        return {{"supported_modes",{"position","angle6","scan6"}},{"available",available},{"mode",mode_},{"applied_mode",applied_},
            {"joint_paths",paths_},{"applied_joint_paths",applied_paths_},
            {"command_id",id_},{"command_status",status_},{"error",error_}};
    }
};
}
