#pragma once
// Storage is opt-in per process and is adopted only between complete batches.
#include "app.hpp"
#include "console/console_transport.hpp"
#include <algorithm>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <stdexcept>

namespace aoa_app {
class ArchiveControl {
    using Json=aoa_console::Json;
    bool initialized_=false,enabled_=false;
    bool managed_=false;
    std::string path_,id_,status_="idle",error_;
    std::deque<std::string> seen_;
    Json pending_batches() const {
        if(path_.empty()) return nullptr;
        try {
            std::size_t count=0;
            const auto spool=std::filesystem::path(path_).parent_path()/"aoa";
            if(!std::filesystem::exists(spool)) return 0;
            const std::string suffix="_metadata.json";
            for(const auto& entry:std::filesystem::directory_iterator(spool)) {
                const auto name=entry.path().filename().string();
                if(name.rfind("aoa_",0)==0 && name.size()>=suffix.size() &&
                   name.compare(name.size()-suffix.size(),suffix.size(),suffix)==0) ++count;
            }
            return count;
        } catch(const std::filesystem::filesystem_error&) {return nullptr;}
    }
    void publish() const {
        if(path_.empty()) return;
        std::ofstream out(path_+".tmp");
        if(!out) throw std::runtime_error("Cannot write archive control state");
        out<<state(managed_).dump()<<'\n';out.flush();
        if(!out) throw std::runtime_error("Cannot flush archive control state");
        out.close();std::filesystem::rename(path_+".tmp",path_);
    }
public:
    ArchiveControl() {
        const auto* managed=std::getenv("AOA_ARCHIVE_SWITCH");
        managed_=managed && std::string(managed)=="1";
        const auto* path=std::getenv("AOA_ARCHIVE_CONTROL_PATH");
        if(managed_ && path) path_=path;
    }
    void prepare(Options& o,const Json& command,bool received,bool available) {
        if(!initialized_) {
            // Existing explicit offline --record-raw behavior remains compatible.
            enabled_=managed_?false:o.record_raw;initialized_=true;publish();
        }
        if(received) {
            const bool previous=enabled_;
            try {
                id_=command.at("id").get<std::string>();error_.clear();
                if(id_.empty() || id_.size()>64 || !available || !managed_ ||
                   command.value("expired",false) || command.at("schema")!=1)
                    throw std::invalid_argument("Archive control unavailable/invalid/expired");
                if(std::find(seen_.begin(),seen_.end(),id_)!=seen_.end())
                    throw std::invalid_argument("Duplicate archive command");
                if(!command.at("archive_enabled").is_boolean())
                    throw std::invalid_argument("Expected boolean archive switch");
                enabled_=command.at("archive_enabled").get<bool>();
                status_="applied";publish();
                seen_.push_back(id_);if(seen_.size()>32)seen_.pop_front();
            } catch(const std::exception& e) {
                enabled_=previous;status_="rejected";error_=e.what();
            }
        }
        if(managed_) {o.record_raw=enabled_;o.save_results=enabled_;}
    }
    Json state(bool available) const {
        return {{"available",available && managed_},{"enabled",enabled_},
                {"command_id",id_},{"command_status",status_},{"error",error_},
                {"pending_batches",pending_batches()},{"pending_scope","current_session"}};
    }
};
} // namespace aoa_app
