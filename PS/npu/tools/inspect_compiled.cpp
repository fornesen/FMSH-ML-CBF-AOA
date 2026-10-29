// Inspect deployment binding with a memory-only mock device. Never runs forward.
#include <icraft-xrt/core/session.h>
#include <icraft-xrt/dev/host_device.h>
#include <icraft-backends/hostbackend/backend.h>
#include <icraft-backends/zg330backend/zg330backend.h>
#include <icraft-xir/ops/hard_op.h>
#include <icraft-xir/serialize/json.h>
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include <set>

int main(int argc,char** argv) {
    try {
        using namespace icraft::xir;
        using namespace icraft::xrt;
        if(argc<4) throw std::runtime_error("Usage: inspect_compiled MODEL.json MODEL.raw REPORT.json [--eager] [--no-merge] [--precheck]");
        bool eager=false,no_merge=false,run_precheck=false;
        for(int i=4;i<argc;++i) {
            if(std::string(argv[i])=="--eager") eager=true;
            else if(std::string(argv[i])=="--no-merge") no_merge=true;
            else if(std::string(argv[i])=="--precheck") run_precheck=true;
            else throw std::runtime_error("Unknown inspection option");
        }
        auto n=Network::CreateFromJsonFile(argv[1]);
        if(eager)n.loadParamsFromFile(argv[2]);else n.lazyLoadParamsFromFile(argv[2]);
        if(n.inputs().size()!=3||n.outputs().size()!=1) throw std::runtime_error("Wrong input/output count");
        std::set<int64_t> original_ids,covered_ids;
        for(const auto& op:n->ops) if(op.is<HardOp>()) original_ids.insert(op->op_id);
        auto device=Device::Open("mock://zg330");
        auto zg_backend=zg330::ZG330Backend::Init();
        if(no_merge) zg_backend.disableMergeHardOp();
        auto session=Session::CreateByOrder(n.view(0),{zg_backend,HostBackend::Init()},{device,HostDevice::Default()});
        session.apply();
        std::size_t hard=0,bound=0,other=0;
        for(const auto& op:n->ops) if(op.is<HardOp>()) ++hard;
        for(const auto& b:session.backendBindings()) {
            if(b.second.is<zg330::ZG330Backend>()) ++bound;else ++other;
        }
        std::cout<<"Original HardOps="<<hard<<" ZG330 bindings="<<bound<<" other bindings="<<other<<'\n';
        if(!hard||!bound) throw std::runtime_error("No generated HardOp/ZG330 binding");
        bool uploaded_ok=true;
        std::string upload_error;
        for(const auto& b:session->backends) if(b.is<zg330::ZG330Backend>()) {
            auto zg=b.cast<zg330::ZG330Backend>();
            for(const auto& group:zg->speed_ops) {
                if(group->merge_from.empty()) covered_ids.insert(group->net_hardop->op_id);
                else covered_ids.insert(group->merge_from.begin(),group->merge_from.end());
            }
            if(run_precheck) try {uploaded_ok=zg.precheck()&&uploaded_ok;}
            catch(const std::exception& e) {uploaded_ok=false;upload_error=e.what();}
        }
        if(covered_ids!=original_ids) throw std::runtime_error("Merged ZG330 groups do not cover all original HardOps");
        nlohmann::json j={{"state","apply_checked"},{"device","mock://zg330"},
            {"device_is_memory_only_mock",true},{"forward_executed",false},{"actual_board_run",false},
            {"parameter_loading",eager?"eager":"lazy"},
            {"merge_hardops_enabled",!no_merge},
            {"generated_hard_ops",hard},{"zg330_bound_ops",bound},{"other_bound_ops",other},
            {"covered_original_hard_ops",covered_ids.size()},{"all_hardops_covered_by_zg330",true},
            {"mock_precheck_performed",run_precheck},
            {"mock_weight_instruction_readback_passed",run_precheck?nlohmann::json(uploaded_ok):nlohmann::json(nullptr)},
            {"mock_precheck_error",upload_error},
            {"inputs",nlohmann::json::array()},{"outputs",nlohmann::json::array()}};
        for(const auto& v:n.inputs()) j["inputs"].push_back(JsonPrinter().dump(v.tensorType()));
        for(const auto& v:n.outputs()) j["outputs"].push_back(JsonPrinter().dump(v.tensorType()));
        std::ofstream out(argv[3]);out<<j.dump(2)<<'\n';
        if(!out) throw std::runtime_error("Cannot save inspection report");
        std::cout<<"Checked "<<hard<<" HardOps, "<<bound<<" ZG330 bindings; no forward/board execution\n";
        if(run_precheck&&!uploaded_ok) {std::cerr<<"Explicit mock precheck failed; see report/log\n";return 1;}
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
