#include "pha_npu.hpp"
#include <stdexcept>

#ifdef PHA_NPU_HOST_ONLY
namespace pha_npu {
Inference infer_icraft(const Options&,const Features&,const fs::path&) {
    throw std::runtime_error("This executable was built HOST_ONLY; build the Icraft target for host/NPU inference");
}
}
#else
#include "icraft_device_lock.hpp"
#include <memory>
#include <icraft-xrt/core/session.h>
#include <icraft-xrt/dev/host_device.h>
#include <icraft-backends/hostbackend/backend.h>
#include <icraft-backends/zg330backend/zg330backend.h>
#include <icraft-xir/ops/cast.h>
#include <icraft-xir/ops/hard_op.h>
#include <icraft-xir/serialize/json.h>
#include <cstring>
#include <sstream>
#include <fcntl.h>
#include <unistd.h>
#include <cstdlib>
#include <cerrno>
#include <cstdio>

namespace pha_npu {
namespace {
using namespace icraft::xir;
using namespace icraft::xrt;

Tensor cast_host(const Tensor& input,const TensorType& target) {
    // Let the matching SDK implement layout, padding, quantization and
    // dequantization, using the compiled network's actual TensorType.
    Network n("pha_io_cast",Framework::PYTORCH,"2.0.1",HostTarget::Init());
    Input start(input.dtype());n.addOp(start);
    Cast cast(start->outputs[0],target);n.addOp(cast);
    const auto& outputs=cast.inferResults();n.addOp(Output(outputs));
    auto s=Session::Create<HostBackend>(n.view(0),{HostDevice::Default()});s.apply();
    return s.forward({input}).at(0);
}
Json dtype_json(const TensorType& t) { return JsonPrinter().dump(t); }
class RuntimeScratch {
    fs::path path_;int previous_=-1;
public:
    explicit RuntimeScratch(bool enabled) {
        if(!enabled) return;
        // HostBackend creates hardopLog even with all application dumps disabled.
        // Keep those SDK-owned diagnostics on tmpfs, outside the capture directory.
        previous_=::open(".",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
        if(previous_<0) throw std::runtime_error("Cannot preserve SDK working directory");
        auto pattern=std::string("/dev/shm/icraft_npu_")+std::to_string(::getpid())+"_XXXXXX";
        std::vector<char> writable(pattern.begin(),pattern.end());writable.push_back(0);
        const auto created=::mkdtemp(writable.data());
        if(!created) {::close(previous_);previous_=-1;throw std::runtime_error("Cannot create SDK RAM scratch directory");}
        path_=created;
        if(::chdir(path_.c_str())) {std::error_code e;fs::remove(path_,e);::close(previous_);previous_=-1;throw std::runtime_error("Cannot enter SDK RAM scratch directory");}
    }
    ~RuntimeScratch() {
        if(previous_<0) return;
        // [板端编译修正] Ubuntu ARM64 marks fchdir warn_unused_result.
        // Destructors cannot throw; retain scratch and report a failed restore.
        const int restored=::fchdir(previous_);
        const int restore_errno=errno;
        ::close(previous_);
        if(restored!=0) {
            std::fprintf(stderr,"NPU scratch working-directory restore failed: errno=%d\n",restore_errno);
            return;
        }
        // Delete only the exact mkdtemp directory owned by this invocation.
        if(path_.parent_path()=="/dev/shm"&&path_.filename().string().rfind("icraft_npu_"+std::to_string(::getpid())+"_",0)==0) {
            std::error_code e;fs::remove_all(path_,e);
        }
    }
};
}

Inference infer_icraft(const Options& requested,const Features& f,const fs::path& requested_prefix) {
    using namespace icraft::xir;
    using namespace icraft::xrt;
    auto o=requested;o.model_json=fs::absolute(o.model_json);o.model_raw=fs::absolute(o.model_raw);
    if(!o.model_contract.empty()) o.model_contract=fs::absolute(o.model_contract);
    const auto prefix=fs::absolute(requested_prefix);
    RuntimeScratch scratch(!o.save_results);
    if(!fs::is_regular_file(o.model_json)||!fs::is_regular_file(o.model_raw))
        throw std::runtime_error("Compiled model JSON/RAW does not exist");
    const auto identity=resolve_model_identity(o);
    auto network=Network::CreateFromJsonFile(o.model_json);
    network.lazyLoadParamsFromFile(o.model_raw);
    if(network.inputs().size()!=3||network.outputs().size()!=1)
        throw std::runtime_error("Expected three feature inputs and one classification output");
    // External output is unpacked FP32 [1,6]. Reject old four-class models
    // before opening the device instead of silently dropping or inventing OOK.
    const auto output_type=network.outputs()[0].tensorType();
    if(output_type.numElements()!=kClassCount || !output_type->layout.has('C') ||
       output_type.getDimByAxis('C',true)!=static_cast<std::int64_t>(kClassCount))
        throw std::runtime_error("Expected external classification output [1,6]: FM/AM/CW/noise/OOK/PM");
    std::size_t hard_ops=0;
    for(const auto& op:network->ops) if(op.is<HardOp>()) ++hard_ops;
    if(o.backend=="npu" && !hard_ops)
        throw std::runtime_error("Network has no generated HardOp; use a generated ZG model, not parsed/optimized JSON");
    HostBackend::setThreadsNum(2);
    std::vector<Tensor> inputs;
    Json input_info=Json::array();std::array<bool,3> used{{false,false,false}};
    for(const auto& value:network.inputs()) {
        const auto target=value.tensorType();
        if(!target->layout.has('H')||!target->layout.has('W')||!target->layout.has('C'))
            throw std::runtime_error("Compiled input layout must identify H/W/C axes");
        if(target.getDimByAxis('C',true)!=kRounds)
            throw std::runtime_error("Compiled inputs must contain four temporal groups");
        const auto h=target.getDimByAxis('H',true),w=target.getDimByAxis('W',true);
        int role=-1;
        for(int k=0;k<3;++k) if(h==f[k].shape[2]&&w==f[k].shape[3]) role=k;
        if(role<0||used[role]) throw std::runtime_error("Compiled input shapes cannot map uniquely to tf/spectrum/sequence");
        used[role]=true;
        const auto& feature=f[role];
        TensorType source(FloatType::FP32(),{1,4,feature.shape[2],feature.shape[3]},Layout::NCHW());
        Tensor logical(source);logical.mallocOn(HostDevice::MemRegion());
        logical.write(0,reinterpret_cast<char*>(const_cast<float*>(feature.f32.data())),feature.f32.size()*sizeof(float));
        auto packed=cast_host(logical,target);
        const auto bytes=packed.dtype().bytes();std::vector<std::uint8_t> raw(bytes);
        packed.read(reinterpret_cast<char*>(raw.data()),0,bytes);
        const fs::path path=prefix.string()+"_"+feature.name+"_icraft_input.bin";
        if(o.save_results) write_bytes_atomic(path,raw.data(),raw.size());
        input_info.push_back({{"feature",feature.name},{"value_id",value->v_id},{"value_name",value->name},
            {"dtype",dtype_json(target)},{"physical_bytes",bytes},{"packed_file",fs::absolute(path).string()},
            {"packed_sha256",sha256(raw)}});
        inputs.push_back(packed);
    }
    struct DeviceLifetime {
        // [板端联调修正] 锁晚于 Device::Close 释放，覆盖 Session/DMA 生命周期。
        std::unique_ptr<icraft_device::HardwareLock> hardware_lock;
        Device device;bool opened=false;const Options& options;
        explicit DeviceLifetime(const Options& v):options(v){}
        ~DeviceLifetime() {
            if(opened) try {Device::Close(device);} catch(...) {}
            if(options.device_activity) options.device_activity(false);
        }
    } lifetime(o);
    Session session; // Destroy backends/tensors before Device::Close, including error paths.
    if(o.backend=="npu") {
        lifetime.hardware_lock=std::make_unique<icraft_device::HardwareLock>(o.cancelled);
        if(o.device_activity) o.device_activity(true);
        lifetime.device=Device::Open(o.device);lifetime.opened=true;
        session=Session::Create<zg330::ZG330Backend,HostBackend>(network.view(0),{lifetime.device,HostDevice::Default()});
    } else session=Session::Create<HostBackend>(network.view(0),{HostDevice::Default()});
    session.enableTimeProfile(true);session.apply();
    std::size_t zg330_bound=0,host_bound=0;
    for(const auto& binding:session.backendBindings()) {
        if(binding.second.is<zg330::ZG330Backend>()) ++zg330_bound;
        else ++host_bound;
    }
    if(o.backend=="npu" && !zg330_bound)
        throw std::runtime_error("No operation bound to ZG330; refusing to label a CPU-only run as NPU inference");
    const auto outputs=session.forward(inputs);
    if(outputs.size()!=1) throw std::runtime_error("Unexpected number of output tensors");
    const auto output=outputs[0].to(HostDevice::MemRegion());
    std::vector<std::uint8_t> raw(output.dtype().bytes());
    output.read(reinterpret_cast<char*>(raw.data()),0,raw.size());
    const fs::path raw_path=prefix.string()+"_"+o.backend+"_output.bin";
    if(o.save_results) write_bytes_atomic(raw_path,raw.data(),raw.size());
    // SFB is the SDK's software-layout, floating-point binary dump path.
    // Validate its length, rather than assuming physical INT8/FP16 bytes are FP32.
    std::ostringstream decoded(std::ios::out|std::ios::binary);output.dump(decoded,"SFB");
    const auto binary=decoded.str();
    if(binary.size()!=kClassCount*sizeof(float)) throw std::runtime_error("SFB output is not six FP32 logits; inspect compiled output contract");
    Inference result;result.logits.resize(kClassCount);std::memcpy(result.logits.data(),binary.data(),binary.size());
    result.details={{"sdk","Icraft 3.36.1"},{"model_json",fs::absolute(o.model_json).string()},
        {"model_raw",fs::absolute(o.model_raw).string()},
        {"model_json_sha256",identity.at("model_json_sha256")},{"model_raw_sha256",identity.at("model_raw_sha256")},
        {"model_identity",identity},
        {"generated_hard_ops",hard_ops},{"device_uri",o.backend=="npu"?o.device:"host"},
        {"zg330_bound_ops",zg330_bound},{"other_bound_ops",host_bound},
        {"input_conversion","Icraft HostBackend Cast FP32 NCHW -> compiled Value TensorType"},
        {"inputs",input_info},{"output_dtype",dtype_json(output.dtype())},
        {"raw_output_file",fs::absolute(raw_path).string()},
        {"output_conversion","Tensor.dump SFB; checked exactly six float32 logits"},
        {"session_time_ms",session.totalTime<std::chrono::milliseconds>()}};
    return result;
}
}
#endif
