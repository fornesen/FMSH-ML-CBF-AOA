// Reuse one actual SDK HostBackend session to measure full held-out datasets.
// This tool never opens an AXI/NPU device and never claims hardware execution.
#include <icraft-xrt/core/session.h>
#include <icraft-xrt/dev/host_device.h>
#include <icraft-backends/hostbackend/backend.h>
#include <icraft-xir/serialize/json.h>
#include <nlohmann/json.hpp>
#include <array>
#include <cctype>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <vector>

using Json=nlohmann::json;
using namespace icraft::xir;
using namespace icraft::xrt;
int main(int argc,char** argv){
    try {
        if(argc!=5)throw std::runtime_error("Usage: validate_numeric MODEL.json MODEL.raw REQUESTS.jsonl OUTPUT.jsonl");
        if(std::filesystem::exists(argv[4]))throw std::runtime_error("Preserve existing validation output");
        auto net=Network::CreateFromJsonFile(argv[1]);net.lazyLoadParamsFromFile(argv[2]);
        if(net.inputs().size()!=3||net.outputs().size()!=1||net.outputs()[0].tensorType().numElements()!=6)
            throw std::runtime_error("Expected three feature inputs and six output logits");
        HostBackend::setThreadsNum(4);
        auto session=Session::Create<HostBackend>(net.view(0),{HostDevice::Default()});session.apply();
        std::ifstream requests(argv[3]);std::ofstream output(argv[4]);
        if(!requests||!output)throw std::runtime_error("Cannot open requests/output");
        std::string line;std::size_t count=0;
        while(std::getline(requests,line)){
            const auto request=Json::parse(line);std::vector<Tensor> inputs;
            std::array<bool,3> used{{false,false,false}};
            for(const auto& value:net.inputs()){
                const auto type=value.tensorType();
                const auto h=type.getDimByAxis('H',true),w=type.getDimByAxis('W',true);
                const int role=(h==64&&w==640)?0:(h==1&&w==640)?1:(h==1&&w==48)?2:-1;
                if(role<0||used[role]||!type->element_dtype.getStorageType().isFP32())throw std::runtime_error("Unexpected external input contract");
                used[role]=true;
                auto path=request.at("inputs").at(role).get<std::string>();
#ifdef _WIN32
                if(path.rfind("/mnt/",0)==0&&path.size()>7&&path[6]=='/')
                    path=std::string(1,static_cast<char>(std::toupper(static_cast<unsigned char>(path[5]))))+":"+path.substr(6);
#endif
                std::ifstream data(path,std::ios::binary|std::ios::ate);
                if(!data||data.tellg()!=static_cast<std::streamoff>(type.bytes()))throw std::runtime_error("Input byte count mismatch: "+path);
                std::vector<char> bytes(type.bytes());data.seekg(0);data.read(bytes.data(),bytes.size());
                if(!data)throw std::runtime_error("Short input read");
                Tensor tensor(type);tensor.mallocOn(HostDevice::MemRegion());tensor.write(0,bytes.data(),bytes.size());inputs.push_back(tensor);
            }
            const auto tensors=session.forward(inputs);
            if(tensors.size()!=1)throw std::runtime_error("Unexpected output count");
            std::ostringstream decoded(std::ios::out|std::ios::binary);tensors[0].dump(decoded,"SFB");
            const auto bytes=decoded.str();if(bytes.size()!=6*sizeof(float))throw std::runtime_error("Output must decode to six FP32 logits");
            std::array<float,6> logits{};std::memcpy(logits.data(),bytes.data(),bytes.size());
            for(const auto v:logits)if(!std::isfinite(v))throw std::runtime_error("Nonfinite logits");
            output<<Json({{"name",request.at("name")},{"logits",logits},{"npu_executed",false}}).dump()<<'\n';
            if(!output)throw std::runtime_error("Cannot save validation output");
            if(++count%50==0){output.flush();std::cout<<"VALIDATED "<<count<<std::endl;}
        }
        std::cout<<"COMPLETE records="<<count<<" backend=HostBackend npu_executed=false\n";return 0;
    }catch(const std::exception& e){std::cerr<<"ERROR: "<<e.what()<<'\n';return 1;}
}
